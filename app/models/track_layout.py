"""Deterministic procedural track-layout generation.

Layouts are computed in pure Python (unit-testable) and embedded into the
scenario JSON. Coordinates: vehicles drive along +X, lanes spread across Z,
Y is up. All dimensions are meters.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

VTRACK_TYPES = (
    "mega_ramp", "gap_jump", "obstacle_course", "monster_stunts",
    "hammer_challenge", "ball_dodge", "spiral_race", "bridge_jump",
    "crash_test", "random_stunt",
)

RAINBOW = [
    "#ef476f", "#ffd166", "#06d6a0", "#118ab2",
    "#f78c6b", "#8338ec", "#ff595e", "#3a86ff",
    "#8ac926", "#ffbe0b", "#fb5607", "#9d4edd",
]

# Camera hint keys understood by the runtime director ('detail' anchors the
# dramatic close-up shot when the layout provides it):
_HINT_KEYS = ("start", "ramp_top", "gap_mid", "landing", "mid1", "mid2", "finish", "detail")


def _rainbow_sequence(count: int, offset: int = 0) -> List[str]:
    return [RAINBOW[(offset + i) % len(RAINBOW)] for i in range(count)]


def _segment(kind: str, frm: Sequence[float], size: Sequence[float], color: Optional[str] = None,
             rot_deg: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    seg: Dict[str, Any] = {
        "kind": kind,
        "from": [float(frm[0]), float(frm[1]), float(frm[2])],
        "size": [float(size[0]), float(size[1]), float(size[2])],
        "color": color or "#6c757d",
    }
    if rot_deg is not None:
        seg["rot_deg"] = [float(r) for r in rot_deg]
    return seg


def _zone(zone_type: str, at: Sequence[float], size: Sequence[float]) -> Dict[str, Any]:
    return {"type": zone_type, "at": [float(at[0]), float(at[1]), float(at[2])],
            "size": [float(size[0]), float(size[1]), float(size[2])]}


def _ramp(base_x: float, angle_deg: float, length: float, width: float, lane_z: float = 0.0,
          color: str = "#ffd166") -> Tuple[Dict[str, Any], Tuple[float, float, float]]:
    """A rising ramp box. Returns (segment, apex_position)."""
    a = math.radians(angle_deg)
    rise = length * math.tan(a)
    apex_x = base_x + length * math.cos(a)
    center = [base_x + 0.5 * length * math.cos(a), 0.5 * rise, lane_z]
    seg = _segment("ramp", center, [length, 1.0, width], color, rot_deg=(0, 0, angle_deg))
    return seg, (apex_x, rise, lane_z)


def _lane_starts(count: int, road_width: float) -> List[float]:
    """Evenly spread vehicle spawn lanes across the road."""
    if count <= 1:
        return [0.0]
    span = road_width * 0.6
    start = -span / 2.0
    return [start + span * i / (count - 1) for i in range(count)]


def _spawn_positions(count: int, road_width: float, jitter: float, rng: random.Random,
                     base_x: float = 6.0) -> List[Tuple[float, float, float]]:
    lanes = _lane_starts(count, road_width)
    out: List[Tuple[float, float, float]] = []
    for lane in lanes:
        jz = rng.uniform(-jitter, jitter) if jitter > 0 else 0.0
        out.append((base_x, 0.05, lane + jz))
    return out


def _hint(**kwargs: Tuple[float, float, float]) -> Dict[str, Tuple[float, float, float]]:
    return {k: tuple(float(v) for v in pos) for k, pos in kwargs.items() if pos is not None}


def _base_arena(road_width: float, road_length: float) -> Dict[str, Any]:
    return {
        "arena": {"size": [road_length + 120.0, max(90.0, road_width * 4.0)]},
        "sky": {"energy": 1.5, "top_color": "#8ecae6", "ground_color": "#f1faee"},
        "sun": {"rotation_deg": (-38.0, -25.0), "energy": 1.2},
    }


# --------------------------------------------------------------------------
# Track builders
# --------------------------------------------------------------------------

def build_mega_ramp(params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    rng = random.Random(seed * 977 + 7)
    width = float(params.get("road_width", 26.0))
    angle = float(params.get("ramp_angle_deg", rng.uniform(13.0, 17.0)))
    ramp_len = float(params.get("ramp_length", 46.0))
    gap = float(params.get("gap", 26.0))
    obstacles = int(params.get("obstacle_count", 4))

    road: List[Dict[str, Any]] = []
    cols = _rainbow_sequence(5, rng.randrange(len(RAINBOW)))
    road.append(_segment("platform", (28.0, 0.25, 0), [56.0, 0.5, width], "#495057"))
    ramp_seg, apex = _ramp(56.0, angle, ramp_len, width, 0.0, cols[0])
    road.append(ramp_seg)
    landing_x = apex[0] + gap
    road.append(_segment("platform", (landing_x + 22.0, apex[1] * 0.45, 0),
                         [44.0, 0.5, width], cols[3]))
    exit_x = landing_x + 44.0
    road.append(_segment("platform", (exit_x + 25.0, apex[1] * 0.45, 0),
                         [50.0, 0.5, width], "#495057"))

    obstacles_out: List[Dict[str, Any]] = []
    zone_step = 44.0
    for i in range(obstacles):
        ox = 16.0 + i * zone_step
        if ox < 50.0:
            obstacles_out.append({"type": "cone", "at": [ox, 0.4, width * 0.5 - 4.0]})
    obstacles_out.append({"type": "barrier", "at": [exit_x + 42.0, apex[1] * 0.45 + 1.0, 0],
                          "size": [1.2, 2.4, width * 0.7], "rot_deg": [0, 0, 0]})

    zones = [
        _zone("launch", apex, [6.0, 3.0, width]),
        _zone("landing", (landing_x + 12.0, apex[1] * 0.45, 0), [16.0, 3.0, width]),
        _zone("finish", (exit_x + 42.0, apex[1] * 0.45, 0), [8.0, 4.0, width]),
    ]
    spawns = _spawn_positions(vehicle_count, width, 1.2, rng)
    hints = _hint(start=(0, 8, -30), ramp_top=(apex[0], apex[1] + 2.5, -20),
                  gap_mid=((apex[0] + landing_x) / 2, apex[1] + 9.0, 0),
                  landing=(landing_x + 6, apex[1] * 0.45 + 7, -22),
                  finish=(exit_x + 30, apex[1] * 0.45 + 9, -26))
    return {"meta": {"segment_count": len(road), "arena": _base_arena(width, exit_x)["arena"],
                     "sky": _base_arena(width, exit_x)["sky"], "sun": _base_arena(width, exit_x)["sun"]},
            "road": road, "obstacles": obstacles_out, "zones": zones, "spawns": spawns,
            "camera_hints": hints}


def build_gap_jump(params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    rng = random.Random(seed * 131 + 17)
    width = float(params.get("road_width", 22.0))
    angle = float(params.get("ramp_angle_deg", rng.uniform(11.0, 15.0)))
    ramp_len = float(params.get("ramp_length", 40.0))
    gap = float(params.get("gap", 34.0))

    road: List[Dict[str, Any]] = []
    cols = _rainbow_sequence(6, rng.randrange(len(RAINBOW)))
    road.append(_segment("platform", (30.0, 0.25, 0), [60.0, 0.5, width], "#495057"))
    ramp_seg, apex = _ramp(60.0, angle, ramp_len, width, 0.0, cols[1])
    road.append(ramp_seg)
    landing_x = apex[0] + gap
    drop = min(6.0, gap * 0.12)
    landing_y = apex[1] - drop
    road.append(_segment("platform", (landing_x + 20.0, landing_y, 0),
                         [40.0, 0.5, width], cols[4]))
    road.append(_segment("platform", (landing_x + 56.0, landing_y, 0),
                         [44.0, 0.5, width], "#495057"))

    obstacles: List[Dict[str, Any]] = []
    for i in range(3):
        obstacles.append({"type": "cone", "at": [12.0 + i * 14.0, 0.4, -width * 0.35]})

    zones = [
        _zone("launch", apex, [7.0, 3.0, width]),
        _zone("landing", (landing_x + 10.0, landing_y, 0), [18.0, 3.0, width]),
        _zone("finish", (landing_x + 54.0, landing_y, 0), [8.0, 4.0, width]),
    ]
    spawns = _spawn_positions(vehicle_count, width, 1.0, rng)
    hints = _hint(start=(-6, 8, -26), ramp_top=(apex[0], apex[1] + 3.0, -18),
                  gap_mid=((apex[0] + landing_x) / 2, apex[1] + 10.0, 0),
                  landing=(landing_x + 5, landing_y + 7, -20),
                  finish=(landing_x + 46, landing_y + 9, -24))
    return {"meta": {"segment_count": len(road), **_base_arena(width, landing_x + 80)},
            "road": [r for r in road], "obstacles": obstacles, "zones": zones,
            "spawns": spawns, "camera_hints": hints}


def build_obstacle_course(params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    rng = random.Random(seed * 401 + 91)
    width = float(params.get("road_width", 28.0))
    length = float(params.get("length", 220.0))
    obstacle_count = int(params.get("obstacle_count", 6))

    road = [_segment("platform", (length / 2, 0.25, 0), [length + 40.0, 0.5, width], "#495057")]
    cols = _rainbow_sequence(10, rng.randrange(len(RAINBOW)))
    obstacles: List[Dict[str, Any]] = []
    spacing = length / max(1, obstacle_count + 2)
    for i in range(obstacle_count):
        x = 24.0 + spacing * (i + 1)
        kind = ["cone", "barrier", "rotator", "moving_platform", "cone", "ball"][i % 6]
        if kind == "cone":
            obstacles.append({"type": "cone", "at": [x, 0.4, rng.uniform(-width * 0.35, width * 0.35)]})
        elif kind == "barrier":
            obstacles.append({"type": "barrier", "at": [x, 0.8, 0], "size": [1.0, 1.6, width * 0.35],
                              "rot_deg": [0, 0, 0]})
        elif kind == "rotator":
            obstacles.append({"type": "rotator", "at": [x, 1.6, 0],
                              "size": [3.0, 0.6, 14.0], "rpm": rng.choice((18.0, 30.0))})
        elif kind == "moving_platform":
            obstacles.append({"type": "moving_platform", "at": [x, 0.3, 0],
                              "size": [6.0, 2.4, 2.0], "travel": [0, 0, width * 0.35],
                              "period_sec": rng.uniform(4.0, 7.0)})
        elif kind == "ball":
            obstacles.append({"type": "ball", "at": [x, 2.0, -width * 0.5], "radius": 2.2,
                              "push": [0.0, 0.0, 1.0], "speed": rng.uniform(8.0, 12.0)})
    zones = [_zone("checkpoint", (length / 2, 0, -width / 2 - 3), [width, 0.5, width]),
             _zone("finish", (length + 30.0, 0, 0), [8.0, 4.0, width])]
    spawns = _spawn_positions(vehicle_count, width, 1.0, rng)
    hints = _hint(start=(0, 9, -34), mid1=(length * 0.25, 8, -width * 0.55),
                  mid2=(length * 0.7, 9, -width * 0.5), finish=(length + 22, 10, -30))
    return {"meta": {"segment_count": len(road), **_base_arena(width, length)},
            "road": road, "obstacles": obstacles, "zones": zones,
            "spawns": spawns, "camera_hints": hints}


def build_monster_stunts(params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    rng = random.Random(seed * 811 + 29)
    width = float(params.get("road_width", 30.0))
    angle = float(params.get("ramp_angle_deg", rng.uniform(17.0, 22.0)))
    ramp_len = float(params.get("ramp_length", 42.0))
    gap = float(params.get("gap", 22.0))

    road = [_segment("platform", (26.0, 0.25, 0), [52.0, 0.5, width], "#495057")]
    cols = _rainbow_sequence(6, rng.randrange(len(RAINBOW)))
    ramp_seg, apex = _ramp(52.0, angle, ramp_len, width, 0.0, cols[0])
    road.append(ramp_seg)
    landing_x = apex[0] + gap
    landing_y = apex[1] * 0.55
    road.append(_segment("platform", (landing_x + 18.0, landing_y, 0), [36.0, 0.5, width], cols[5]))
    crush_x = landing_x + 18.0
    obstacles: List[Dict[str, Any]] = []
    # Crush stack of boxes on landing (monster truck spectacle)
    stack = int(params.get("crush_stack", 3))
    for i in range(stack):
        for j in range(2):
            obstacles.append({"type": "crash_wall", "at": [crush_x + 30.0 + i * 4.5, 1.0 + j * 2.0, 0],
                              "size": [3.6, 2.0, 7.0]})
    for i in range(int(params.get("obstacle_count", 3))):
        obstacles.append({"type": "ball", "at": [10.0 + i * 12.0, 2.6, width * 0.42],
                          "radius": 2.4, "push": [0.0, 0.0, -1.0],
                          "speed": rng.uniform(6.0, 10.0)})
    zones = [
        _zone("launch", apex, [7.0, 3.0, width]),
        _zone("landing", (landing_x + 8.0, landing_y, 0), [16.0, 3.0, width]),
        _zone("finish", (crush_x + 46.0, landing_y, 0), [8.0, 4.0, width]),
    ]
    spawns = _spawn_positions(vehicle_count, width, 2.0, rng)
    hints = _hint(start=(0, 10, -34), ramp_top=(apex[0], apex[1] + 4.0, -22),
                  landing=(landing_x + 4, landing_y + 8, -24),
                  detail=(crush_x + 30, 12, -18), finish=(crush_x + 40, 12, -28))
    return {"meta": {"segment_count": len(road), **_base_arena(width, crush_x + 80)},
            "road": road, "obstacles": obstacles, "zones": zones,
            "spawns": spawns, "camera_hints": hints}


def build_hammer_challenge(params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    rng = random.Random(seed * 613 + 53)
    width = float(params.get("road_width", 26.0))
    length = float(params.get("length", 180.0))
    hammers = int(params.get("obstacle_count", 5))

    road = [_segment("platform", (length / 2, 0.25, 0), [length + 36.0, 0.5, width], "#495057")]
    cols = _rainbow_sequence(max(4, hammers), rng.randrange(len(RAINBOW)))
    obstacles: List[Dict[str, Any]] = []
    spacing = length / (hammers + 2)
    for i in range(hammers):
        x = 20.0 + spacing * (i + 1)
        side = 1.0 if i % 2 == 0 else -1.0
        obstacles.append({"type": "hammer", "at": [x, 9.5, side * (width * 0.30)],
                          "arm_len": 9.0, "head_size": 3.4,
                          "swing_sec": rng.uniform(2.4, 3.2), "phase_deg": rng.uniform(0, 120)})
    zones = [_zone("checkpoint", (length * 0.5, 0, -width / 2 - 3), [width, 0.5, width]),
             _zone("finish", (length + 26.0, 0, 0), [8.0, 4.0, width])]
    for i in range(4):
        obstacles.append({"type": "cone", "at": [14.0 + i * 9.0, 0.4, width * 0.4]})
    spawns = _spawn_positions(vehicle_count, width, 1.0, rng)
    hints = _hint(start=(0, 9, -34), mid1=(length * 0.35, 14, -40),
                  mid2=(length * 0.65, 14, -40), finish=(length + 20, 9, -30))
    return {"meta": {"segment_count": len(road), **_base_arena(width, length)},
            "road": road, "obstacles": obstacles, "zones": zones,
            "spawns": spawns, "camera_hints": hints}


def build_ball_dodge(params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    rng = random.Random(seed * 251 + 77)
    width = float(params.get("road_width", 30.0))
    length = float(params.get("length", 200.0))
    balls = int(params.get("obstacle_count", 6))

    road = [_segment("platform", (length / 2, 0.25, 0), [length + 40.0, 0.5, width], "#495057")]
    obstacles: List[Dict[str, Any]] = []
    spacing = length / (balls + 2)
    for i in range(balls):
        x = 22.0 + spacing * (i + 1)
        side = 1.0 if i % 2 == 0 else -1.0
        obstacles.append({"type": "ball", "at": [x, 2.6, side * (width * 0.55)],
                          "radius": float(rng.uniform(2.0, 3.2)),
                          "push": [0.0, 0.0, -side], "speed": float(rng.uniform(9.0, 14.0))})
    zones = [_zone("checkpoint", (length * 0.5, 0, -width / 2 - 3), [width, 0.5, width]),
             _zone("finish", (length + 28.0, 0, 0), [8.0, 4.0, width])]
    spawns = _spawn_positions(vehicle_count, width, 1.5, rng)
    hints = _hint(start=(0, 9, -34), mid1=(length * 0.3, 10, -width * 0.6),
                  mid2=(length * 0.6, 10, -width * 0.6), finish=(length + 20, 10, -30))
    return {"meta": {"segment_count": len(road), **_base_arena(width, length)},
            "road": road, "obstacles": obstacles, "zones": zones,
            "spawns": spawns, "camera_hints": hints}


def build_spiral_race(params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    rng = random.Random(seed * 353 + 41)
    width = float(params.get("road_width", 16.0))
    turns = float(params.get("turns", 1.25))
    radius = float(params.get("radius", 26.0))
    height = float(params.get("height", 30.0))

    cols = _rainbow_sequence(20, rng.randrange(len(RAINBOW)))
    road: List[Dict[str, Any]] = []
    # Base runway
    road.append(_segment("platform", (radius + 26.0, 0.25, 0), [52.0, 0.5, width], "#495057"))
    step_deg = 14.0
    steps = int(360.0 * turns / step_deg)
    enter_x = radius + 52.0
    enter_angle = 0.0
    for i in range(steps):
        a = math.radians(enter_angle + step_deg * i)
        cx = enter_x + radius * (math.cos(a) - 1.0)
        cz = radius * math.sin(a)
        y = i / max(1, steps - 1) * height + 0.35
        seg_len = radius * math.radians(step_deg) / math.cos(0.0) * 1.06
        seg = _segment("ramp", (cx, y, cz), [seg_len, 0.5, width], cols[i % len(cols)],
                       rot_deg=(0, -math.degrees(a), 6.2))
        # small bank tilt
        road.append(seg)
    top_angle = math.radians(enter_angle + step_deg * steps)
    top_x = enter_x + radius * (math.cos(top_angle) - 1.0)
    top_z = radius * math.sin(top_angle)
    road.append(_segment("platform", (top_x + 14.0, height + 0.4, top_z),
                         [28.0, 0.5, width], "#495057", rot_deg=(0, -math.degrees(top_angle), 0)))
    zones: List[Dict[str, Any]] = [
        _zone("launch", (enter_x, 0, 0), [6.0, 3.0, width]),
        _zone("checkpoint", (enter_x + radius, height - 1.0, 0), [width, 0.5, width]),
        _zone("finish", (top_x + 24.0, height, top_z), [8.0, 4.0, width]),
    ]
    spawns = _spawn_positions(vehicle_count, width, 0.6, rng)
    hints = _hint(start=(radius - 6, 10, -34), mid1=(enter_x + radius * 0.4, height * 0.55, -radius - 30),
                  mid2=(enter_x + radius * 2.1, height * 0.95, radius * 0.4), finish=(top_x + 20, height + 8, top_z - 20))
    return {"meta": {"segment_count": len(road), **_base_arena(width * 2.6, top_x + 90)},
            "road": road, "obstacles": [], "zones": zones,
            "spawns": spawns, "camera_hints": hints}


def build_bridge_jump(params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    rng = random.Random(seed * 199 + 3)
    width = float(params.get("road_width", 22.0))
    total = float(params.get("length", 190.0))
    gap = float(params.get("gap", 18.0))
    bridge_y = float(params.get("bridge_height", 12.0))

    cols = _rainbow_sequence(6, rng.randrange(len(RAINBOW)))
    road = [_segment("platform", (total * 0.28, bridge_y, 0), [total * 0.56, 0.5, width], "#6d7d8b")]
    # approach and exit roads
    road.append(_segment("platform", (total * 0.13, 0.25, 0), [total * 0.26, 0.5, width], "#495057"))
    road.append(_segment("platform", (total * 0.86, 0.25, 0), [total * 0.28, 0.5, width], "#495057"))
    # bridge ramps up/down at ends
    ramp_len = 22.0
    up_angle = math.degrees(math.atan2(bridge_y, ramp_len))
    seg, apex = _ramp(total * 0.26, up_angle, ramp_len, width, 0.0, cols[2])
    road[-1] = road[-1]
    road.append(seg)
    # bridge deck spans up to gap start
    gap_start = total * 0.50
    end_x = gap_start - gap / 2
    bridge_center = (total * 0.26 + ramp_len + end_x) / 2
    road.append(_segment("bridge", (bridge_center, bridge_y + (apex[1]) * 0.2, 0),
                         [(gap_start - (total * 0.26 + ramp_len)), 0.5, width], cols[4]))
    # landing past gap
    landing_x = end_x + gap
    road.append(_segment("platform", (landing_x + 16.0, bridge_y - gap * 0.45, 0),
                         [32.0, 0.5, width], cols[5]))
    road.append(_segment("platform", (landing_x + 46.0, bridge_y - gap * 0.45, 0),
                         [36.0, 0.5, width], "#495057"))

    obstacles = [
        {"type": "rotator", "at": [total * 0.3, bridge_y + 1.6, 0],
         "size": [2.6, 0.6, 10.0], "rpm": 26.0},
    ]
    zones = [
        _zone("launch", (end_x, bridge_y + 1.0, 0), [5.0, 3.0, width]),
        _zone("landing", (landing_x + 6.0, bridge_y - gap * 0.45, 0), [14.0, 3.0, width]),
        _zone("finish", (landing_x + 44.0, bridge_y - gap * 0.45, 0), [8.0, 4.0, width]),
    ]
    spawns = _spawn_positions(vehicle_count, width, 1.0, rng)
    hints = _hint(start=(0, 10, -34), mid1=(end_x - 16, bridge_y + 9, -26),
                  gap_mid=(gap_start - gap / 2, bridge_y + 8, 0),
                  landing=(landing_x + 4, bridge_y - gap * 0.45 + 8, -22),
                  finish=(landing_x + 38, bridge_y - gap * 0.45 + 9, -28))
    return {"meta": {"segment_count": len(road), **_base_arena(width, total + 40)},
            "road": road, "obstacles": obstacles, "zones": zones,
            "spawns": spawns, "camera_hints": hints}


def build_crash_test(params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    rng = random.Random(seed * 173 + 11)
    width = float(params.get("road_width", 24.0))
    length = float(params.get("run_length", 90.0))
    wall_pos = length - 8.0

    road = [_segment("platform", (length / 2, 0.25, 0), [length + 30.0, 0.5, width], "#343a40")]
    obstacles: List[Dict[str, Any]] = []
    rows = int(params.get("wall_rows", 3))
    cols_count = int(params.get("wall_cols", 6))
    for i in range(rows):
        for j in range(cols_count):
            cross = (j - (cols_count - 1) / 2) * 3.6
            obstacles.append({"type": "crash_wall",
                              "at": [wall_pos, 0.9 + i * 1.9, cross],
                              "size": [3.3, 1.9, 3.5]})
    barriers = [
        {"type": "barrier", "at": [wall_pos + 18.0, 1.0, -width / 2 + 6], "size": [1.0, 2.0, 3.5], "rot_deg": [0, 0, 0]},
        {"type": "barrier", "at": [wall_pos + 18.0, 1.0, width / 2 - 6], "size": [1.0, 2.0, 3.5], "rot_deg": [0, 0, 0]},
    ]
    obstacles_total = obstacles + barriers
    zones = [
        _zone("checkpoint", (length * 0.4, 0, -width / 2 - 3), [width, 0.5, width]),
        _zone("finish", (wall_pos + 22.0, 0, 0), [8.0, 4.0, width]),
    ]
    spawns = _spawn_positions(vehicle_count, width, 2.5, rng)
    hints = _hint(start=(length * 0.05, 8, -32), mid1=(wall_pos - 24, 16, -38),
                  detail=(wall_pos + 6, 10, -26), finish=(wall_pos + 20, 9, -30))
    return {"meta": {"segment_count": len(road), **_base_arena(width, length + 40)},
            "road": road, "obstacles": obstacles_total, "zones": zones,
            "spawns": spawns, "camera_hints": hints}


def build_random_stunt(params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    rng = random.Random(seed * 3167 + 101)
    feature = rng.choice(["ramp_balls", "ramp_hammers", "course_rotators", "gap_cones"])
    base_params = dict(params)

    if feature == "ramp_balls":
        layout = build_mega_ramp({"obstacle_count": 0, **base_params}, seed, vehicle_count)
        extra: List[Dict[str, Any]] = []
        spacing = 34.0
        for i in range(int(rng.uniform(3, 5))):
            extra.append({"type": "ball", "at": [14.0 + i * spacing, 2.6, 0.0],
                          "radius": float(rng.uniform(2.0, 3.0)),
                          "push": [0.0, 0.0, 1.0 if i % 2 else -1.0],
                          "speed": float(rng.uniform(6.0, 12.0))})
        layout["obstacles"] += extra
    elif feature == "ramp_hammers":
        layout = build_mega_ramp({"obstacle_count": 0, **base_params}, seed, vehicle_count)
        for i in range(int(rng.uniform(2, 4))):
            x = 12.0 + i * 14.0
            layout["obstacles"].append({"type": "hammer", "at": [x, 9.0, -11.0],
                                        "arm_len": 8.0, "head_size": 3.0,
                                        "swing_sec": rng.uniform(2.2, 3.4), "phase_deg": rng.uniform(0, 180)})
    elif feature == "course_rotators":
        layout = build_obstacle_course({"obstacle_count": max(4, int(rng.uniform(4, 7))), **base_params},
                                       seed, vehicle_count)
    else:
        layout = build_gap_jump({**base_params, "gap": round(rng.uniform(22.0, 34.0), 1)}, seed, vehicle_count)
    return layout


BUILDERS = {
    "mega_ramp": build_mega_ramp,
    "gap_jump": build_gap_jump,
    "obstacle_course": build_obstacle_course,
    "monster_stunts": build_monster_stunts,
    "hammer_challenge": build_hammer_challenge,
    "ball_dodge": build_ball_dodge,
    "spiral_race": build_spiral_race,
    "bridge_jump": build_bridge_jump,
    "crash_test": build_crash_test,
    "random_stunt": build_random_stunt,
}


def build_layout(track_type: str, params: Dict[str, Any], seed: int, vehicle_count: int) -> Dict[str, Any]:
    builder = BUILDERS.get(track_type)
    if builder is None:
        raise ValueError(f"unknown track type {track_type!r}; known: {sorted(BUILDERS)}")
    layout = builder(dict(params), seed, vehicle_count)
    layout["meta"]["duration_hint"] = {
        "mega_ramp": 22, "gap_jump": 20, "obstacle_course": 30, "monster_stunts": 26,
        "hammer_challenge": 28, "ball_dodge": 30, "spiral_race": 32, "bridge_jump": 26,
        "crash_test": 18, "random_stunt": 24,
    }[track_type]
    return layout
