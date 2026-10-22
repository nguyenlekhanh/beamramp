"""Static scenario/config integrity tests for the cinematic camera milestone."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.track_layout import _HINT_KEYS  # noqa: E402
from app.services import scenario_service, render_service  # noqa: E402

TEMPLATE_IDS = [tpl["id"] for tpl in scenario_service.TEMPLATES]


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_all_templates_build_and_validate(template_id):
    sc = scenario_service.build_scenario(template_id, 12345)
    assert sc.duration_seconds >= 2.0
    layout = sc.layout
    assert layout.get("road") and layout.get("zones") is not None
    sc.validate()


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_camera_hint_keys_are_known(template_id):
    """Unknown hint keys would silently degrade the director's shot plan."""
    sc = scenario_service.build_scenario(template_id, 7)
    hints = (sc.layout or {}).get("camera_hints", {})
    assert "start" in hints, f"{template_id}: start hint missing"
    for key in hints:
        assert key in _HINT_KEYS, f"{template_id}: unknown hint {key!r}"


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_seeded_hints_are_deterministic(template_id):
    """Same seed -> same camera hints (shot plan determinism requirement)."""
    a = scenario_service.build_scenario(template_id, 4242)
    b = scenario_service.build_scenario(template_id, 4242)
    assert (a.layout or {}).get("camera_hints") == (b.layout or {}).get("camera_hints")


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_missing_camera_hints_keys_are_safe(template_id):
    """Templates without specific hints must not crash the shot plan builder;
    emulated here by unset hints keys that the GDScript treats as fallbacks."""
    sc = scenario_service.build_scenario(template_id, 99)
    hints = (sc.layout or {}).get("camera_hints", {})
    # remove optional hints; start/finish always present via the builder
    for optional in ("gap_mid", "landing", "mid1", "mid2", "ramp_top"):
        hints.pop(optional, None)
    # the Python-side validator must accept a stripped hint set
    sc.validate()


def test_portrait_and_landscape_sizes_both_supported():
    for aspect, res, w, h in (("9:16", "720p", 720, 1280), ("9:16", "1080p", 1080, 1920),
                              ("16:9", "720p", 1280, 720), ("16:9", "1080p", 1920, 1080),
                              ("1:1", "720p", 720, 720), ("1:1", "1080p", 1080, 1080)):
        assert render_service.resolve_output_size(aspect, res) == (w, h)
