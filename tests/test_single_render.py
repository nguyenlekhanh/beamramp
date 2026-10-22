"""Single-template rendering through the same path the UI uses (slow test)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import godot_service  # noqa: E402
from app.services.ffmpeg_service import get_tools  # noqa: E402
from app.services.render_service import ConfigError, run_single_render  # noqa: E402


@pytest.fixture(scope="module")
def tmp_out(tmp_path_factory):
    return tmp_path_factory.mktemp("ui_renders")


@pytest.mark.slow
def test_single_template_render_and_verify(tmp_out):
    """Render crash_test at low quality; mirrors the UI's render flow."""
    if godot_service.find_godot() is None:
        pytest.skip("Godot binary not found under ./tools")
    if get_tools().get("ffmpeg") is None or get_tools().get("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available on PATH")
    cfg = {
        "template_id": "crash_test",
        "vehicle_count": 1,
        "duration": 8,
        "aspect": "9:16",
        "resolution": "720p",
        "fps": 30,
        "quality": "low",
        "seed": 777,
        "color": "#e63946",
        "out_dir": str(tmp_out),
    }
    result = run_single_render(cfg)
    mp4 = Path(result["mp4_path"])
    assert mp4.exists() and mp4.stat().st_size > 1000
    assert 6.0 <= result["duration"] <= 12.0
    assert result["width"] == 720 and result["height"] == 1280
    # no silent overwrite: a second job must produce a new file name
    result2 = run_single_render(cfg)
    assert Path(result2["mp4_path"]) != mp4
    assert Path(result2["mp4_path"]).exists()


def test_invalid_config_writes_nothing(tmp_path):
    """Validation errors never touch the filesystem."""
    cfg = {"template_id": "crash_test", "out_dir": str(tmp_path), "fps": 999,
           "duration": 8, "resolution": "720p", "aspect": "9:16", "quality": "low"}
    with pytest.raises(ConfigError):
        run_single_render(cfg)
    assert list(tmp_path.glob("*.mp4")) == []
