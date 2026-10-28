"""Milestone 3.4: content package tests."""

import json
import subprocess
from pathlib import Path

import pytest

from app.services import ffmpeg_service as ff
from app.services.package_service import (PACKAGE_SCHEMA, PackageError,
                                          create_package, slugify)

pytestmark = pytest.mark.skipif(
    not (ff.get_tools().get("ffmpeg") and ff.get_tools().get("ffprobe")),
    reason="ffmpeg/ffprobe required")


def _ffmpeg():
    return ff.get_tools()["ffmpeg"]


@pytest.fixture(scope="module")
def final_video(tmp_path_factory):
    ws = tmp_path_factory.mktemp("pkg_ws")
    video = ws / "final.mp4"
    proc = subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-nostdin",
         "-f", "lavfi", "-i", "color=green:duration=1.0:size=320x180:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=330:duration=1.0",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", str(video)], capture_output=True, text=True,
        timeout=180)
    assert proc.returncode == 0, proc.stderr[-300:]
    return video


def _make_thumb(path: Path) -> None:
    proc = subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-nostdin",
         "-f", "lavfi", "-i", "color=orange:size=480x270",
         "-frames:v", "1", str(path)], capture_output=True, text=True,
        timeout=120)
    assert proc.returncode == 0, proc.stderr[-300:]


def test_slugify():
    assert slugify("My Cool Jump!") == "My_Cool_Jump"
    assert slugify("") == "video"


def test_create_package_basics(final_video, tmp_path):
    root = tmp_path / "packages"
    thumb = tmp_path / "pick.png"
    _make_thumb(thumb)
    montage_manifest = tmp_path / "m.montage.json"
    montage_manifest.write_text(json.dumps({"schema": "x"}), encoding="utf-8")
    doc = create_package(
        final_video, title="Best Jump", description="desc text",
        tags=["stunts", "godot"], aspect="", thumbnail=thumb,
        montage_manifest=montage_manifest,
        source_jobs=[{"job_id": "j1", "seed": 5}],
        settings={"quality": "high"}, out_root=root)
    pkg = Path(doc["package_dir"])
    assert doc["package_id"] and doc["package_id"].startswith("pkg_")
    assert (pkg / "video.mp4").exists()
    assert (pkg / "thumbnail.png").exists()
    meta = json.loads((pkg / "metadata.json").read_text("utf-8"))
    assert meta["schema"] == PACKAGE_SCHEMA
    assert meta["title"] == "Best Jump"
    assert meta["description"] == "desc text"
    assert meta["tags"] == ["stunts", "godot"]
    assert meta["aspect"] == "16:9"
    assert meta["source_jobs"] == [{"job_id": "j1", "seed": 5}]
    assert meta["settings"] == {"quality": "high"}
    assert meta["montage_manifest"].endswith("m.montage.json")
    yt = (pkg / "youtube.txt").read_text("utf-8")
    assert yt.startswith("Best Jump")
    assert "#stunts" in yt and "#godot" in yt


def test_aspect_guess_other(final_video, tmp_path):
    doc = create_package(final_video, title="Odd One", out_root=tmp_path)
    meta = json.loads(Path(doc["metadata_path"]).read_text("utf-8"))
    assert meta["aspect"] == "16:9"


def test_no_thumbnail_column(final_video, tmp_path):
    doc = create_package(final_video, title="No Thumb Here", out_root=tmp_path)
    meta = json.loads(Path(doc["metadata_path"]).read_text("utf-8"))
    assert meta["thumbnail"] is None
    assert not (Path(doc["package_dir"]) / "thumbnail.png").exists()
    assert not Path(doc["youtube_txt_path"]).read_text().count("#")


def test_title_required(final_video, tmp_path):
    with pytest.raises(PackageError):
        create_package(final_video, title="   ", out_root=tmp_path)


def test_title_fail_quietly_on_len(final_video, tmp_path):
    with pytest.raises(PackageError):
        create_package(final_video, title="x" * 121, out_root=tmp_path)


def test_tags_fail_quietly_on_count(final_video, tmp_path):
    with pytest.raises(PackageError):
        create_package(final_video, title="Many tags",
                       tags=[f"t{i}" for i in range(21)], out_root=tmp_path)


def test_missing_video_raises(tmp_path):
    with pytest.raises(PackageError):
        create_package(tmp_path / "nope.mp4", title="x", out_root=tmp_path)


def test_no_overwrite_folder(final_video, tmp_path):
    create_package(final_video, title="Same Name", out_root=tmp_path)
    doc2 = create_package(final_video, title="Same Name", out_root=tmp_path)
    d1 = tmp_path / "Same_Name"
    d2 = Path(doc2["package_dir"])
    assert d1.exists() and d2.exists() and d1 != d2


def test_source_video_untouched(final_video, tmp_path):
    before = final_video.stat().st_mtime_ns
    create_package(final_video, title="Keep Source", out_root=tmp_path)
    assert final_video.stat().st_mtime_ns == before
