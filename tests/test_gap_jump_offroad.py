"""Regression: gap_jump seed 4242 vehicles must follow the racing line.

Historical failure (M3.1.1): the car-anchored pursuit target let both cars
drift off-road forever; secondary vehicle fell/respawned in a loop.

Guards:
- sim runs to full duration, no script errors
- both vehicles launch, land and finish (zone collision mask fix)
- neither vehicle falls off-world
- no vehicle wanders far off-road while grounded (|z| way outside lane)
- finished cars brake and stay parked (no teleport-back loops)
"""
import json
import os
import re
from pathlib import Path

import pytest

from app.services.godot_service import run_sim_probe
from app.services.scenario_service import build_scenario, save_scenario_file

pytestmark = pytest.mark.slow

DURATION = 16.0
LANE_LIMIT = 10.0  # grounded cars should stay within ~road half-width + margin


@pytest.fixture(scope="module")
def probe():
    path = Path(os.environ.get("TEMP", ".")) / "crs_gap_jump_regression.json"
    sc = build_scenario("gap_jump", 4242, vehicle_count=2, duration=DURATION,
                        output_overrides={"width": 640, "height": 360,
                                          "out_file": "gap_regression_probe.mp4"})
    save_scenario_file(sc, path)
    os.environ["CRS_DBG_ALL"] = "1"
    try:
        return run_sim_probe(path, timeout=300)
    finally:
        path.unlink(missing_ok=True)
        os.environ.pop("CRS_DBG_ALL", None)


def _events(probe):
    return json.loads(Path(probe["events_path"]).read_text())["events"]


def _trace(probe):
    """[{v, pos(3), spd, fgnd, thr, brk, steer}, ...] from [CRS all] lines."""
    rows = []
    pat = re.compile(
        r"v(\d) pos=\(([-\d.]+), ([-\d.]+), ([-\d.]+)\) spd=([\d.]+) "
        r"fgnd=(\d) thr=([\d.]+) brk=([\d.]+)")
    for ln in probe["log_full"].splitlines():
        if "CRS all" not in ln:
            continue
        for m in pat.finditer(ln):
            x, y, z = float(m.group(2)), float(m.group(3)), float(m.group(4))
            rows.append({"v": int(m.group(1)), "pos": (x, y, z),
                         "spd": float(m.group(5)), "fgnd": int(m.group(6)),
                         "thr": float(m.group(7)), "brk": float(m.group(8))})
    return rows


def test_runs_to_duration_without_errors(probe):
    assert not probe["script_errors"]
    assert probe["sim_time"] >= DURATION - 0.5


def test_both_vehicles_run_the_track(probe):
    ev = _events(probe)
    for idx in (0, 1):
        kinds = {e["type"] for e in ev if e["vehicle"] == idx}
        assert "launch" in kinds, f"vehicle {idx} never launched: {ev}"
        assert "land" in kinds, f"vehicle {idx} never landed: {ev}"
        assert "fall" not in kinds, f"vehicle {idx} fell off-world: {ev}"


def test_no_offroad_wander_while_grounded(probe):
    rows = _trace(probe)
    assert rows, "trace missing: run_sim_probe must set CRS_DBG_ALL=1"
    for row in rows:
        if row["fgnd"] >= 3 and row["spd"] > 3.0:
            assert abs(row["pos"][2]) <= LANE_LIMIT, row


def test_finished_cars_brake_to_a_stop(probe):
    rows = _trace(probe)
    by_v = {v: [r for r in rows if r["v"] == v] for v in (0, 1)}
    for v, rs in by_v.items():
        exited = [r for r in rs if r["pos"][0] > 190 and r["fgnd"] >= 3]
        assert exited, f"vehicle {v} never reached the exit platform"
        parked = [r for r in exited if r["spd"] < 1.0 and r["brk"] > 0.9]
        assert len(parked) >= 2, f"vehicle {v} did not brake to a stop"
