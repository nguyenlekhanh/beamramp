"""Single-scenario render path for the UI (and tests).

Turns a configuration dict into a validated scenario, renders exactly one
MP4 through the existing godot_service + ffmpeg_service pipeline and verifies
the output. tools/run_all.py keeps its own independent batch workflow.
"""

from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..models.scenario import ScenarioModel
from . import ffmpeg_service
from . import godot_service
from .scenario_service import TEMPLATES, build_scenario

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = PROJECT_ROOT / "output" / "ui_renders"

VEHICLE_TYPES = ("sports", "compact", "bus", "truck", "monster")
SUPPORTED_FPS = (30, 60)
SUPPORTED_QUALITIES = ("low", "medium", "high")

# resolution presets per aspect: long-edge convention (720p/1080p)
ASPECT_SIZES: Dict[str, Dict[str, tuple]] = {
    "9:16": {"720p": (720, 1280), "1080p": (1080, 1920)},
    "16:9": {"720p": (1280, 720), "1080p": (1920, 1080)},
    "1:1": {"720p": (720, 720), "1080p": (1080, 1080)},
}


class ConfigError(ValueError):
    """Raised when a user configuration is invalid."""


def template_by_id(template_id: str) -> Optional[Dict[str, Any]]:
    for tpl in TEMPLATES:
        if tpl["id"] == template_id:
            return tpl
    return None


def resolve_output_size(aspect: str, res_preset: str) -> tuple:
    entry = ASPECT_SIZES.get(aspect)
    if entry is None:
        raise ConfigError(f"unsupported aspect: {aspect!r}")
    size = entry.get(res_preset)
    if size is None:
        raise ConfigError(f"unsupported resolution preset: {res_preset!r}")
    return size


def validate_config(cfg: Dict[str, Any]) -> None:
    tpl = template_by_id(str(cfg.get("template_id", "")))
    if tpl is None:
        raise ConfigError(f"unknown template: {cfg.get('template_id')!r}")

    count = int(cfg.get("vehicle_count") or tpl["vehicles"])
    max_v = int(tpl.get("max_vehicles", 6))
    if not 1 <= count <= max_v:
        raise ConfigError(f"{tpl['name']}: vehicle count must be within 1..{max_v} (got {count})")

    duration = float(cfg.get("duration") or tpl["duration"])
    if not 2.0 <= duration <= 300.0:
        raise ConfigError(f"duration must be within 2..300 seconds (got {duration})")

    if cfg.get("vehicle_type") and cfg["vehicle_type"] not in VEHICLE_TYPES:
        raise ConfigError(f"unknown vehicle type: {cfg['vehicle_type']!r}")

    fps = int(cfg.get("fps") or 30)
    if fps not in SUPPORTED_FPS:
        raise ConfigError(f"fps must be one of {SUPPORTED_FPS} (got {fps})")

    if cfg.get("quality") not in SUPPORTED_QUALITIES:
        raise ConfigError(f"quality must be one of {SUPPORTED_QUALITIES} (got {cfg.get('quality')!r})")

    resolve_output_size(str(cfg.get("aspect", "9:16")), str(cfg.get("resolution", "1080p")))

    color = cfg.get("color")
    if color is not None and (not isinstance(color, str) or not color.startswith("#") or len(color) not in (7, 4)):
        raise ConfigError(f"primary color must be a #RRGGBB hex value (got {color!r})")

    seed = int(cfg.get("seed") or 12345)
    if not 0 <= seed <= 10_000_000:
        raise ConfigError("seed must be within 0..10000000")

    for nm in ("engine_volume", "sfx_volume", "music_volume"):
        val = cfg.get(nm)
        if val is not None:
            fv = float(val)
            if not 0.0 <= fv <= 1.0:
                raise ConfigError(f"{nm} must be within 0..1 (got {fv})")
    for nm in ("sfx_enabled", "music_enabled"):
        if not isinstance(cfg.get(nm, True), bool):
            raise ConfigError(f"{nm} must be a boolean (got {cfg.get(nm)!r})")
    music = cfg.get("music_path")
    if music is not None and not isinstance(music, str):
        raise ConfigError("music_path must be a string path or null")

    raw_dir = str(cfg.get("out_dir") or "").strip()
    if not raw_dir:
        raise ConfigError("output folder is empty")
    out_dir = Path(raw_dir)


def default_output_name(cfg: Dict[str, Any]) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tpl = template_by_id(str(cfg.get("template_id", "")))
    ident = tpl["id"] if tpl else "scenario"
    base = str(cfg.get("file_name") or "").strip()
    if not base:
        base = f"{ident}_{stamp}"
    if not base.lower().endswith(".mp4"):
        base += ".mp4"
    return base


