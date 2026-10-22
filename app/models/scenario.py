"""ColorRush scenario data model and validation.

Scenario files are self-contained JSON documents (schema ``crs.scenario/1``)
that embed the full generated track layout. The Godot project only needs the
JSON file to build the scene, run the simulation, direct the camera and render.
"""

from __future__ import annotations

import copy
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "crs.scenario/1"

VEHICLE_TYPES = ("sports", "compact", "bus", "truck", "monster")

TRACK_TYPES = (
    "mega_ramp",
    "gap_jump",
    "obstacle_course",
    "monster_stunts",
    "hammer_challenge",
    "ball_dodge",
    "spiral_race",
    "bridge_jump",
    "crash_test",
    "random_stunt",
)

QUALITY_PRESETS = ("low", "medium", "high")
ASPECT_PRESETS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}

CAMERA_STYLES = ("cinematic_auto", "single_wide")


class ScenarioError(ValueError):
    """Raised when a scenario document fails validation."""


def _check_type(cond: bool, msg: str) -> None:
    if not cond:
        raise ScenarioError(msg)


def _check_hex_color(value: str, context: str) -> None:
    _check_type(isinstance(value, str), f"{context} must be a string")
    _check_type(value.startswith("#") and len(value) in (7, 4), f"{context} must be a #RRGGBB color: {value!r}")


def _check_vec3(value: Any, context: str) -> None:
    _check_type(
        isinstance(value, (list, tuple)) and len(value) == 3,
        f"{context} must be [x, y, z]",
    )
    for comp in value:
        _check_type(
            isinstance(comp, (int, float)) and math.isfinite(comp),
            f"{context} contains a non-finite component",
        )


@dataclass
class VehicleSpecModel:
    type: str = "sports"
    color: str = "#e63946"
    wheel_color: str = "#22223b"
    label: str = ""
    scale: float = 1.0
    mass_override: Optional[float] = None
    spawn_offset: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    spawn_yaw_deg: float = 0.0
    style: str = "balanced"  # cautious | balanced | aggressive

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "color": self.color,
            "wheel_color": self.wheel_color,
            "label": self.label,
            "scale": self.scale,
            "mass_override": self.mass_override,
            "spawn_offset": list(self.spawn_offset),
            "spawn_yaw_deg": self.spawn_yaw_deg,
            "style": self.style,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VehicleSpecModel":
        return cls(
            type=data.get("type", "sports"),
            color=data.get("color", "#e63946"),
            wheel_color=data.get("wheel_color", "#22223b"),
            label=data.get("label", ""),
            scale=float(data.get("scale", 1.0)),
            mass_override=data.get("mass_override"),
            spawn_offset=tuple(data.get("spawn_offset", (0.0, 0.0, 0.0))),
            spawn_yaw_deg=float(data.get("spawn_yaw_deg", 0.0)),
            style=data.get("style", "balanced"),
        )


@dataclass
class OutputModel:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    quality: str = "medium"
    out_dir: Optional[str] = None
    out_file: str = "colorrush_render.mp4"
    music_path: Optional[str] = None
    music_volume: float = 0.35
    music_enabled: bool = True
    engine_volume: float = 0.5
    sfx_volume: float = 0.8
    sfx_enabled: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "quality": self.quality,
            "out_dir": self.out_dir,
            "out_file": self.out_file,
            "music_path": self.music_path,
            "music_volume": self.music_volume,
            "music_enabled": self.music_enabled,
            "engine_volume": self.engine_volume,
            "sfx_volume": self.sfx_volume,
            "sfx_enabled": self.sfx_enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OutputModel":
        return cls(
            width=int(data.get("width", 1080)),
            height=int(data.get("height", 1920)),
            fps=int(data.get("fps", 30)),
            quality=data.get("quality", "medium"),
            out_dir=data.get("out_dir"),
            out_file=data.get("out_file", "colorrush_render.mp4"),
            music_path=data.get("music_path"),
            music_volume=float(data.get("music_volume", 0.35)),
            music_enabled=bool(data.get("music_enabled", True)),
            engine_volume=float(data.get("engine_volume", 0.5)),
            sfx_volume=float(data.get("sfx_volume", 0.8)),
            sfx_enabled=bool(data.get("sfx_enabled", True)),
        )


