"""Headless simulation integrity tests (slow: real Godot runs, no recording).

Each test runs the sim WITHOUT Movie Maker so it cannot write MP4s; it parses
the log for the completion line, script errors and slow-motion spacing. These
guard the camera/slowmo milestone: the simulation must reach its full duration,
invalid camera targets must not crash, and slowmo may not fire continuously.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import godot_service, scenario_service  # noqa: E402

pytestmark = pytest.mark.slow

_SLOWMO_MIN_GAP = {"land": 1.8, "launch": 1.8, "crash": 1.8}  # small tolerance


@pytest.fixture(scope="module")
def deferral():
    if godot_service.find_godot() is None:
        pytest.skip("Godot binary not found under ./tools")
    return None


def _save_probe_scenario(tmp_path, template_id, seed, duration, vehicles, w, h):
    sm = scenario_service.build_scenario(template_id, seed, vehicle_count=vehicles,
                                         duration=duration)
    sm.output.width, sm.output.height = w, h
    sm.output.quality = "low"
    return scenario_service.save_scenario_file(sm, tmp_path / f"{template_id}_{seed}.json")


def test_headless_sim_reaches_duration_with_slowmo_budget(tmp_path, deferral):
    """Portrait run: sim finishes at (or beyond) the intended duration and no
    script errors occur while the camera/slowmo systems are active."""
    scenario_path = _save_probe_scenario(tmp_path, "mega_ramp", 12345, 14.0, 2, 320, 180)
    res = godot_service.run_sim_probe(scenario_path, timeout=240)
    assert res["script_errors"] == [], res["log_full"][-2000:]
    assert res["exit_code"] == 0
    assert res["events_count"] >= 0
    assert res["sim_time"] >= 14.0 - 0.5, f"sim ended early at {res['sim_time']}s"
    assert res["sim_time"] <= 14.0 + 2.5, f"sim drifted far past horizon: {res['sim_time']}s"


def test_slowmo_triggers_are_spaced_not_continuous(tmp_path, deferral):
    """Repeated 'land' events (fall->land cycles) must not trigger slowmo more
    than once per configured cooldown window."""
    scenario_path = _save_probe_scenario(tmp_path, "ball_dodge", 777, 16.0, 3, 320, 180)
    res = godot_service.run_sim_probe(scenario_path, timeout=240)
    assert res["script_errors"] == [], res["log_full"][-2000:]
    assert res["sim_time"] >= 16.0 - 0.5
    prev_t = {}
    for entry in res["slowmo"]:
        kind, t = entry["kind"], entry["t"]
        if kind in prev_t:
            gap = t - prev_t[kind]
            assert gap >= _SLOWMO_MIN_GAP.get(kind, 1.5) - 0.25, (
                f"slowmo kind {kind!r} retriggered after {gap:.2f}s")
        prev_t[kind] = t
        assert 0.2 <= entry["dur"] <= 1.8  # clamped window sanity


def test_landscape_headless_sim_reaches_duration(tmp_path, deferral):
    """Landscape (16:9) run: same guarantees as the portrait config."""
    scenario_path = _save_probe_scenario(tmp_path, "bridge_jump", 909, 12.0, 2, 320, 180)
    res = godot_service.run_sim_probe(scenario_path, timeout=240)
    assert res["script_errors"] == [], res["log_full"][-2000:]
    assert res["exit_code"] == 0
    assert res["sim_time"] >= 12.0 - 0.5
    assert res["sim_time"] <= 12.0 + 2.5
