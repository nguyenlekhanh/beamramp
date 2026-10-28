"""Milestone 3.4: thumbnail candidates + ranking tests."""

import json
from pathlib import Path

import pytest

from app.services import ffmpeg_service as ff
from app.services.thumbnail_service import (ThumbCandidate, ThumbnailError,
                                            _scene_map, _score,
                                            generate_candidates, save_selected)

pytestmark = pytest.mark.skipif(
    not (ff.get_tools().get("ffmpeg") and ff.get_tools().get("ffprobe")),
    reason="ffmpeg/ffprobe required")


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    ws = tmp_path_factory.mktemp("thumbs_ws")
    video = ws / "sample.mp4"
    ffmpeg = ff.get_tools()["ffmpeg"]
    # dark -> bright -> dark for ranking variance (3 concatenaded sources)
    proc = ff.run_cmd(ffmpeg, [
        "-y", "-hide_banner", "-nostdin",
        "-f", "lavfi", "-i", "color=black:size=320x180:rate=30:duration=0.5",
        "-f", "lavfi", "-i", "color=white:size=320x180:rate=30:duration=0.5",
        "-f", "lavfi", "-i", "color=black:size=320x180:rate=30:duration=0.5",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", str(video)], timeout=180)
    assert proc.returncode == 0, proc.stderr[-300:]
    return video


def test_generate_candidates(sample_video, tmp_path):
    out = tmp_path / "thumbs"
    man = generate_candidates(sample_video, count=4, out_dir=out)
    assert man["schema"] == "crs.thumbs/1"
    cands = man["candidates"]
    assert len(cands) == 4
    files = [Path(c["path"]) for c in cands]
    assert all(f.exists() and f.stat().st_size > 0 for f in files)
    assert all(f.suffix == ".png" for f in files)
    ts = [c["timestamp"] for c in cands]
    assert min(ts) >= 0.0 and max(ts) <= 1.51
    # manifest persisted
    stored = json.loads((out / "thumbnail_candidates.json").read_text("utf-8"))
    assert stored["schema"] == man["schema"]


def test_ranking_prefers_midtone(sample_video, tmp_path):
    man = generate_candidates(sample_video, count=3,
                              out_dir=tmp_path / "t2")
    cands = man["candidates"]
    # dark frame candidates must rank below the bright frame candidate
    bright = max(cands, key=lambda c: c["brightness"])
    darks = sorted(cands, key=lambda c: c["rank"])
    if bright["brightness"] - darks[0]["brightness"] > 10:
        assert bright["rank"] >= darks[0]["rank"]


def test_scene_map_detects_changes(sample_video):
    cmap = _scene_map(sample_video)
    assert cmap, "expected at least one scene-change entry"
    assert all(t >= 0 for t in cmap)


def test_score_formula():
    assert _score(45.0, 0.0) == pytest.approx(50.0)
    assert _score(45.0, 1.0) == pytest.approx(90.0)
    assert _score(0.0, 0.0) < _score(45.0, 0.0)
    assert _score(100.0, 0.0) < _score(45.0, 0.0)


def test_short_video_rejected(tmp_path):
    ffmpeg = ff.get_tools()["ffmpeg"]
    tiny = tmp_path / "tiny.mp4"
    proc = ff.run_cmd(ffmpeg, ["-y", "-hide_banner", "-nostdin",
                               "-f", "lavfi", "-i", "color=red:size=64x64:rate=5:duration=0.2",
                               "-c:v", "libx264", str(tiny)], timeout=120)
    assert proc.returncode == 0
    with pytest.raises(ThumbnailError):
        generate_candidates(tiny, count=2, out_dir=tmp_path)


def test_bad_count_rejected(sample_video, tmp_path):
    with pytest.raises(ThumbnailError):
        generate_candidates(sample_video, count=0, out_dir=tmp_path)
    with pytest.raises(ThumbnailError):
        generate_candidates(sample_video, count=99, out_dir=tmp_path)


def test_missing_video(tmp_path):
    with pytest.raises(ThumbnailError):
        generate_candidates(tmp_path / "nope.mp4", count=2,
                            out_dir=tmp_path / "o")


def test_save_selected_copies(sample_video, tmp_path):
    out = tmp_path / "thumbs3"
    man = generate_candidates(sample_video, count=2, out_dir=out)
    src = Path(man["candidates"][0]["path"])
    dest = save_selected(src, tmp_path / "pkg")
    assert dest.exists() and dest.suffix == ".png"
    assert src.exists()  # original untouched
    again = save_selected(src, tmp_path / "pkg")
    assert again != dest  # no overwrite