def build_scenario_from_config(cfg: Dict[str, Any]) -> ScenarioModel:
    """Validated scenario model from a UI configuration dict."""
    validate_config(cfg)
    tpl = template_by_id(str(cfg["template_id"]))
    seed = int(cfg.get("seed") or 12345)
    count = int(cfg.get("vehicle_count") or tpl["vehicles"])
    duration = float(cfg.get("duration") or tpl["duration"])
    w, h = resolve_output_size(str(cfg["aspect"]), str(cfg["resolution"]))
    fps = int(cfg.get("fps") or 30)
    quality = str(cfg.get("quality") or "medium")
    vtype = cfg.get("vehicle_type") or None
    color = cfg.get("color") or None

    sc = build_scenario(
        tpl["id"], seed,
        vehicle_count=count, duration=duration,
        camera_style="cinematic_auto",
        vehicle_type=vtype,
        output_overrides={
            "width": w, "height": h, "fps": fps, "quality": quality,
            "out_file": default_output_name(cfg),
            "music_path": cfg.get("music_path"),
            "music_enabled": bool(cfg.get("music_enabled", True)),
            "music_volume": float(cfg.get("music_volume", 0.35)),
            "engine_volume": float(cfg.get("engine_volume", 0.5)),
            "sfx_volume": float(cfg.get("sfx_volume", 0.8)),
            "sfx_enabled": bool(cfg.get("sfx_enabled", True)),
        },
    )
    if color and sc.vehicles:
        sc.vehicles[0].color = color
    return sc


def audio_cfg_from_scenario(sc: "ScenarioModel") -> Dict[str, Any]:
    """Audio post-mix config from a scenario model, for direct run_render calls."""
    music_path = (sc.output.music_path or "").strip()
    music_ok = bool(music_path) and Path(music_path).exists() \
        and bool(sc.output.music_enabled)
    return {
        "duration": float(sc.duration_seconds),
        "sfx_enabled": bool(sc.output.sfx_enabled),
        "engine_volume": float(sc.output.engine_volume),
        "sfx_volume": float(sc.output.sfx_volume),
        "music_enabled": music_ok,
        "music_path": Path(music_path) if music_ok else None,
        "music_volume": float(sc.output.music_volume),
    }


def run_single_render(cfg: Dict[str, Any], *,
                      log_cb: Optional[Callable[[str], None]] = None,
                      progress_cb: Optional[Callable[[Optional[int]], None]] = None,
                      cancel_event=None,
                      overwrite: bool = False) -> Dict[str, Any]:
    """Render exactly one MP4 from config. Returns a result dict with 'mp4_path'."""
    if log_cb is None:
        def log_cb(line: str) -> None:
            pass

    sc = build_scenario_from_config(cfg)
    out_dir = Path(cfg.get("out_dir") or DEFAULT_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = default_output_name(cfg)
    mp4 = out_dir / out_file
    if mp4.exists() and not overwrite:
        # never overwrite silently; bump the name instead
        stem, ext = mp4.stem, mp4.suffix
        for i in range(1, 500):
            candidate = out_dir / f"{stem}_{i}{ext}"
            if not candidate.exists():
                mp4 = candidate
                break
        else:
            raise ConfigError(f"could not find a free output name for {out_file}")

    # reproducible job spec written next to the video
    save_scenario_file = out_dir / (mp4.stem + ".scenario.json")
    sc.save(save_scenario_file)

    fps = int(cfg.get("fps") or 30)
    expected_frames = fps * float(sc.duration_seconds)
    wrapped_progress = progress_cb

    def _progress(frames: Optional[int]) -> None:
        if wrapped_progress and frames:
            wrapped_progress(frames / expected_frames)
        elif wrapped_progress:
            pass

    def _log(line: str) -> None:
        log_cb(line)

    log_cb(f"[job] rendering template '{cfg.get('template_id')}' -> {mp4.name}")
    music_path = (sc.output.music_path or "").strip()
    if music_path and not Path(music_path).exists():
        log_cb(f"[audio] music file not found ({music_path}); exporting without music")
        music_path = ""
    audio_cfg = audio_cfg_from_scenario(sc)
    info = godot_service.run_render(
        save_scenario_file, mp4,
        output_w=sc.output.width, output_h=sc.output.height,
        fps=fps, quality=str(cfg.get("quality") or "medium"),
        allow_audio=False, movie_format="avi",
        progress_cb=_progress if wrapped_progress else None,
        cancel_event=cancel_event,
        audio=audio_cfg,
    )
    log_cb("[job] encoding finished; verifying MP4 ...")
    err = ffmpeg_service.verify_output(
        mp4, expected_w=sc.output.width, expected_h=sc.output.height,
        expected_fps=fps, min_duration=min(2.0, float(sc.duration_seconds) * 0.5),
        want_audio=info.get("has_audio", False) or False)
    if err:
        raise RuntimeError(f"output verification failed: {err}")
    log_cb("[job] VERIFY OK")
    return {
        "mp4_path": str(mp4),
        "scenario_path": str(save_scenario_file),
        "duration": info["duration"],
        "width": sc.output.width,
        "height": sc.output.height,
        "fps": fps,
        "has_audio": info["has_audio"],
        "events_path": info.get("events_path"),
        "audio_bed": info.get("audio_bed"),
        "elapsed": info.get("elapsed"),
    }
