"""Milestone 3.3: variation expansion, queue model, manifests, restart recovery."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.render_queue import jobs  # noqa: E402
from app.render_queue.variation import (  # noqa: E402
    expand_variations, build_variation_config, VariationError)
from app.services.render_service import validate_config  # noqa: E402


BASE_CFG = {"template_id": "gap_jump", "seed": 7, "vehicle_count": 2,
            "aspect": "16:9", "resolution": "720p", "fps": 30,
            "quality": "low", "duration": None, "camera_style": "single_wide",
            "out_dir": str(ROOT / "output" / "batch_tests")}


def _make_jobs(template_ids, variations=2, seed=500, seed_step=111):
    batch_id = jobs.new_batch_id()
    _, recs = expand_variations(
        dict(BASE_CFG), template_ids=template_ids, variations=variations,
        seed=seed, seed_step=seed_step, batch_id=batch_id, batch_name="t")
    return batch_id, recs


# ------------------------------------------------------------- variations
def test_expansion_seeds_names_and_validation():
    _, recs = _make_jobs(["gap_jump"], variations=3)
    assert len(recs) == 3
    seeds = [r.seed for r in recs]
    assert seeds == [500, 611, 722]
    assert len({r.config["file_name"] for r in recs}) == 3  # unique outputs
    for r in recs:
        validate_config(r.config)          # used by the render path as-is
        assert r.status == "pending"
        assert r.config["template_id"] == "gap_jump"
        assert r.config["camera_style"] == "single_wide"


def test_expansion_multi_template_same_seed_ok():
    batch_id, recs = _make_jobs(["gap_jump", "crash_test"], variations=2)
    assert len(recs) == 4
    assert recs[0].seed == recs[2].seed == 500          # same seed, different template
    assert recs[0].config["file_name"] != recs[2].config["file_name"]


def test_vehicle_count_clamped_per_template():
    cfg = build_variation_config(dict(BASE_CFG), "crash_test", 11, "b1")
    assert cfg["vehicle_count"] == 2                    # max_vehicles
    cfg2 = build_variation_config(dict(BASE_CFG), "gap_jump", 11, "b1")
    assert cfg2["vehicle_count"] == 2


def test_invalid_variation_rejected_before_enqueue():
    bad = dict(BASE_CFG, aspect="4:7")
    with pytest.raises(VariationError):
        batch_id = jobs.new_batch_id()
        expand_variations(bad, template_ids=["gap_jump"], variations=1,
                          seed=500, seed_step=111, batch_id=batch_id,
                          batch_name="t")
    # config that passes expansion is individually validated too
    cfg = build_variation_config(dict(BASE_CFG), "gap_jump", 1, "b1")
    validate_config(cfg)


def test_batch_too_large_rejected():
    with pytest.raises(VariationError):
        batch_id = jobs.new_batch_id()
        expand_variations(dict(BASE_CFG), template_ids=["gap_jump"] * 26,
                          variations=2, seed=500, seed_step=111,
                          batch_id=batch_id, batch_name="t")


def test_batch_and_output_paths_recorded():
    batch_id, recs = _make_jobs(["gap_jump"], variations=1)
    j = recs[0]
    assert j.config["out_dir"] == BASE_CFG["out_dir"]
    assert j.config["file_name"].startswith("gap_jump_s")
    assert batch_id in j.config["file_name"]


def test_reproducibility_same_seed_same_scenario():
    from app.services.scenario_service import build_scenario
    a = build_scenario("gap_jump", 771, vehicle_count=2, duration=8.0,
                       camera_style="single_wide")
    b = build_scenario("gap_jump", 771, vehicle_count=2, duration=8.0,
                       camera_style="single_wide")
    assert a.to_document() == b.to_document()           # identical documents
    c = build_scenario("gap_jump", 772, vehicle_count=2, duration=8.0,
                       camera_style="single_wide")
    assert c.to_document() != b.to_document()           # different seed differs
    assert [v.color for v in c.vehicles] != [v.color for v in b.vehicles]


# ------------------------------------------------------------- job states
def test_transition_rules_and_timestamps():
    j = jobs.JobRecord("j1", "b", "name", "gap_jump", "Gap", 5, {})

    with pytest.raises(jobs.JobError):
        jobs.transition(j, "succeeded")     # pending -> succeeded illegal
    jobs.transition(j, "running")
    assert j.started_at and not j.finished_at
    jobs.transition(j, "failed", error="boom", render_log=["l1", "l2"])
    assert j.error == "boom" and j.finished_at
    with pytest.raises(jobs.JobError):
        jobs.transition(j, "succeeded")
    jobs.retry(j)
    assert j.status == "pending" and j.error is None

    j2 = jobs.JobRecord("j2", "b", "n", "gap_jump", "Gap", 6, {})
    jobs.transition(j2, "running")
    jobs.transition(j2, "cancelled", error="stop")
    jobs.retry(j2)
    assert j2.status == "pending"

    j3 = jobs.JobRecord("j3", "b", "n", "gap_jump", "Gap", 6, {})
    jobs.transition(j3, "running")
    jobs.transition(j3, "succeeded")
    with pytest.raises(jobs.JobError):
        jobs.retry(j3)


# ------------------------------------------------------------- manifests
def test_manifest_roundtrip_and_atomic(tmp_path):
    _, recs = _make_jobs(["gap_jump"], variations=2)
    man = jobs.create_batch("x2", recs, directory=tmp_path)
    man.save()
    assert not list(tmp_path.glob("*/manifest.json.tmp"))
    loaded = jobs.BatchManifest.load(man.path)
    assert loaded.batch_id == man.batch_id
    assert [j.name for j in loaded.jobs] == [j.name for j in man.jobs]
    assert [j.config for j in loaded.jobs] == [j.config for j in man.jobs]

    bad = tmp_path / "bad" / "manifest.json"
    bad.parent.mkdir()
    bad.write_text('{"jobs": [], "batch_id": "b"}', encoding="utf-8")
    with pytest.raises(jobs.JobError):
        jobs.BatchManifest.load(bad)


def test_progress_counts_and_next_pending(tmp_path):
    _, recs = _make_jobs(["gap_jump"], variations=4)
    man = jobs.create_batch("q", recs, directory=tmp_path)
    assert man.next_pending() is recs[0]
    jobs.transition(recs[0], "running")
    jobs.transition(recs[0], "succeeded")
    jobs.transition(recs[1], "running")
    jobs.transition(recs[1], "failed", error="x")
    done, total = man.progress_done()
    assert (done, total) == (2, 4)
    assert man.next_pending() is recs[2]
    assert man.overall_label() == "2/4"


# ------------------------------------------------------------- recovery
def test_restart_recovery_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "BATCHES_DIR", tmp_path)
    _, recs = _make_jobs(["gap_jump"], variations=5)
    man = jobs.create_batch("rec", recs, directory=tmp_path)
    # simulate an interrupted run plus two completed jobs
    jobs.transition(recs[0], "running")
    man.save()
    out_file = Path(recs[1].config["out_dir"]) / recs[1].config["file_name"]
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(b"placeholder")
    jobs.transition(recs[1], "running")
    jobs.transition(recs[1], "succeeded",
                    verify={"ok": True}, render_log=[])
    recs[1].output_path = str(out_file)
    jobs.transition(recs[2], "running")
    jobs.transition(recs[2], "succeeded")
    recs[2].output_path = str(tmp_path / "deleted.mp4")
    jobs.transition(recs[3], "running")
    jobs.transition(recs[3], "failed", error="gpu")
    man.save()

    loaded = jobs.load_all_manifests(directory=tmp_path)[0]
    statuses = {j.name: j.status for j in loaded.jobs}
    assert statuses[recs[0].name] == "pending"      # interrupted -> requeueable
    assert statuses[recs[1].name] == "succeeded"    # file still there
    assert statuses[recs[2].name] == "pending"      # file gone
    assert statuses[recs[3].name] == "failed"       # untouched, retryable
    assert statuses[recs[4].name] == "pending"
    # nothing auto-runs: the loaded queue must start paused
    assert loaded.by_status("running") == []


def test_unreadable_manifest_is_skipped(tmp_path):
    d = tmp_path / "batch_bad"
    d.mkdir()
    (d / "manifest.json").write_text("{{{", encoding="utf-8")
    assert jobs.load_all_manifests(directory=tmp_path) == []
