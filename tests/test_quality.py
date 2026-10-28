"""Milestone 3.5 Phase 2: quality_service — event persistence + scoring."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import quality_service as qs  # noqa: E402
from app.services import render_service  # noqa: E402
from app.services import ffmpeg_service as ff  # noqa: E402

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _events(path: Path, types=("launch", "land", "finish"), **extra):
    doc = {
        "schema": "crs.events/1", "created_at": "2026-10-23T00:00:00Z",
        "events": [{"i": i, "type": t, "t": 1.0 + i, "vehicle": 0,
                    "impact_speed": 12.0 if t == "land" else None}
                   for i, t in enumerate(types)],
    }
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


@pytest.fixture(scope="module")
def colored_clip(tmp_path_factory):
    """A loud red clip with a sine bed — in-band, detectable color."""
    out = tmp_path_factory.mktemp("clips") / "clip_red.mp4"
    ffmpeg = ff.get_tools().get("ffmpeg")
    ff.run_cmd(ffmpeg, [
        "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=320x240:d=4",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", str(out)])
    assert out.is_file()
    return out


@pytest.fixture(scope="module")
def dark_clip(tmp_path_factory):
    out = tmp_path_factory.mktemp("clips") / "clip_dark.mp4"
    ffmpeg = ff.get_tools().get("ffmpeg")
    ff.run_cmd(ffmpeg, [
        "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)])
    return out


def _fake_res(mp4: Path, events_file=None, has_audio=True):
    info = ff.probe(mp4)
    return {"mp4_path": str(mp4), "width": info["width"],
            "height": info["height"], "fps": round(info["fps"], 2),
            "duration": info["duration"], "has_audio": has_audio,
            "events_file": events_file, "scenario_path": None}


# ---------------------------------------------------------------------------
# event persistence
# ---------------------------------------------------------------------------


def test_persist_events_copies_and_never_overwrites(tmp_path):
    mp4 = tmp_path / "job_1.mp4"
    mp4.write_bytes(b"x")
    src = tmp_path / "run" / "events.json"
    src.parent.mkdir()
    _events(src, ("launch", "finish"))

    first = qs.persist_events(str(src), mp4)
    assert first == str(tmp_path / "job_1.events.json")
    assert json.loads(Path(first).read_text(encoding="utf-8"))["events"]

    second = qs.persist_events(str(src), mp4)
    assert second == str(tmp_path / "job_1_1.events.json")   # stem bump like *_1.mp4 reruns

    # collision forced: countdown to _2 when the bump already exists
    (tmp_path / "job_1_1.events.json").rename(tmp_path / "keep.json")
    (tmp_path / "job_1_1.events.json").touch()
    (tmp_path / "job_1_2.events.json").touch()
    third = qs.persist_events(str(src), mp4)
    assert third == str(tmp_path / "job_1_3.events.json")


def test_persist_events_none_cases(tmp_path):
    mp4 = tmp_path / "job.mp4"
    mp4.write_bytes(b"x")
    assert qs.persist_events(None, mp4) is None
    assert qs.persist_events(str(tmp_path / "missing.json"), mp4) is None


def test_count_events(tmp_path):
    ok = tmp_path / "ok.json"
    _events(ok, ("launch", "land", "land", "finish"))
    assert qs.count_events(str(ok)) == 4
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert qs.count_events(str(bad)) is None
    empty_list = tmp_path / "empty.json"
    empty_list.write_text(json.dumps({"schema": "crs.events/1", "events": []}),
                          encoding="utf-8")
    assert qs.count_events(str(empty_list)) == 0
    assert qs.count_events(None) is None


# ---------------------------------------------------------------------------
# score_render — hard failures
# ---------------------------------------------------------------------------


def test_score_valid_run_no_hard_failures(colored_clip):
    res = _fake_res(colored_clip)
    report = qs.score_render(colored_clip, template_id="gap_jump",
                             expected={"width": 320, "height": 240,
                                       "fps": res["fps"], "want_audio": True})
    assert report["schema"] == "crs.quality/1"
    assert report["hard_failures"] == []
    assert report["info"]["telemetry"]["state"] == "missing"
    assert "NOT a measure of virality" in report["info"]["statement"]
    assert report["info"]["audio_rms_db"] is not None
    assert -32 <= report["info"]["audio_rms_db"] <= -10


def test_score_malformed_events_is_hard(colored_clip, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("definitely not json", encoding="utf-8")
    report = qs.score_render(colored_clip, events_file=str(bad))
    assert any("malformed" in h for h in report["hard_failures"])
    assert report["overall"] == "fail"


def test_score_missing_launch_is_hard_for_ramp_templates(colored_clip, tmp_path):
    ev = tmp_path / "ev.json"
    _events(ev, ("land", "finish"))          # gap_jump run with no launch
    report = qs.score_render(colored_clip, template_id="gap_jump",
                             events_file=str(ev))
    assert any("launch" in h for h in report["hard_failures"])
    assert report["overall"] == "fail"


def test_score_non_ramp_template_without_launch_is_pass(colored_clip, tmp_path):
    ev = tmp_path / "ev.json"
    _events(ev, ("finish",))
    report = qs.score_render(colored_clip, template_id="obstacle_course",
                             events_file=str(ev))
    # obstacle_course has no launch zone: no launch must NOT be a failure
    assert not any("launch" in h for h in report["hard_failures"])
    assert not any("launch" in s for s in report["soft_warnings"])
    assert report["overall"] == "pass"


def test_score_broken_video_is_hard(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    report = qs.score_render(junk)
    assert report["hard_failures"], "bogus file must hard-fail"


def test_score_wrong_resolution_is_hard(colored_clip):
    report = qs.score_render(colored_clip,
                             expected={"width": 1920, "height": 1080,
                                       "fps": 30})
    assert any("resolution" in h for h in report["hard_failures"])


# ---------------------------------------------------------------------------
# score_render — soft warnings
# ---------------------------------------------------------------------------


def test_soft_missing_finish_and_stuck_crash_test(colored_clip, tmp_path):
    ev = tmp_path / "ev.json"
    _events(ev, ("launch", "land"))          # mega_ramp: no finish
    report = qs.score_render(colored_clip, template_id="mega_ramp",
                             events_file=str(ev))
    assert not report["hard_failures"]
    assert any("finish" in s for s in report["soft_warnings"])

    ev2 = tmp_path / "ev2.json"
    _events(ev2, ("finish",))                # crash_test with no crashes
    report = qs.score_render(colored_clip, template_id="crash_test",
                             events_file=str(ev2))
    assert not report["hard_failures"]
    assert any("crash" in s for s in report["soft_warnings"])


def test_soft_unrecovered_fall(colored_clip, tmp_path):
    doc = {"schema": "crs.events/1", "events": [
        {"type": "launch", "t": 0.5, "vehicle": 0},
        {"type": "land", "t": 1.5, "vehicle": 0, "impact_speed": 12.0},
        {"type": "fall", "t": 2.0, "vehicle": 1},   # unrecovered vehicle
    ]}
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps(doc), encoding="utf-8")
    report = qs.score_render(colored_clip, template_id="gap_jump",
                             events_file=str(ev))
    assert not report["hard_failures"]           # launch present
    assert any("fall" in s for s in report["soft_warnings"])
    assert report["info"]["telemetry"]["types"]["fall"] == 1
    # a recovered fall (later land/finish) must NOT warn
    doc2 = {"schema": "crs.events/1", "events": [
        {"type": "launch", "t": 0.5, "vehicle": 0},
        {"type": "fall", "t": 1.0, "vehicle": 0},
        {"type": "land", "t": 2.0, "vehicle": 0, "impact_speed": 12.0},
    ]}
    ev2 = tmp_path / "ev2.json"
    ev2.write_text(json.dumps(doc2), encoding="utf-8")
    report2 = qs.score_render(colored_clip, template_id="gap_jump",
                              events_file=str(ev2))
    assert not any("fall" in s for s in report2["soft_warnings"])


def test_dead_window_warns(tmp_path):
    ffmpeg = ff.get_tools().get("ffmpeg")
    out = tmp_path / "race.mp4"
    ff.run_cmd(ffmpeg, [
        "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=s=320x240:d=22",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)])
    doc = {"schema": "crs.events/1", "events": [
        {"type": "launch", "t": 0.5, "vehicle": 0},
        {"type": "finish", "t": 21.5, "vehicle": 0},
    ]}
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps(doc), encoding="utf-8")
    report = qs.score_render(out, template_id="gap_jump", events_file=str(ev))
    dw = report["info"]["dead_window"]
    assert dw["longest_gap_s"] > 10
    assert any("event-free stretch" in s for s in report["soft_warnings"])


def test_soft_no_big_impact(colored_clip, tmp_path):
    doc = {"schema": "crs.events/1", "events": [
        {"type": "launch", "t": 1.0, "vehicle": 0},
        {"type": "land", "t": 2.0, "vehicle": 0, "impact_speed": 3.35},
    ]}
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps(doc), encoding="utf-8")
    report = qs.score_render(colored_clip, template_id="mega_ramp",
                             events_file=str(ev))
    assert any("impact" in s for s in report["soft_warnings"])


def test_soft_empty_events(colored_clip, tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"schema": "crs.events/1", "events": []}),
                  encoding="utf-8")
    report = qs.score_render(colored_clip, events_file=str(ev))
    assert not report["hard_failures"]
    assert any("zero events" in s for s in report["soft_warnings"])


def test_audio_silence_warns(tmp_path):
    ffmpeg = ff.get_tools().get("ffmpeg")
    out = tmp_path / "silent.mp4"
    ff.run_cmd(ffmpeg, [
        "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=3",
        "-f", "lavfi",
        "-i", "sine=frequency=300:sample_rate=48000:duration=3",
        "-af", "volume=0.0005",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", str(out)])
    report = qs.score_render(out)
    assert report["info"]["audio_rms_db"] is not None
    assert report["info"]["audio_rms_db"] < qs.RMS_DB_LOW
    assert any("RMS" in s for s in report["soft_warnings"])


def test_frame_checks_dark_and_color(colored_clip, dark_clip):
    # dark clip luma ~0 -> dark streak warning; red clip + red palette -> ok
    dark = qs.score_render(dark_clip)
    assert any("dark/black streak" in s for s in dark["soft_warnings"])
    assert dark["info"]["frame_check"]["samples"] >= 8

    report = qs.score_render(colored_clip,
                             primary_color="#ff0000")
    assert not any("primary vehicle color" in s
                   for s in report["soft_warnings"])
    assert report["info"]["frame_check"]["primary_color_detection_rate"] > 0.5


# ---------------------------------------------------------------------------
# report writing (atomic, no overwrite)
# ---------------------------------------------------------------------------


def test_write_quality_report_atomic_and_unique(colored_clip):
    report = qs.score_render(colored_clip, sample_frames=False)
    p1 = qs.write_quality_report(colored_clip, report)
    assert p1.name.endswith(".quality.json")
    assert json.loads(p1.read_text(encoding="utf-8"))["quality_id"]
    assert not list(p1.parent.glob("*.tmp"))          # atomic: no tmp residue
    p2 = qs.write_quality_report(colored_clip, report)
    assert p2.name == colored_clip.stem + "_1.quality.json"
    assert json.loads(p2.read_text(encoding="utf-8"))["overall"] == \
        report["overall"]


# ---------------------------------------------------------------------------
# runner integration + manifest compat
# ---------------------------------------------------------------------------


def test_runner_adds_events_count_and_quality_report(tmp_path, monkeypatch):
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _os.environ.setdefault("CRS_GUI_TESTS", "1")
    from PySide6.QtWidgets import QApplication  # noqa: F401
    from app.render_queue import jobs
    from app.render_queue.runner import BatchRunner

    mp4 = tmp_path / "batch_job.mp4"
    mp4.write_bytes(b"x")
    ev = tmp_path / "run_events.json"
    _events(ev, ("launch", "land", "finish"))

    def fake_run(cfg, **_k):
        return {"mp4_path": str(mp4), "width": 1280, "height": 720, "fps": 30,
                "duration": 6.0, "has_audio": False, "events_path": None,
                "events_file": str(ev), "audio_bed": None, "elapsed": 0.1}

    monkeypatch.setattr(render_service, "run_single_render", fake_run)
    from app.render_queue.variation import expand_variations
    cfg = {"template_id": "gap_jump", "seed": 700, "vehicle_count": 1,
           "aspect": "16:9", "resolution": "720p", "fps": 30,
           "quality": "low", "duration": None,
           "out_dir": str(tmp_path)}
    _, recs = expand_variations(cfg, template_ids=["gap_jump"], variations=1,
                                seed=700, seed_step=111,
                                batch_id="b_quality", batch_name="t")
    man = jobs.create_batch("quality test", recs, batch_id="b_quality",
                            directory=tmp_path)
    runner = BatchRunner(man)
    runner.start()
    import time
    QApplication.processEvents()
    n = 1500
    while n > 0 and not runner.isFinished():
        QApplication.processEvents()
        time.sleep(0.01)
        n -= 1
    assert runner.isFinished()
    job = man.jobs[0]
    assert job.status == "succeeded"
    assert job.verify["events_count"] == 3

    doc = json.loads(man.path.read_text(encoding="utf-8"))
    assert doc["jobs"][0]["verify"]["events_count"] == 3
    # quality report was persisted next to the (fake) video
    assert any(tmp_path.glob("batch_job.quality*.json"))


def test_old_manifest_without_events_count_loads(tmp_path, monkeypatch):
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _os.environ.setdefault("CRS_GUI_TESTS", "1")
    from PySide6.QtWidgets import QApplication
    from app.render_queue import jobs

    man_dir = tmp_path / "batches" / "b_old"
    man_dir.mkdir(parents=True)
    doc = {
        "schema": "crs.batch/1", "batch_id": "b_old", "name": "old",
        "created_at": "2026-10-01T00:00:00Z",
        "jobs": [{"id": "j1", "job_id": "j1", "batch_id": "b_old",
                  "name": "old gap", "template_id": "gap_jump",
                  "template_name": "Gap jump", "seed": 700,
                  "status": "succeeded", "config": {
            "template_id": "gap_jump", "seed": 700, "vehicle_count": 1,
            "aspect": "16:9", "resolution": "720p", "fps": 30,
            "quality": "low", "duration": None, "out_dir": "."},
            "output_path": "x.mp4", "error": None, "progress": 0,
            "verify": {"ok": True, "width": 1280, "height": 720, "fps": 30,
                       "duration": 6.0, "has_audio": False},
            "render_log": [], "started_at": None, "finished_at": None,
            "attempts": 1}],
    }
    (man_dir / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")
    QApplication.instance() or QApplication([])
    man = jobs.BatchManifest.load(man_dir / "manifest.json")
    job = man.jobs[0]
    assert "events_count" not in job.verify
    job.verify["extra_future_key"] = 1
    man.save()
    man2 = jobs.BatchManifest.load(man.path)
    assert man2.jobs[0].verify.get("extra_future_key") == 1


def test_no_events_file_keeps_verify_exactly_legacy(tmp_path, monkeypatch):
    # regression: batch with no telemetry keeps the exact legacy verify dict
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _os.environ.setdefault("CRS_GUI_TESTS", "1")
    from PySide6.QtWidgets import QApplication
    from app.render_queue import jobs
    from app.render_queue.runner import BatchRunner
    from app.render_queue.variation import expand_variations

    def fake_run(cfg, **_k):
        return {"mp4_path": str(tmp_path / "out.mp4"), "width": 1280,
                "height": 720, "fps": 30, "duration": 6.0,
                "has_audio": False, "events_path": None, "events_file": None,
                "audio_bed": None, "elapsed": 0.1}

    monkeypatch.setattr(render_service, "run_single_render", fake_run)
    cfg = {"template_id": "gap_jump", "seed": 700, "vehicle_count": 1,
           "aspect": "16:9", "resolution": "720p", "fps": 30,
           "quality": "low", "duration": None, "out_dir": str(tmp_path)}
    QApplication.instance() or QApplication([])
    _, recs = expand_variations(cfg, template_ids=["gap_jump"], variations=1,
                                seed=700, seed_step=111,
                                batch_id="b_legacy", batch_name="t")
    man = jobs.create_batch("legacy", recs, batch_id="b_legacy",
                            directory=tmp_path)
    runner = BatchRunner(man)
    runner.start()
    import time
    QApplication.processEvents()
    n = 1500
    while n > 0 and not runner.isFinished():
        QApplication.processEvents()
        time.sleep(0.01)
        n -= 1
    assert man.jobs[0].verify == {
        "ok": True, "width": 1280, "height": 720, "fps": 30,
        "duration": 6.0, "has_audio": False}