@dataclass
class ScenarioModel:
    name: str = "Untitled Scenario"
    seed: int = 0
    duration_seconds: float = 20.0
    camera_style: str = "cinematic_auto"
    slowmo_enabled: bool = True
    slowmo_factor: float = 0.4
    recovery_enabled: bool = True
    track_type: str = "mega_ramp"
    track_params: Dict[str, Any] = field(default_factory=dict)
    vehicles: List[VehicleSpecModel] = field(default_factory=list)
    output: OutputModel = field(default_factory=OutputModel)
    # Filled by the layout generator (deterministic from seed):
    layout: Dict[str, Any] = field(default_factory=dict)

    def to_document(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "name": self.name,
            "seed": self.seed,
            "duration_seconds": self.duration_seconds,
            "camera": {"style": self.camera_style},
            "sim": {
                "recovery": self.recovery_enabled,
                "slowmo": {"enabled": self.slowmo_enabled, "factor": self.slowmo_factor},
            },
            "track": {"type": self.track_type, "params": dict(self.track_params)},
            "vehicles": {
                "count": len(self.vehicles),
                "specs": [v.as_dict() for v in self.vehicles],
            },
            "output": self.output.as_dict(),
            "layout": copy.deepcopy(self.layout),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_document(), indent=indent)

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def from_document(cls, doc: Dict[str, Any]) -> "ScenarioModel":
        validate_document(doc)
        sim = doc.get("sim", {})
        slowmo = sim.get("slowmo", {})
        return cls(
            name=doc["name"],
            seed=int(doc["seed"]),
            duration_seconds=float(doc["duration_seconds"]),
            camera_style=doc.get("camera", {}).get("style", "cinematic_auto"),
            recovery_enabled=bool(sim.get("recovery", True)),
            slowmo_enabled=bool(slowmo.get("enabled", True)),
            slowmo_factor=float(slowmo.get("factor", 0.4)),
            track_type=doc.get("track", {}).get("type", "mega_ramp"),
            track_params=doc.get("track", {}).get("params", {}),
            vehicles=[VehicleSpecModel.from_dict(v) for v in doc.get("vehicles", {}).get("specs", [])],
            output=OutputModel.from_dict(doc.get("output", {})),
            layout=doc.get("layout", {}),
        )

    @classmethod
    def load(cls, path: Path) -> "ScenarioModel":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_document(doc)

    def validate(self) -> None:
        validate_document(self.to_document())


def validate_document(doc: Dict[str, Any]) -> None:
    """Validate a raw scenario document. Raises ScenarioError."""
    _check_type(isinstance(doc, dict), "scenario must be an object")
    schema = doc.get("schema")
    if schema != SCHEMA:
        raise ScenarioError(f"unsupported schema {schema!r}; expected {SCHEMA!r}")

    for key in ("name", "seed", "duration_seconds", "layout"):
        _check_type(key in doc, f"missing field: {key}")

    _check_type(isinstance(doc["name"], str) and doc["name"].strip(), "name must be a non-empty string")
    _check_type(isinstance(doc["seed"], int), "seed must be an integer")
    _check_type(isinstance(doc["layout"], dict), "layout must be an object")

    duration = doc["duration_seconds"]
    _check_type(isinstance(duration, (int, float)) and 2.0 <= duration <= 300.0, "duration_seconds must be within 2..300")

    camera = doc.get("camera", {})
    _check_type(isinstance(camera, dict), "camera must be an object")
    style = camera.get("style", "cinematic_auto")
    _check_type(style in CAMERA_STYLES, f"camera.style must be one of {CAMERA_STYLES}")

    sim = doc.get("sim", {})
    _check_type(isinstance(sim, dict), "sim must be an object")
    slowmo = sim.get("slowmo", {})
    _check_type(isinstance(slowmo, dict), "sim.slowmo must be an object")
    factor = slowmo.get("factor", 0.4)
    _check_type(isinstance(factor, (int, float)) and 0.1 <= factor <= 1.0, "sim.slowmo.factor must be within 0.1..1.0")

    track = doc.get("track", {})
    _check_type(isinstance(track, dict), "track must be an object")
    track_type = track.get("type", "mega_ramp")
    _check_type(track_type in TRACK_TYPES, f"track.type must be one of {TRACK_TYPES}")
    _check_type(isinstance(track.get("params", {}), dict), "track.params must be an object")

    vehicles = doc.get("vehicles", {})
    _check_type(isinstance(vehicles, dict) and isinstance(vehicles.get("specs", []), list), "vehicles.specs must be a list")
    specs = vehicles.get("specs", [])
    count = vehicles.get("count", len(specs))
    _check_type(len(specs) == count, "vehicles.count must match number of specs")
    _check_type(1 <= len(specs) <= 12, "1..12 vehicles supported")
    for i, spec in enumerate(specs):
        _check_type(isinstance(spec, dict), f"vehicles[{i}] must be an object")
        _check_type(spec.get("type", "sports") in VEHICLE_TYPES, f"vehicles[{i}].type invalid")
        _check_hex_color(spec.get("color", "#e63946"), f"vehicles[{i}].color")
        _check_hex_color(spec.get("wheel_color", "#22223b"), f"vehicles[{i}].wheel_color")
        _check_type(spec.get("style", "balanced") in ("cautious", "balanced", "aggressive"), f"vehicles[{i}].style invalid")
        scale = spec.get("scale", 1.0)
        _check_type(isinstance(scale, (int, float)) and 0.5 <= scale <= 3.0, f"vehicles[{i}].scale must be within 0.5..3.0")
        _check_vec3(spec.get("spawn_offset", (0, 0, 0)), f"vehicles[{i}].spawn_offset")

    output = doc.get("output", {})
    _check_type(isinstance(output, dict), "output must be an object")
    width = output.get("width", 1080)
    height = output.get("height", 1920)
    fps = output.get("fps", 30)
    for nm, val in (("width", width), ("height", height)):
        _check_type(isinstance(val, int) and 256 <= val <= 3840, f"output.{nm} must be an int within 256..3840")
    _check_type(isinstance(fps, int) and fps in (24, 30, 60), "output.fps must be one of 24/30/60")
    quality = output.get("quality", "medium")
    _check_type(quality in QUALITY_PRESETS, f"output.quality must be one of {QUALITY_PRESETS}")
    vol = output.get("music_volume", 0.35)
    _check_type(isinstance(vol, (int, float)) and 0.0 <= vol <= 1.0, "output.music_volume must be 0..1")
    for nm in ("engine_volume", "sfx_volume"):
        val = output.get(nm, 0.5 if nm == "engine_volume" else 0.8)
        _check_type(isinstance(val, (int, float)) and 0.0 <= val <= 1.0,
                    f"output.{nm} must be 0..1")
    _check_type(isinstance(output.get("music_enabled", True), bool),
                "output.music_enabled must be a boolean")
    _check_type(isinstance(output.get("sfx_enabled", True), bool),
                "output.sfx_enabled must be a boolean")
    music = output.get("music_path")
    if music is not None:
        _check_type(isinstance(music, str), "output.music_path must be a string or null")

    validate_layout(doc["layout"])


