"""Milestone 3.3 acceptance: a REAL batch of renders through the queue runner.

Three jobs across two templates, executed sequentially by BatchRunner,
each producing a distinct, verified MP4 and a readable manifest.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CRS_GUI_TESTS", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.render_queue import jobs  # noqa: E402
from app.render_queue.variation import expand_variations  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_real_batch_three_jobs_two_templates(qapp, tmp_path):
    from app.render_queue.runner import BatchRunner
    out_dir = tmp_path / "renders"
    base_cfg = {
        "aspect": "16:9", "resolution": "720p", "fps": 30, "quality": "low",
        "duration": 6.0, "vehicle_count": 1, "camera_style": "cinematic_auto",
        "sfx_enabled": True, "music_enabled": False, "out_dir": str(out_dir),
    }
    batch_id = jobs.new_batch_id()
    _, recs = expand_variations(
        base_cfg, template_ids=["gap_jump", "crash_test"], variations=2,
        seed=4200, seed_step=111, batch_id=batch_id, batch_name="acceptance")
    man = jobs.create_batch("acceptance batch", recs, batch_id=batch_id,
                            directory=tmp_path)
    assert len(man.jobs) == 4

    runner = BatchRunner(man)
    runner.start()
    t0 = time.time()
    while not runner.isFinished() and time.time() - t0 < 900:
        QApplication.processEvents()
        time.sleep(0.05)
    runner.wait(2000)
    QApplication.processEvents()
    assert runner.isFinished(), "batch did not finish"

    statuses = [j.status for j in man.jobs]
    assert all(s == "succeeded" for s in statuses), statuses

    # four distinct files
    outputs = [j.output_path for j in man.jobs]
    assert len(set(outputs)) == 4
    for path_str in outputs:
        p = Path(path_str)
        assert p.exists() and p.stat().st_size > 100_000, path_str

    # per-job verification info recorded
    for j in man.jobs:
        assert j.verify.get("ok") is True
        assert j.verify.get("width") == 1280 and j.verify.get("height") == 720
        assert float(j.verify.get("duration") or 0) >= 5.0
        assert j.started_at and j.finished_at

    # manifest is valid, readable and self-describing
    doc = json.loads(man.path.read_text(encoding="utf-8"))
    assert doc["schema"] == "crs.batch/1"
    assert doc["batch_id"] == batch_id
    assert len(doc["jobs"]) == 4
    seeds = [j["seed"] for j in doc["jobs"]]
    assert seeds == [4200, 4311, 4200, 4311]
    for entry in doc["jobs"]:
        assert entry["template_id"] in ("gap_jump", "crash_test")
        assert entry["config"].get("resolution") == "720p"
        assert entry["verify"].get("ok") is True
        assert entry["finished_at"]
