"""Milestone 3.3: BatchRunner behavior — isolation, retry, cancel (offscreen Qt)."""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CRS_GUI_TESTS", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.render_queue import jobs  # noqa: E402
from app.services import render_service  # noqa: E402
from app.services.godot_service import RenderCancelled  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


BASE_CFG = {"template_id": "gap_jump", "seed": 700, "vehicle_count": 1,
            "aspect": "16:9", "resolution": "720p", "fps": 30,
            "quality": "low", "duration": None,
            "out_dir": str(ROOT / "output" / "batch_tests")}


def _make_manifest(tmp_path, monkeypatch, n=3):
    monkeypatch.setattr(jobs, "BATCHES_DIR", tmp_path)
    batch_id = jobs.new_batch_id()
    from app.render_queue.variation import expand_variations
    _, recs = expand_variations(
        dict(BASE_CFG), template_ids=["gap_jump"], variations=n, seed=700,
        seed_step=111, batch_id=batch_id, batch_name="t")
    return jobs.create_batch("runner test", recs, batch_id=batch_id,
                             directory=tmp_path)


def _make_and_run(manifest, holder=None, on_started=None, max_iters=None):
    from app.render_queue.runner import BatchRunner
    import time
    runner = BatchRunner(manifest)
    if holder is not None:
        holder["runner"] = runner   # visible to signal hooks before start()
    if on_started is not None:
        runner.signals.job_started.connect(on_started)
    runner.start()
    n = max_iters or max(1500, 400 * len(manifest.jobs))
    while n > 0 and not runner.isFinished():
        QApplication.processEvents()
        time.sleep(0.01)
        n -= 1
    assert runner.isFinished(), "runner did not finish in time"
    runner.wait(2000)
    QApplication.processEvents()
    return runner


def test_failed_job_isolated_and_retry(qapp, tmp_path, monkeypatch):
    man = _make_manifest(tmp_path, monkeypatch, n=3)
    attempts = {"n": 0, "registry": {}}

    def fake_run(cfg, *, log_cb=None, progress_cb=None, cancel_event=None,
                 overwrite=False):
        attempts["registry"][(cfg["template_id"], cfg["seed"])] = True
        attempts["n"] += 1
        if cfg["seed"] == 811 and attempts.get("round", 1) == 1:
            if log_cb:
                log_cb("[job log] entering render")
            raise RuntimeError("synthetic boom")
        out = Path(cfg["out_dir"]); out.mkdir(parents=True, exist_ok=True)
        p = out / cfg["file_name"]
        p.write_bytes(b"x")
        return {"mp4_path": str(p), "width": 1280, "height": 720, "fps": 30,
                "duration": 6.0, "has_audio": False, "events_path": None,
                "audio_bed": None, "elapsed": 0.1}

    monkeypatch.setattr(render_service, "run_single_render", fake_run)
    runner_ref = _make_and_run(man)

    statuses = {j.seed: j.status for j in runner_ref.manifest.jobs}
    assert statuses[700] == "succeeded"
    assert statuses[811] == "failed"            # isolated...
    assert statuses[922] == "succeeded"         # ...siblings continued
    assert attempts["n"] == 3
    fail_job = runner_ref.manifest.jobs[1]
    assert fail_job.error and "synthetic boom" in (fail_job.error or "")
    assert any("entering render" in ln for ln in fail_job.render_log)

    # retry: only the failed job runs again
    attempts["n"] = 0
    attempts["round"] = 2
    for job in runner_ref.manifest.jobs:
        if job.status == "failed":
            jobs.retry(job)
    runner_ref2 = _make_and_run(runner_ref.manifest)
    statuses2 = {j.seed: j.status for j in runner_ref2.manifest.jobs}
    assert statuses2[811] == "succeeded"        # now passes
    assert attempts["n"] == 1                   # no re-run of succeeded jobs


def test_cancel_current_marks_job_cancelled(qapp, tmp_path, monkeypatch):
    man = _make_manifest(tmp_path, monkeypatch, n=3)

    def fake_run(cfg, *, log_cb=None, progress_cb=None, cancel_event=None,
                 overwrite=False):
        import time as _t
        _t.sleep(0.25)                     # window for the cancel hook to fire
        if cancel_event is not None and cancel_event.is_set():
            raise RenderCancelled("render cancelled by user")
        out = Path(cfg["out_dir"]); out.mkdir(parents=True, exist_ok=True)
        p = out / cfg["file_name"]
        p.write_bytes(b"x")
        return {"mp4_path": str(p), "width": 1280, "height": 720, "fps": 30,
                "duration": 6.0, "has_audio": False, "events_path": None,
                "audio_bed": None, "elapsed": 0.1}

    monkeypatch.setattr(render_service, "run_single_render", fake_run)
    runner_holder = {}
    cancelled_hook = {"done": 0}

    def on_started(job):
        # the "user" cancels while the FIRST render is running
        if cancelled_hook["done"] == 0 and job.seed == 700:
            cancelled_hook["done"] += 1
            runner_holder["runner"].cancel_current()

    runner_holder = {}
    runner = _make_and_run(man, holder=runner_holder, on_started=on_started)
    statuses = {j.seed: j.status for j in runner.manifest.jobs}
    assert statuses[700] == "cancelled"
    assert statuses[811] == "succeeded"         # next job renders normally
    assert statuses[922] == "succeeded"
    # cancel pending: a new manifest whose jobs never start
    from app.render_queue.runner import BatchRunner
    man2 = _make_manifest(tmp_path, monkeypatch, n=2)
    runner2 = BatchRunner(man2)
    runner2.cancel_pending()
    runner2.cancel_pending()
    runner2.start()
    n = 1500
    import time
    while n > 0 and not runner2.isFinished():
        QApplication.processEvents()
        time.sleep(0.01)
        n -= 1
    QApplication.processEvents()
    statuses2 = {j.seed: j.status for j in runner2.manifest.jobs}
    assert all(v == "cancelled" for v in statuses2.values())
    manifest_doc = __import__("json").loads(
        runner2.manifest.path.read_text(encoding="utf-8"))
    assert manifest_doc["schema"] == "crs.batch/1"
    assert {j["status"] for j in manifest_doc["jobs"]} == {"cancelled"}

def test_manifest_written_after_each_job(qapp, tmp_path, monkeypatch):
    man = _make_manifest(tmp_path, monkeypatch, n=2)
    seen = {"saves": 0}

    def fake_run(cfg, **_k):
        doc = __import__("json").loads(man.path.read_text(encoding="utf-8"))
        seen["saves"] += 1
        assert doc["jobs"][0]["status"] == "running"  # persisted BEFORE finishing
        out = Path(cfg["out_dir"]); out.mkdir(parents=True, exist_ok=True)
        p = out / cfg["file_name"]
        p.write_bytes(b"y")
        return {"mp4_path": str(p), "width": 1280, "height": 720, "fps": 30,
                "duration": 6.0, "has_audio": False, "events_path": None,
                "audio_bed": None, "elapsed": 0.1}

    monkeypatch.setattr(render_service, "run_single_render", fake_run)
    runner = _make_and_run(man, max_iters=600)
    assert seen["saves"] == 2
    assert runner.manifest.jobs[0].verify == {
        "ok": True, "width": 1280, "height": 720, "fps": 30,
        "duration": 6.0, "has_audio": False}
