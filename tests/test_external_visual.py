"""M3.6B tests: external vehicle visual (opt-in GLB body replacement).

The visual is enabled purely through environment variables; the default
procedural car pipeline must be untouched when they are absent. Unit tests
cover the python-side env helper; integration tests run REAL headless sims
and assert the [CRS asset] log lines + fallback behavior.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services import asset_service  # noqa: E402
from app.services import godot_service  # noqa: E402
from app.services import scenario_service  # noqa: E402
from app.services.asset_service import AssetError  # noqa: E402
from tests.test_asset_service import build_minimal_glb  # noqa: E402

_GLB_ENV_KEYS = (
    "CRS_VEHICLE_GLB", "CRS_VEHICLE_GLB_SCALE", "CRS_VEHICLE_GLB_YAW_DEG",
    "CRS_VEHICLE_GLB_PITCH_DEG", "CRS_VEHICLE_GLB_AUTO_GROUND",
    "CRS_VEHICLE_GLB_WHEELS",
)


@pytest.fixture(autouse=True)
def _no_stray_visual_env(monkeypatch):
    """Never leak visual env between tests; default sim must stay default."""
    for key in _GLB_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fake_import(monkeypatch):
    monkeypatch.setattr(asset_service, "import_assets",
                        lambda *a, **k: {"ok": True, "returncode": 0,
                                         "elapsed": 0.5, "log_tail": []})


@pytest.fixture
def project_assets_dir(monkeypatch):
    """Throwaway dir INSIDE the godot project so the importer sees it."""
    d = godot_service.GODOT_PROJECT / "assets" / "user_assets_test"
    monkeypatch.setattr(asset_service, "USER_ASSETS_DIR", d)
    yield d
    if d.exists():
        for p in sorted(d.glob("*"), reverse=True):
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
        try:
            d.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# unit: prepare_visual_env
# ---------------------------------------------------------------------------

def test_prepare_visual_env_defaults(project_assets_dir, fake_import, tmp_path):
    model = build_minimal_glb(tmp_path / "my_car.glb")
    env = asset_service.prepare_visual_env(str(model))
    assert env["CRS_VEHICLE_GLB"].startswith(
        "res://assets/user_assets_test/my_car.glb")
    assert env["CRS_VEHICLE_GLB_SCALE"] == "1"
    assert env["CRS_VEHICLE_GLB_YAW_DEG"] == "0"
    assert env["CRS_VEHICLE_GLB_PITCH_DEG"] == "0"
    assert env["CRS_VEHICLE_GLB_AUTO_GROUND"] == "1"
    assert env["CRS_VEHICLE_GLB_WHEELS"] == "keep"


def test_prepare_visual_env_offsets_and_apply(project_assets_dir, fake_import,
                                              tmp_path, monkeypatch):
    model = build_minimal_glb(tmp_path / "my_car.glb")
    env = asset_service.prepare_visual_env(
        str(model), scale=1.5, yaw_deg=180.0, pitch_deg=5.0,
        auto_ground=False, wheel_mode="hide", apply=True)
    for key, val in env.items():
        assert os.environ[key] == val
    assert env["CRS_VEHICLE_GLB_SCALE"] == "1.5"
    assert env["CRS_VEHICLE_GLB_YAW_DEG"] == "180"
    assert env["CRS_VEHICLE_GLB_AUTO_GROUND"] == "0"
    assert env["CRS_VEHICLE_GLB_WHEELS"] == "hide"


def test_prepare_visual_env_scale_clamped(project_assets_dir, fake_import,
                                          tmp_path):
    model = build_minimal_glb(tmp_path / "big.glb")
    env = asset_service.prepare_visual_env(str(model), scale=100.0)
    assert env["CRS_VEHICLE_GLB_SCALE"] == "20"
    env = asset_service.prepare_visual_env(str(model), scale=0.001)
    assert env["CRS_VEHICLE_GLB_SCALE"] == "0.05"


def test_prepare_visual_env_rejects_bad_input(tmp_path):
    with pytest.raises(AssetError):
        asset_service.prepare_visual_env(str(tmp_path / "missing.glb"))
    gtlf = tmp_path / "a.gltf"
    gtlf.write_text("{}")
    with pytest.raises(AssetError):
        asset_service.prepare_visual_env(str(gtlf))
    model = build_minimal_glb(tmp_path / "a.glb")
    with pytest.raises(AssetError):
        asset_service.prepare_visual_env(str(model), wheel_mode="explode")


def test_prepare_visual_env_requires_import_ok(project_assets_dir,
                                               monkeypatch, tmp_path):
    monkeypatch.setattr(asset_service, "import_assets",
                        lambda *a, **k: {"ok": False, "returncode": 1,
                                         "elapsed": 1.0, "log_tail": ["boom"]})
    model = build_minimal_glb(tmp_path / "a.glb")
    with pytest.raises(AssetError):
        asset_service.prepare_visual_env(str(model))


# ---------------------------------------------------------------------------
# integration: real headless sims (godot gated)
# ---------------------------------------------------------------------------

skip_no_godot = pytest.mark.skipif(
    godot_service.find_godot() is None,
    reason="Godot binary not found under tools/ (real integration test)")


def _probe_scenario(tmp_path, duration=6.0, vehicles=2):
    sm = scenario_service.build_scenario("gap_jump", 9001,
                                         vehicle_count=vehicles,
                                         duration=duration)
    sm.output.width, sm.output.height = 320, 180
    sm.output.quality = "low"
    return scenario_service.save_scenario_file(
        sm, tmp_path / f"probe_v{vehicles}_{duration}.json")


def _asset_asserts(res, duration, expect_fail):
    assert res["script_errors"] == [], res["log_full"][-3000:]
    assert res["exit_code"] == 0
    assert res["sim_time"] >= duration - 0.5
    lines = [ln for ln in res["log_full"].splitlines()
             if "[CRS asset]" in ln]
    assert lines, "expected [CRS asset] log lines\n" + res["log_full"][-2000:]
    return lines


@skip_no_godot
def test_external_visual_enabled_probe(project_assets_dir, tmp_path,
                                       monkeypatch):
    """Opt-in visual: GLB applied, procedural body hidden, sim completes."""
    model = build_minimal_glb(tmp_path / "stub_car.glb")
    env = asset_service.prepare_visual_env(str(model), log_cb=print)
    assert env["CRS_VEHICLE_GLB"]
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    scenario_path = _probe_scenario(tmp_path)
    res = godot_service.run_sim_probe(scenario_path, timeout=300)
    lines = _asset_asserts(res, 6.0, expect_fail=False)
    joined = "\n".join(lines)
    assert "external vehicle visual: glb=" in joined
    assert "procedural body part" in joined
    assert "FAILED" not in joined
    assert "model wheel layout unverified" in joined
    assert "auto-grounded" in joined


@skip_no_godot
def test_external_visual_missing_file_fallback(tmp_path):
    """Bad env path must NOT stop the sim; procedural car stays in use."""
    monkey_env = {"CRS_VEHICLE_GLB": "res://assets/no_such_asset.glb"}
    old = {k: os.environ.get(k) for k in monkey_env}
    os.environ.update(monkey_env)
    try:
        scenario_path = _probe_scenario(tmp_path)
        res = godot_service.run_sim_probe(scenario_path, timeout=300)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    lines = _asset_asserts(res, 6.0, expect_fail=True)
    joined = "\n".join(lines)
    assert "FAILED(" in joined and "procedural fallback" in joined


@skip_no_godot
def test_default_pipeline_has_no_asset_logs(tmp_path):
    """No visual env set: sim is unchanged and prints no [CRS asset] lines."""
    scenario_path = _probe_scenario(tmp_path, duration=4.0)
    res = godot_service.run_sim_probe(scenario_path, timeout=300)
    assert res["script_errors"] == [], res["log_full"][-3000:]
    assert res["exit_code"] == 0
    assert not any("[CRS asset]" in ln
                   for ln in res["log_full"].splitlines()), res["log_full"]
