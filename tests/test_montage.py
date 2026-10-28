"""Milestone 3.4: montage builder tests (real ffmpeg, synthetic clips)."""

import json
import subprocess
from pathlib import Path

import pytest

from app.services import ffmpeg_service as ff
from app.services.montage_service import (ClipSpec, MontageConfig,
                                          MontageError, MontageInputError,
                                          build_montage, clips_from_manifest,
                                          estimate_duration,
                                          validate_clip_specs)
from app.render_queue.jobs import BatchManifest, JobRecord

pytestmark = pytest.mark.skipif(
    not (ff.get_tools().get("ffmpeg") and ff.get_tools().get("ffprobe")),
    reason="ffmpeg/ffprobe required")

MONTAGE_SCHEMA = "crs.montage/1"


def _ffmpeg():
    return ff.get_tools()["ffmpeg"]


def _make_clip(path: Path, dur: float, size: str, color: str,
               freq: str | None = "440") -> None:
    args = [_ffmpeg(), "-y", "-hide_banner", "-nostdin",
            "-f", "lavfi", "-i", f"color={color}:duration={dur}:size={size}:rate=30"]
    if freq:
        args += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p"]
    if freq:
        args += ["-c:a", "aac"]
    args += [str(path)]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-400:]


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    ws = tmp_path_factory.mktemp("montage_ws")
    clips = ws / "clips"
    clips.mkdir()
    _make_clip(clips / "red.mp4", 3.0, "640x360", "red", freq="440")
    _make_tile_noaudio = clips / "blue.mp4"
    args = [_ffmpeg(), "-y", "-hide_banner", "-nostdin",
            "-f", "lavfi", "-i", "color=blue:duration=2.0:size=1280x720:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(_make_tile_noaudio)]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-400:]
    return ws


@pytest.fixture(scope="module")
def clips_a_b(workspace):
    a = ClipSpec(path=workspace / "clips" / "red.mp4", label="red",
                 info=ff.probe(workspace / "clips" / "red.mp4"))
    b = ClipSpec(path=workspace / "clips" / "blue.mp4", label="blue",
                 info=ff.probe(workspace / "clips" / "blue.mp4"))
    assert a.has_audio and not b.has_audio
    assert a.duration == pytest.approx(3.0, abs=0.25)
    return [a, b]


@pytest.fixture(scope="module")
def throughput(tmp_path_factory, workspace):
    """Build once for multiple assertions, reuse the output."""
    out = tmp_path_factory.mktemp("montage_out")
    a = ClipSpec(path=workspace / "clips" / "red.mp4", label="red",
                 info=ff.probe(workspace / "clips" / "red.mp4"))
    b = ClipSpec(path=workspace / "clips" / "blue.mp4", label="blue",
                 info=ff.probe(workspace / "clips" / "blue.mp4"))
    cfg = MontageConfig(aspect="16:9", resolution="720p", fps=30,
                        transition="cut", quality="low")
    res = build_montage([a, b], cfg, out_dir=out, name="two.mp4")
    return res, out


def _info(path):
    return ff.probe(path)


def _rms_window(path: Path, start: float, dur: float) -> float:
    args = [_ffmpeg(), "-hide_banner", "-nostdin", "-ss", f"{start:.3f}",
            "-t", f"{dur:.3f}", "-i", str(path), "-map", "0:a",
            "-af", "astats=metadata=1:measure_overall=RMS_level:"
                   "measure_perchannel=none", "-f", "null", "-"]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
    for ln in (proc.stdout + proc.stderr).splitlines():
        if "RMS level dB" in ln:
            return float(ln.split(":")[1].strip())
    raise AssertionError("no RMS in astats output")


def _cropdetect(path: Path) -> str:
    args = [_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path),
            "-frames:v", "8", "-vf",
            "cropdetect=limit=24:round=0", "-f", "null", "-"]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
    best = ""
    for ln in (proc.stdout + proc.stderr).splitlines():
        if "crop=" in ln:
            best = ln.split("crop=")[-1].split(" ")[0]
    return best


def _crop_width(crop: str) -> int:
    return int(crop.strip().split(":")[0])


# --------------------------------------------------------------------------
# pure-logic pieces

