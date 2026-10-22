"""Scenario lifecycle service: templates, seeded generation, save/load, validation."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.scenario import ScenarioError, ScenarioModel
from ..models.track_layout import build_layout

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIOS_DIR = PROJECT_ROOT / "scenarios"

VEHICLE_PALETTE = ["#e63946", "#1d71f2", "#ffd166", "#2a9d61", "#8338ec", "#ff8c1a",
                   "#ff595e", "#06d6a0", "#118ab2", "#fb5607"]

# Human-facing template metadata (id, display name, description, default duration,
# vehicle count/type and max supported vehicles).
TEMPLATES: List[Dict[str, Any]] = [
    {"id": "mega_ramp", "name": "Rainbow Mega Ramp", "duration": 22, "vehicles": 6,
     "vehicle_type": "sports", "icon": "\U0001F6D9", "max_vehicles": 6,
     "description": "Six-car launch down a huge rainbow ramp with a giant gap, "
                    "boost pads and flying gravel piles. The classic spectacle opener."},
    {"id": "gap_jump", "name": "Giant Gap Jump", "duration": 20, "vehicles": 3,
     "vehicle_type": "sports", "icon": "\u2B50", "max_vehicles": 6,
     "description": "Three speeding cars hit a long ramp and try to clear a massive "
                    "gap onto the landing platform. High-flying suspense."},
    {"id": "obstacle_course", "name": "Colorful Obstacle Course", "duration": 30, "vehicles": 4,
     "vehicle_type": "compact", "icon": "\U0001F9EC", "max_vehicles": 6,
     "description": "Four compact cars weave through rotating arms, hammers and "
                    "barriers on a long raid course. Chaos and dodges throughout."},
    {"id": "monster_stunts", "name": "Monster Truck Stunts", "duration": 26, "vehicles": 4,
     "vehicle_type": "monster", "icon": "\U0001F949", "max_vehicles": 4,
     "description": "Monster trucks jump a steep ramp, crush cars in the crush zone "
                    "and smash through stacked barriers."},
    {"id": "hammer_challenge", "name": "Moving Hammer Challenge", "duration": 28, "vehicles": 3,
     "vehicle_type": "compact", "icon": "\U0001F528", "max_vehicles": 6,
     "description": "Giant swinging hammers over the road time their strikes against "
                    "the pack while cars dodge and get smashed."},
    {"id": "ball_dodge", "name": "Giant Ball Dodge", "duration": 30, "vehicles": 3,
     "vehicle_type": "compact", "icon": "\U0001F94D", "max_vehicles": 6,
     "description": "Huge boulders roll across the track; the board ducking, "
                    "swerving and dodging is the whole show."},
    {"id": "spiral_race", "name": "Spiral Track Race", "duration": 32, "vehicles": 5,
     "vehicle_type": "sports", "icon": "\U0001F313", "max_vehicles": 5,
     "description": "Five cars climb a winding spiral tower with big drops and "
                    "yellow checkpoints. Great for race-hype edits."},
    {"id": "bridge_jump", "name": "Bridge Jump Challenge", "duration": 26, "vehicles": 3,
     "vehicle_type": "sports", "icon": "\u2693", "max_vehicles": 3,
     "description": "Cars speed up an elevated bridge, launch off the far end and "
                    "slow-mo splash onto the landing pad."},
    {"id": "crash_test", "name": "Vehicle Crash Test", "duration": 18, "vehicles": 2,
     "vehicle_type": "compact", "icon": "\U0001F4A5", "max_vehicles": 2,
     "description": "Two cars charge a brick crash-wall head-on. Pure destruction "
                    "and debris mayhem in a tight, rewatchable loop."},
    {"id": "random_stunt", "name": "Random Stunt Challenge", "duration": 24, "vehicles": 4,
     "vehicle_type": "mixed", "icon": "\U0001F3B2", "max_vehicles": 6,
     "description": "A sealed mixed-type selection of ramp/gap/obstacle stage "
                    "rebuilt from the seed each time. Every run is different."},
]

DEFAULT_TRACK_PARAMS = {
    "mega_ramp": {"ramp_angle_deg": 14.0, "ramp_length": 46.0, "gap": 26.0,
                  "road_width": 26.0, "obstacle_count": 4},
    "gap_jump": {"ramp_angle_deg": 12.0, "ramp_length": 40.0, "gap": 34.0, "road_width": 22.0},
    "obstacle_course": {"length": 220.0, "road_width": 28.0, "obstacle_count": 6},
    "monster_stunts": {"ramp_angle_deg": 19.0, "ramp_length": 42.0, "gap": 22.0,
                       "road_width": 30.0, "crush_stack": 3, "obstacle_count": 3},
    "hammer_challenge": {"length": 180.0, "road_width": 26.0, "obstacle_count": 5},
    "ball_dodge": {"length": 200.0, "road_width": 30.0, "obstacle_count": 6},
    "spiral_race": {"turns": 1.25, "radius": 26.0, "height": 30.0, "road_width": 16.0},
    "bridge_jump": {"length": 190.0, "bridge_height": 12.0, "gap": 18.0, "road_width": 22.0},
    "crash_test": {"run_length": 90.0, "road_width": 24.0, "wall_rows": 3, "wall_cols": 6},
    "random_stunt": {},
}


def template_by_id(template_id: str) -> Optional[Dict[str, Any]]:
    for tpl in TEMPLATES:
        if tpl["id"] == template_id:
            return tpl
    return None


def pick_colors(count: int, seed: int) -> List[str]:
    """Assign distinct colors (seeded) to vehicles."""
    rng = random.Random(seed * 7919 + 13)
    pool = VEHICLE_PALETTE.copy()
    rng.shuffle(pool)
    out: List[str] = []
    for i in range(count):
        out.append(pool[i % len(pool)])
    return out


def build_scenario(template_id: str, seed: int, *, vehicle_count: Optional[int] = None,
                   duration: Optional[float] = None, track_params: Optional[Dict[str, Any]] = None,
                   camera_style: str = "cinematic_auto",
                   vehicle_type: Optional[str] = None,
                   output_overrides: Optional[Dict[str, Any]] = None) -> ScenarioModel:
    """Create a fully generated (seeded, deterministic) scenario model.

    ``vehicle_type`` optionally overrides the template's default vehicle kind
    (ignored for templates whose default is ``mixed`` unless given).
    """
    tpl = template_by_id(template_id) or (TEMPLATES[0] if template_id == "" else None)
    if tpl is None:
        raise ScenarioError(f"unknown template {template_id!r}")
    count = vehicle_count or int(tpl["vehicles"])
    duration = float(duration or tpl["duration"])
    params = dict(DEFAULT_TRACK_PARAMS[template_id])
    if track_params:
        params.update(track_params)

    colors = pick_colors(count, seed)
    specs: List[Dict[str, Any]] = []
    kinds: List[str] = []
    effective_type = vehicle_type or tpl["vehicle_type"]
    if effective_type == "mixed":
        kinds = ["sports", "compact", "truck", "monster", "bus", "compact"][:count]
    else:
        kinds = [effective_type] * count
    styles = ["balanced", "aggressive", "cautious", "balanced"]
    for i in range(count):
        specs.append({
            "type": kinds[i % len(kinds)],
            "color": colors[i % len(colors)],
            "wheel_color": "#22223b",
            "label": f"Car {i + 1}",
            "scale": 1.0,
            "mass_override": None,
            "spawn_offset": [0.0, 0.0, 0.0],
            "spawn_yaw_deg": 0.0,
            "style": styles[i % len(styles)],
        })

    from ..models.scenario import VehicleSpecModel, OutputModel  # local import ok

    scenario = ScenarioModel(
        name=template_id.replace("_", " ").title(),
        seed=int(seed),
        duration_seconds=float(duration),
        camera_style=camera_style,
        track_type=template_id,
        track_params={k: (round(v, 2) if isinstance(v, float) else v) for k, v in params.items()},
        vehicles=[VehicleSpecModel.from_dict(s) for s in specs],
        output=OutputModel(),
    )
    if output_overrides:
        for key, value in output_overrides.items():
            if hasattr(scenario.output, key):
                setattr(scenario.output, key, value)
    layout = build_layout(template_id, scenario.track_params, scenario.seed, count)
    scenario.layout = layout
    if duration is None:
        # fall back to layout's duration hint when not explicitly chosen
        scenario.duration_seconds = float(layout["meta"]["duration_hint"])
    scenario.validate()
    return scenario


def regenerate_layout(scenario: ScenarioModel) -> ScenarioModel:
    layout = build_layout(scenario.track_type, scenario.track_params, scenario.seed,
                          len(scenario.vehicles))
    scenario.layout = layout
    scenario.validate()
    return scenario


def save_scenario_file(scenario: ScenarioModel, path: Optional[Path] = None) -> Path:
    if path is None:
        path = DEFAULT_SCENARIOS_DIR / (scenario.name.lower().replace(" ", "_") + ".json")
    scenario.save(path)
    return path


def load_scenario(path: Path) -> ScenarioModel:
    return ScenarioModel.load(path)


def list_scenario_files(directory: Optional[Path] = None) -> List[Path]:
    directory = Path(directory or DEFAULT_SCENARIOS_DIR)
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def ensure_smoke_scenario(out_path: Path, seed: int = 42) -> Path:
    """Deterministic 10-second single-vehicle mega-ramp smoke test."""
    sm = build_scenario("mega_ramp", seed, vehicle_count=1, duration=10.0)
    sm.name = "smoke_one_car_mega_ramp"
    sm.output.width, sm.output.height = 720, 1280
    sm.output.quality = "low"
    sm.output.out_file = "smoke_test.mp4"
    return save_scenario_file(sm, out_path)
