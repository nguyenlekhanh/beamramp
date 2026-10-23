"""Persistent UI settings (ui_settings.json in the project root)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = PROJECT_ROOT / "ui_settings.json"

DEFAULTS: Dict[str, Any] = {
    "template_id": "mega_ramp",
    "aspect": "9:16",
    "resolution": "1080p",
    "fps": 30,
    "quality": "medium",
    "camera_style": "cinematic_auto",
    "duration": None,          # None = template default
    "vehicle_count": None,     # None = template default
    "vehicle_type": None,      # None = template default
    "color": None,             # None = seeded palette
    "seed": 12345,
    "out_dir": str(Path(__file__).resolve().parents[2] / "output" / "ui_renders"),
    "file_name": "",
}


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(path) if path else SETTINGS_PATH
    data: Dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    return merged


def save_settings(values: Dict[str, Any], path: Optional[Path] = None) -> Path:
    path = Path(path) if path else SETTINGS_PATH
    merged = load_settings(path)
    merged.update({k: v for k, v in values.items() if k in DEFAULTS})
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return path
