"""M3.7 tests: modular road & environment decorations (CRS_TRACK_DECO).

Guarantees verified here:
- default OFF: no [CRS env] log lines, sim unchanged,
- opt-in ON: decorations log, sim completes, no script errors,
- physics identity: vehicle position traces match within a small tolerance
  with deco OFF vs ON for the same scenario/seed (decorations must add no
  collision; exact equality is impossible because the sim itself drifts
  ~0.001 m between two identical OFF/OFF runs).
"""
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services import godot_service  # noqa: E402
from app.services import scenario_service  # noqa: E402

_DECO = "CRS_TRACK_DECO"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(_DECO, raising=False)
    # CRITICAL for the identity test: the default [CRS dbg] trace only prints
    # vehicles[0]; DBG_ALL prints one line per vehicle every 0.5 s.
    monkeypatch.setenv("CRS_DBG_ALL", "1")


skip_no_godot = pytest.mark.skipif(
    godot_service.find_godot() is None,
    reason="Godot binary not found under tools/ (real integration test)")


def _probe_scenario(tmp_path, template="gap_jump", seed=9001, duration=8.0,
                    vehicles=2):
    sm = scenario_service.build_scenario(template, seed,
                                         vehicle_count=vehicles,
                                         duration=duration)
    sm.output.width, sm.output.height = 320, 180
    sm.output.quality = "low"
    return scenario_service.save_scenario_file(
        sm, tmp_path / f"deco_probe_{template}_{seed}_{duration}.json")


def _trace_lines(log_full):
    return [ln for ln in log_full.splitlines() if "[CRS all t=" in ln]


# [CRS all t=7.01] v0 pos=(151.132706, 4.810231, -1.431315) spd=19.2 fgnd=4
# thr=1.0 brk=0.0 steer=-0.07 | v1 pos=(...) ...
_VEHICLE_RE = re.compile(
    r"v(\d+) pos=\(([^)]*)\) spd=(-?[\d.]+) fgnd=(\d+)"
    r" thr=(-?[\d.]+) brk=(-?[\d.]+) steer=(-?[\d.]+)")
_T_RE = re.compile(r"t=(-?[\d.]+)\]")

# Tolerances (~10x the observed OFF-vs-OFF jitter of ~0.0007 m; still orders
# of magnitude below any collision-scale deflection, which would be meters):
_POS_TOL = 0.01
_SPD_TOL = 0.5
_CTRL_TOL = 0.05


def _parse_trace(lines):
    """Parse DBG_ALL trace lines -> [(t, [per-vehicle tuples])]."""
    parsed = []
    for ln in lines:
        mt = _T_RE.search(ln)
        if not mt:
            continue
        cars = []
        for m in _VEHICLE_RE.finditer(ln):
            x, y, z = (float(p) for p in m.group(2).split(","))
            cars.append((m.group(1), (x, y, z), float(m.group(3)),
                         int(m.group(4)), float(m.group(5)),
                         float(m.group(6)), float(m.group(7))))
        parsed.append((mt.group(1), cars))
    return parsed


def _asset_lines(log_full):
    return [ln for ln in log_full.splitlines() if "[CRS env]" in ln]


@skip_no_godot
def test_default_off_has_no_deco_logs(tmp_path):
    """No CRS_TRACK_DECO -> track behaves exactly as before (no deco logs)."""
    sp = _probe_scenario(tmp_path, duration=5.0)
    res = godot_service.run_sim_probe(sp, timeout=300)
    assert res["script_errors"] == [], res["log_full"][-2000:]
    assert res["exit_code"] == 0
    assert res["sim_time"] >= 5.0 - 0.5
    assert _asset_lines(res["log_full"]) == [], res["log_full"][-2000:]


