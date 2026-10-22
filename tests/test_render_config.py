"""Tests for configuration generation and validation (Milestone 2)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.render_service import (  # noqa: E402
    ConfigError, SUPPORTED_FPS, build_scenario_from_config, resolve_output_size,
    template_by_id, validate_config,
)
from app.services.scenario_service import TEMPLATES  # noqa: E402


def _base_cfg(**over):
    cfg = {
        "template_id": "crash_test",
        "vehicle_count": 2,
        "vehicle_type": None,
        "duration": 10,
        "aspect": "9:16",
        "resolution": "720p",
        "fps": 30,
        "quality": "low",
        "seed": 12345,
        "color": "#1d71f2",
        "out_dir": "output/ui_renders",
    }
    cfg.update(over)
    return cfg


def test_every_template_builds_and_validates():
    for tpl in TEMPLATES:
        cfg = _base_cfg(template_id=tpl["id"], vehicle_count=tpl["vehicles"],
                        duration=float(tpl["duration"]))
        sc = build_scenario_from_config(cfg)
        assert sc.track_type == tpl["id"]
        assert len(sc.vehicles) == tpl["vehicles"]
        sc.validate()


def test_output_sizes_follow_aspect_and_resolution():
    assert resolve_output_size("9:16", "1080p") == (1080, 1920)
    assert resolve_output_size("9:16", "720p") == (720, 1280)
    assert resolve_output_size("16:9", "1080p") == (1920, 1080)
    assert resolve_output_size("16:9", "720p") == (1280, 720)
    assert resolve_output_size("1:1", "720p") == (720, 720)
    with pytest.raises(ConfigError):
        resolve_output_size("4:3", "720p")
    with pytest.raises(ConfigError):
        resolve_output_size("9:16", "4K")


def test_scenario_output_matches_config():
    cfg = _base_cfg(aspect="16:9", resolution="1080p", fps=30)
    sc = build_scenario_from_config(cfg)
    assert sc.output.width == 1920 and sc.output.height == 1080
    assert sc.output.fps == 30


def test_primary_color_override_applied():
    sc = build_scenario_from_config(_base_cfg(color="#123456"))
    assert sc.vehicles[0].color == "#123456"


def test_vehicle_type_override_non_mixed():
    cfg = _base_cfg(template_id="crash_test", vehicle_type="monster")
    sc = build_scenario_from_config(cfg)
    assert all(v.type == "monster" for v in sc.vehicles)


def test_mixed_template_keeps_mixed_when_no_override():
    cfg = _base_cfg(template_id="random_stunt", vehicle_type=None, vehicle_count=4)
    sc = build_scenario_from_config(cfg)
    types = {v.type for v in sc.vehicles}
    assert len(types) >= 2  # mixed lineup uses several vehicle kinds


@pytest.mark.parametrize("over, err", [
    ({"template_id": "nope"}, "unknown template"),
    ({"vehicle_count": 99}, "must be within"),
    ({"duration": 1}, "duration"),
    ({"fps": 45}, "fps"),
    ({"quality": "ultra"}, "quality"),
    ({"aspect": "4:3"}, "aspect|resolution"),
    ({"vehicle_type": "racecar"}, "vehicle type"),
    ({"color": "red"}, "hex"),
    ({"seed": -5}, "seed"),
])
def test_validate_config_rejects_bad_values(over, err):
    with pytest.raises(ConfigError) as excinfo:
        validate_config(_base_cfg(**over))
    assert any(part in str(excinfo.value).lower()
               for part in err.split("|")), str(excinfo.value)


def test_validate_config_counts_are_template_bounded():
    for tpl in TEMPLATES:
        max_v = tpl.get("max_vehicles", 6)
        good = _base_cfg(template_id=tpl["id"], vehicle_count=max_v)
        validate_config(good)
        with pytest.raises(ConfigError):
            validate_config(_base_cfg(template_id=tpl["id"], vehicle_count=max_v + 1))


def test_supported_fps_constant():
    assert 30 in SUPPORTED_FPS and 60 in SUPPORTED_FPS


def test_template_lookup():
    assert template_by_id("crash_test")["duration"] == 18
    assert template_by_id("nope") is None
