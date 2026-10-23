"""Milestone 3.3: preset validation, persistence and application."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import preset_service  # noqa: E402


@pytest.fixture()
def preset_path(tmp_path, monkeypatch):
    path = tmp_path / "presets.json"
    monkeypatch.setattr(preset_service, "PRESETS_PATH", path)
    return path


def test_seed_builtins_once(preset_path):
    presets = preset_service.load_presets()
    assert [p["name"] for p in presets] == [
        "YouTube Shorts 9:16", "YouTube Landscape 16:9", "Square 1:1"]
    assert preset_path.exists()
    # reload again: built-ins are not duplicated
    presets2 = preset_service.load_presets()
    assert [p["name"] for p in presets2] == [p["name"] for p in presets]


def test_normalize_rejects_bad_values(preset_path):
    with pytest.raises(preset_service.PresetError):
        preset_service.normalize_preset({"name": "X", "aspect": "4:7"})
    with pytest.raises(preset_service.PresetError):
        preset_service.normalize_preset({"name": ""})
    with pytest.raises(preset_service.PresetError):
        preset_service.normalize_preset({"name": "X", "sfx_enabled": "yes"})
    with pytest.raises(preset_service.PresetError):
        preset_service.normalize_preset({"name": "X", "engine_volume": 1.5})
    with pytest.raises(preset_service.PresetError):
        preset_service.normalize_preset({"name": "X", "color": "red"})
    # supported keys pass through
    clean = preset_service.normalize_preset(
        {"name": "X", "aspect": "16:9", "resolution": "720p", "fps": 60,
         "quality": "low", "duration": "12.5", "camera_style": "single_wide",
         "music_volume": 0.2})
    assert clean["fps"] == 60 and clean["duration"] == 12.5
    assert clean["music_volume"] == 0.2


def test_duplicate_names_rejected(preset_path, tmp_path):
    presets = [preset_service.new_preset_from_config("Test A", {"aspect": "9:16"}),
               preset_service.new_preset_from_config("test a", {"aspect": "1:1"})]
    with pytest.raises(preset_service.PresetError):
        preset_service.save_presets(presets, preset_path)
    assert list(preset_path.with_suffix(".json.tmp").parent.glob("*.tmp")) == []


def test_save_load_roundtrip_is_atomic(preset_path):
    presets = preset_service.load_presets()
    custom = preset_service.new_preset_from_config(
        "My Preset", {"aspect": "1:1", "resolution": "720p", "fps": 60,
                      "quality": "low", "duration": 9, "engine_volume": 0.3})
    preset_service.save_presets(presets[:1] + [custom], preset_path)
    loaded = preset_service.load_presets()
    assert "My Preset" in [p["name"] for p in loaded]
    mine = preset_service.find_preset(loaded, "my preset")  # case-insensitive
    assert mine["fps"] == 60 and mine["aspect"] == "1:1"
    assert not preset_path.with_suffix(".json.tmp").exists()
    doc = json.loads(preset_path.read_text(encoding="utf-8"))
    assert doc["schema"] == "crs.presets/1"


def test_builtins_restored_after_manual_removal(preset_path):
    presets = preset_service.load_presets()
    preset_service.save_presets(presets[1:], preset_path)  # drop the first
    again = preset_service.load_presets()
    names = [p["name"] for p in again]
    assert "YouTube Shorts 9:16" in names and "YouTube Landscape 16:9" in names


def test_apply_preset_overrides_and_preserves_identity(preset_path):
    presets = preset_service.load_presets()
    cfg = {"template_id": "gap_jump", "seed": 5, "aspect": "16:9",
           "resolution": "1080p", "fps": 30, "quality": "medium",
           "duration": None, "vehicle_count": 2}
    square = preset_service.find_preset(presets, "Square 1:1")
    merged = preset_service.apply_preset(square, cfg)
    assert merged["aspect"] == "1:1"
    assert merged["template_id"] == "gap_jump" and merged["seed"] == 5
    assert merged["preset_name"] == "Square 1:1"


def test_corrupt_presets_file_raises(preset_path):
    preset_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(preset_service.PresetError):
        preset_service.load_presets()