@skip_no_godot
def test_deco_enabled_probe(tmp_path, monkeypatch):
    """CRS_TRACK_DECO=1 -> deco logs present, sim completes cleanly."""
    monkeypatch.setenv(_DECO, "1")
    sp = _probe_scenario(tmp_path, duration=5.0)
    res = godot_service.run_sim_probe(sp, timeout=300)
    assert res["script_errors"] == [], res["log_full"][-3000:]
    assert res["exit_code"] == 0
    assert res["sim_time"] >= 5.0 - 0.5
    lines = _asset_lines(res["log_full"])
    assert len(lines) == 1, lines  # logged once per build_all
    assert "shoulder strip" in lines[0]
    assert "tree(s)" in lines[0]
    assert "collision off" in lines[0]


@skip_no_godot
def test_deco_off_on_physics_identity(tmp_path, monkeypatch):
    """Same scenario/seed: deco OFF vs ON produce the same trajectory.

    Decorations are MeshInstance-only (zero colliders), so vehicle traces
    must match within a small tolerance. Exact equality is NOT asserted:
    a control experiment (two identical deco-OFF runs, temp/mil37_ctrl.py)
    showed 3 of 16 trace lines differing by ~0.0007 m — inherent sim float
    jitter, unrelated to deco. _POS_TOL is ~10x that jitter but far below
    any real collision deflection (an unwanted contact would move the car
    by meters, and fgnd (grounded wheel count) is still compared exactly
    because deco can never add a collider under the road)."""
    sp = _probe_scenario(tmp_path, duration=8.0)
    monkeypatch.setenv(_DECO, "0")
    off = godot_service.run_sim_probe(sp, timeout=300)
    monkeypatch.setenv(_DECO, "1")
    on = godot_service.run_sim_probe(sp, timeout=300)
    assert off["script_errors"] == [] and on["script_errors"] == []
    p_off = _parse_trace(_trace_lines(off["log_full"]))
    p_on = _parse_trace(_trace_lines(on["log_full"]))
    assert p_off, "no trace lines captured (probe broken)"
    assert len(p_off) == len(p_on), \
        f"trace sample count changed: {len(p_off)} vs {len(p_on)}"
    for (t_off, cars_off), (t_on, cars_on) in zip(p_off, p_on):
        assert t_off == t_on, f"trace timestamp mismatch: {t_off} vs {t_on}"
        assert len(cars_off) == len(cars_on), \
            f"vehicle count in trace changed at t={t_off}"
        for c_off, c_on in zip(cars_off, cars_on):
            assert c_off[0] == c_on[0], "vehicle order changed in trace"
            worst = max(abs(a - b) for a, b in zip(c_off[1], c_on[1]))
            assert worst <= _POS_TOL, \
                f"position drift {worst:.4f} m > {_POS_TOL} at t={t_off}: " \
                f"{c_off} vs {c_on}"
            assert abs(c_off[2] - c_on[2]) <= _SPD_TOL, \
                f"speed drift at t={t_off}: {c_off} vs {c_on}"
            assert c_off[3] == c_on[3], \
                f"grounded wheel count changed at t={t_off}: " \
                f"{c_off} vs {c_on}"
            for j in (4, 5, 6):
                assert abs(c_off[j] - c_on[j]) <= _CTRL_TOL, \
                    f"AI control changed at t={t_off}: {c_off} vs {c_on}"
    # events identical too
    assert off["events_count"] == on["events_count"] >= 0


@skip_no_godot
def test_deco_on_straight_track(tmp_path, monkeypatch):
    """Long straight template also decorates cleanly (obstacle_course)."""
    monkeypatch.setenv(_DECO, "1")
    sp = _probe_scenario(tmp_path, template="obstacle_course", seed=4242,
                         duration=6.0, vehicles=3)
    res = godot_service.run_sim_probe(sp, timeout=300)
    assert res["script_errors"] == [], res["log_full"][-3000:]
    assert res["exit_code"] == 0
    lines = _asset_lines(res["log_full"])
    assert len(lines) == 1 and "tree(s)" in lines[0]