def validate_layout(layout: Dict[str, Any]) -> None:
    """Validate the embedded layout document produced by the layout generator."""
    _check_type(isinstance(layout, dict), "layout must be an object")
    for key in ("meta", "road", "obstacles", "zones", "spawns"):
        _check_type(key in layout, f"layout missing field: {key}")

    meta = layout["meta"]
    _check_type(isinstance(meta, dict) and "segment_count" in meta, "layout.meta must include segment_count")

    road = layout["road"]
    _check_type(isinstance(road, list) and road, "layout.road must be a non-empty list")
    for i, seg in enumerate(road):
        _check_type(isinstance(seg, dict), f"road[{i}] must be an object")
        for key in ("kind", "from", "size", "color"):
            _check_type(key in seg, f"road[{i}] missing {key}")
        _check_type(seg.get("kind") in ("platform", "ramp", "bridge", "cylinder", "gap_marker"), f"road[{i}].kind invalid")
        _check_vec3(seg["from"], f"road[{i}].from")
        _check_vec3(seg["size"], f"road[{i}].size")
        if seg.get("rot_deg") is not None:
            _check_vec3(seg.get("rot_deg"), f"road[{i}].rot_deg")

    obstacles = layout["obstacles"]
    _check_type(isinstance(obstacles, list), "layout.obstacles must be a list")
    for i, obs in enumerate(obstacles):
        _check_type(isinstance(obs, dict), f"obstacles[{i}] must be an object")
        _check_type(obs.get("type") in ("hammer", "ball", "cone", "barrier", "moving_platform", "rotator", "crash_wall"), f"obstacles[{i}].type invalid")
        _check_vec3(obs.get("at", (0, 0, 0)), f"obstacles[{i}].at")

    zones = layout["zones"]
    _check_type(isinstance(zones, list), "layout.zones must be a list")
    for i, zone in enumerate(zones):
        _check_type(isinstance(zone, dict), f"zones[{i}] must be an object")
        _check_type(zone.get("type") in ("launch", "finish", "checkpoint", "landing"), f"zones[{i}].type invalid")
        _check_vec3(zone.get("at", (0, 0, 0)), f"zones[{i}].at")

    spawns = layout["spawns"]
    _check_type(isinstance(spawns, list) and spawns, "layout.spawns must be a non-empty list")
    for i, spawn in enumerate(spawns):
        _check_vec3(spawn, f"spawns[{i}]")

    camera_hints = layout.get("camera_hints", {})
    _check_type(isinstance(camera_hints, dict), "layout.camera_hints must be an object")
    for key, value in camera_hints.items():
        _check_vec3(value, f"camera_hints.{key}")