def test_estimate_cut_and_fade(clips_a_b):
    cfg_cut = MontageConfig(transition="cut")
    assert estimate_duration(clips_a_b, cfg_cut) == pytest.approx(5.0, abs=0.6)
    cfg_fade = MontageConfig(transition="fade", fade_duration=0.5)
    assert estimate_duration(clips_a_b, cfg_fade) == pytest.approx(4.5, abs=0.6)


def test_validate_rejects_bad_config(clips_a_b):
    with pytest.raises(ValueError):
        validate_clip_specs(clips_a_b * 11 + clips_a_b, MontageConfig())
    with pytest.raises(ValueError):
        validate_clip_specs(clips_a_b, MontageConfig(fps=25))
    with pytest.raises(ValueError):
        validate_clip_specs(clips_a_b, MontageConfig(transition="fade",
                                                     fade_duration=0.0))


def test_validate_rejects_tiny_clip(workspace, tmp_path):
    short_path = tmp_path / "short.mp4"
    _make_clip(short_path, 0.7, "320x180", "red", freq=None)
    spec = ClipSpec(path=short_path, label="short",
                    info=ff.probe(short_path))
    with pytest.raises(MontageInputError):
        validate_clip_specs([spec], MontageConfig())


def test_config_out_size_and_defaults():
    assert MontageConfig(aspect="9:16", resolution="720p").out_size == (720, 1280)
    assert MontageConfig(aspect="16:9", resolution="1080p").out_size == (1920, 1080)
    assert MontageConfig(aspect="1:1", resolution="720p").out_size == (720, 720)
    with pytest.raises(MontageError):
        MontageConfig(aspect="odd").out_size
    with pytest.raises(MontageError):
        MontageConfig(aspect="16:9", resolution="360p").out_size


# --------------------------------------------------------------------------
# failure paths

def test_missing_input_raises():
    ghost = ClipSpec(path=Path("Z:/nope/never.mp4"), label="g", info={})
    with pytest.raises(MontageInputError):
        build_montage([ghost], MontageConfig(),
                      out_dir=Path("tmp"), name="x.mp4")


