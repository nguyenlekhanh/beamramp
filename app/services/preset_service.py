"""Render presets: named, validated groups of supported output settings.

A preset stores only settings the renderer genuinely supports:
  aspect / resolution / fps / quality (+ optional out_dir, duration,
  camera_style and audio options). Template, seed and vehicle count stay
  in the variation builder, never inside presets.

Persistence: ``presets.json`` (atomic writes, schema ``crs.presets/1``).
Three built-in presets are seeded on first use but user presets always win.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRESETS_PATH = PROJECT_ROOT / "presets.json"

SCHEMA = "crs.presets/1"
MAX_NAME_LEN = 48
PRESET_KEYS = (
    "aspect", "resolution", "fps", "quality",
    "out_dir", "duration", "camera_style", "color",
    "sfx_enabled", "engine_volume", "sfx_volume",
    "music_enabled", "music_path", "music_volume",
)

BUILT_INS: List[Dict[str, Any]] = [
    {"name": "YouTube Shorts 9:16", "aspect": "9:16", "resolution": "1080p",
     "fps": 30, "quality": "medium"},
    {"name": "YouTube Landscape 16:9", "aspect": "16:9", "resolution": "1080p",
     "fps": 30, "quality": "medium"},
    {"name": "Square 1:1", "aspect": "1:1", "resolution": "1080p",
     "fps": 30, "quality": "medium"},
]


class PresetError(ValueError):
    """Raised on invalid preset definitions or persistence problems."""


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _clean_name(name: str) -> str:
    return str(name or "").strip()


def normalize_preset(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only supported keys; validate types and ranges."""
    if not isinstance(data, dict):
        raise PresetError("preset must be an object")
    name = _clean_name(str(data.get("name") or ""))
    if not name or len(name) > MAX_NAME_LEN:
        raise PresetError(f"preset name must be 1..{MAX_NAME_LEN} chars: {name!r}")
    out: Dict[str, Any] = {"name": name}
    for key in PRESET_KEYS:
        if key not in data or data[key] is None:
            continue
        out[key] = data[key]
    if out.get("fps") is not None:
        out["fps"] = int(out["fps"])
    if out.get("duration") is not None:
        out["duration"] = float(out["duration"])
    for key in ("sfx_enabled", "music_enabled"):
        if key in out and not isinstance(out[key], bool):
            raise PresetError(f"{key} must be a boolean")
    for key in ("engine_volume", "sfx_volume", "music_volume"):
        if key in out:
            try:
                val = float(out[key])
            except (TypeError, ValueError):
                raise PresetError(f"{key} must be a number")
            if not 0.0 <= val <= 1.0:
                raise PresetError(f"{key} must be within 0..1")
            out[key] = val
    for key in ("aspect", "resolution", "quality", "camera_style", "out_dir",
                "music_path"):
        if key in out and not isinstance(out[key], str):
            raise PresetError(f"{key} must be a string")
    if "aspect" in out and out["aspect"] not in ("9:16", "16:9", "1:1"):
        raise PresetError(f"unsupported preset aspect: {out['aspect']!r}")
    color = out.get("color")
    if color is not None and (not isinstance(color, str) or not color.startswith("#")
                              or len(color) not in (7, 4)):
        raise PresetError(f"color must be a #RRGGBB hex value (got {color!r})")
    return out


def load_presets(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = Path(path) if path else PRESETS_PATH
    user: List[Dict[str, Any]] = []
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise PresetError(f"presets file unreadable: {exc}") from exc
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            raise PresetError("unsupported presets schema")
        if not isinstance(doc.get("presets"), list):
            raise PresetError("presets must be a list")
        user = [normalize_preset(p) for p in doc["presets"]]
    else:
        user = [normalize_preset(dict(p)) for p in BUILT_INS]
        save_presets(user, path)
    names = {p["name"] for p in user}
    for built in BUILT_INS:
        if built["name"] not in names:
            user.insert(0, normalize_preset(dict(built)))
            save_presets(user, path)
            names.add(built["name"])
    return user


def save_presets(presets: List[Dict[str, Any]], path: Optional[Path] = None) -> Path:
    path = Path(path) if path else PRESETS_PATH
    cleaned = [normalize_preset(p) for p in presets]
    seen: set = set()
    for p in cleaned:
        if p["name"].lower() in seen:
            raise PresetError(f"duplicate preset name: {p['name']!r}")
        seen.add(p["name"].lower())
    _atomic_write(path, json.dumps(
        {"schema": SCHEMA, "presets": cleaned}, indent=2, ensure_ascii=False))
    return path


def find_preset(presets: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    name = _clean_name(name)
    for p in presets:
        if p["name"].lower() == name.lower():
            return p
    return None


def apply_preset(preset: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay preset values onto a render configuration (preset wins)."""
    merged = dict(cfg)
    for key in PRESET_KEYS:
        if key in preset and preset[key] is not None:
            merged[key] = preset[key]
    merged["preset_name"] = preset.get("name", "")
    return merged


def new_preset_from_config(name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Preset draft with the user-visible subset of the current UI config."""
    data: Dict[str, Any] = {"name": name}
    for key in PRESET_KEYS:
        if key == "color" and cfg.get(key) is None:
            continue
        if key == "music_path" and not cfg.get(key):
            continue
        if key in cfg and cfg[key] is not None:
            data[key] = cfg[key]
    return normalize_preset(data)