def test_corrupt_input_raises(workspace, tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00" * 2048)
    spec = ClipSpec(path=bad, label="bad", info={"has_video": True})
    with pytest.raises(MontageInputError):
        build_montage([spec], MontageConfig(), out_dir=tmp_path, name="x.mp4")


def test_music_file_missing(clips_a_b, tmp_path):
    cfg = MontageConfig(music_path="Z:/no/music.wav")
    with pytest.raises(MontageInputError):
        build_montage(clips_a_b, cfg, out_dir=tmp_path, name="x.mp4")


# --------------------------------------------------------------------------
# real renders

def test_cut_montage_verify_and_order(throughput):
    res, out = throughput
    v = res["verified"]
    assert v["ok"] is True
    assert v["width"] == 1280 and v["height"] == 720
    assert v["fps"] == pytest.approx(30.0)
    assert v["has_audio"] is True
    p = Path(res["out_path"])
    # ORDER: red clip (with tone) first, blue (silent) second.
    assert _rms_window(p, 0.2, 1.5) > -45.0
    assert _rms_window(p, float(res["measured_duration"]) - 1.2, 1.0) < -55.0
    # duration = sum of clips (cut)
    assert res["measured_duration"] == pytest.approx(5.0, abs=0.7)


def test_montage_manifest(throughput):
    res, _ = throughput
    m = json.loads(Path(res["manifest_path"]).read_text(encoding="utf-8"))
    assert m["schema"] == MONTAGE_SCHEMA
    assert m["out_path"] == res["out_path"]
    assert m["config"]["transition"] == "cut"
    assert len(m["inputs"]) == 2
    assert {c["label"] for c in m["inputs"]} == {"red", "blue"}
    assert m["verified"]["width"] == 1280
    assert m["audio_rms_db"]


def test_pad_letterboxes(clips_a_b, tmp_path):
    cfg = MontageConfig(aspect="1:1", resolution="720p", transition="cut",
                        quality="low")
    res = build_montage(clips_a_b, cfg, out_dir=tmp_path, name="pad.mp4")
    p = Path(res["out_path"])
    info = _info(p)
    assert (info["width"], info["height"]) == (720, 720)
    crop = _cropdetect(p)
    # letterboxed 16:9 in a square: black bars top/bottom -> detected height ~405
    ch = int(_cropdetect(p).split(":")[1]) if crop else 0
    assert ch < 680, f"pad policy letterbox missing (crop={crop})"


def test_crop_fills_frame(clips_a_b, tmp_path):
    cfg = MontageConfig(aspect="1:1", resolution="720p", transition="cut",
                        fit_policy="crop", quality="low")
    res = build_montage(clips_a_b, cfg, out_dir=tmp_path, name="crop.mp4")
    crop = _cropdetect(Path(res["out_path"]))
    cw = _crop_width(crop)
    assert abs(cw - 720) <= 40, f"crop policy should fill frame (crop={crop})"


def test_fade_montage_duration(clips_a_b, tmp_path):
    cfg = MontageConfig(aspect="16:9", resolution="720p", transition="fade",
                        fade_duration=0.5, quality="low")
    res = build_montage(clips_a_b, cfg, out_dir=tmp_path, name="fade.mp4")
    assert res["verified"]["ok"] is True
    # 3.0 + 2.0 - 0.5 crossfade
    assert res["measured_duration"] == pytest.approx(4.5, abs=0.7)
    assert res["estimate_duration"] == pytest.approx(4.5, abs=0.6)


def test_no_overwrite(throughput):
    res, out = throughput
    first = Path(res["out_path"])
    from app.services.montage_service import _unique_out
    bumped = _unique_out(first)
    assert bumped != first and bumped.name != first.name
    assert first.exists()  # untouched


def test_unique_out_name_bump(tmp_path):
    from app.services.montage_service import _unique_out
    target = tmp_path / "m.mp4"
    assert _unique_out(target) == target
    target.write_bytes(b"x")
    second = _unique_out(target)
    assert second != target and not second.exists()


def test_music_overlay_changes_audio(clips_a_b, workspace, tmp_path):
    music_ws = tmp_path / "music"
    music_ws.mkdir()
    music = music_ws / "tone.wav"
    proc = subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-nostdin",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
         str(music)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-300:]
    base_cfg = MontageConfig(aspect="16:9", resolution="720p", quality="low")
    res = build_montage(clips_a_b, base_cfg, out_dir=tmp_path, name="n.mp4")
    cfg_m = MontageConfig(aspect="16:9", resolution="720p", quality="low",
                          music_path=str(music), music_volume=0.9)
    resm = build_montage(clips_a_b, cfg_m, out_dir=tmp_path, name="m.mp4")
    assert resm["verified"]["has_audio"] is True
    # louder music mix must be measurably different from the plain mix
    plain = float(res["audio_rms_db"])
    mixed = float(resm["audio_rms_db"])
    assert abs(plain - mixed) > 1.0 or mixed > plain


# --------------------------------------------------------------------------
# batch-manifest integration

def test_clips_from_manifest_selects_succeeded(tmp_path, workspace):
    def rec(job_id, status, out_path, width=640, height=360):
        return JobRecord(job_id=job_id, batch_id="b_mont", name="alpha",
                         template_id="gap_jump", template_name="Gap Jump",
                         seed=1, config={"resolution": "720p",
                                         "aspect": "16:9"},
                         preset="", status=status,
                         output_path=out_path,
                         verify={"ok": True, "width": width, "height": height,
                                 "fps": 30.0})
    good = workspace / "clips" / "red.mp4"
    man = BatchManifest("b_mont", "mont", path=tmp_path / "manifest.json", jobs=[
        rec("j1", "succeeded", str(good)),
        rec("j2", "failed", str(good)),
        rec("j4", "cancelled", str(good)),
        rec("j5", "pending", str(good)),
    ])
    man.save()
    loaded = BatchManifest.load(man.path)
    specs = clips_from_manifest(loaded, recheck=True)
    assert len(specs) == 1
    assert specs[0].label == "alpha"


def test_clips_from_manifest_missing_output_raises(tmp_path, workspace):
    rec = JobRecord(job_id="j1", batch_id="b_mont", name="ghost",
                    template_id="gap_jump", template_name="Gap Jump",
                    seed=1, config={"resolution": "720p", "aspect": "16:9"},
                    preset="", status="succeeded",
                    output_path=str(tmp_path / "gone.mp4"))
    man = BatchManifest("b_mont", "mont", path=tmp_path / "manifest.json",
                        jobs=[rec])
    with pytest.raises(MontageInputError):
        clips_from_manifest(man)
