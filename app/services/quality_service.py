"""Milestone 3.5 Phase 2: durable render telemetry + deterministic quality report.

Persist the Godot event recorder output as ``<video>.events.json`` beside the
MP4 (the simulation workdir is a temp dir that may vanish at any time), then
score the finished render with a deterministic report:

- hard_failures : things that make the render technically unusable, or break a
  stunt requirement that is structural for that template.  A hard failure in
  the quality report never flips a batch job status by itself.
- soft_warnings : suspicious but plausible patterns that need manual review.
- info          : measurements, thresholds used and an explicit statement that
  this is an automated technical check, NOT a measure of virality or audience
  appeal.

Report schema: ``crs.quality/1``.  All checks run off the UI thread (they are
called from the QThread worker / batch runner).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services import ffmpeg_service as ff

QUALITY_SCHEMA = "crs.quality/1"

# ---------------------------------------------------------------------------
# Evidence-based template expectations (source: `temp/mil35_probe.json`, 10
# real low-res sim probes with default durations + track-layout zone files).
# ---------------------------------------------------------------------------

# Templates whose layout always starts cars on a launch ramp; a valid,
# non-empty telemetry file without a single launch event means the stunt
# never happened -> hard failure.  (spiral_race / bridge_jump ALSO have
# launch zones but real probes show runs where cars skip them, so those
# get a soft warning only.)
LAUNCH_EXPECTED = frozenset({"mega_ramp", "gap_jump", "monster_stunts"})

# Templates where every probe run recorded at least one finish event.
# Missing finish here is suspicious but not structurally impossible ->
# soft warning.
FINISH_EXPECTED = frozenset({
    "mega_ramp", "gap_jump", "obstacle_course", "hammer_challenge",
    "ball_dodge", "crash_test", "random_stunt",
})

# crash_test: the crash-wall exists on the racing line, but vehicles CAN
# dodge it (documented since M3.2) -> absence of crash events is a warning.
CRASH_WALL_EXPECTED = frozenset({"crash_test"})

# Templates built around a big jump: at least one land impact above the
# plateau-noise floor (~3.35 m/s in real logs) is expected.
BIG_JUMP_TEMPLATES = frozenset({
    "mega_ramp", "gap_jump", "monster_stunts", "bridge_jump", "random_stunt",
})
MIN_BIG_IMPACT_MPS = 8.0

# Audio RMS band: measured finals ranged -20.85..-24.16 dB; silent/breathing
# music beds sit below -32 dB; clipping/blasting tone beds exceed -10 dB.
RMS_DB_LOW, RMS_DB_HIGH = -32.0, -10.0

# Dead-window: longest event-free stretch.
DEAD_WINDOW_ABS_S = 10.0
DEAD_WINDOW_FRAC = 0.5

# Frame sampling (soft, heuristic): 3 fps, tiny frames, capped count.
FRAME_FPS = 3
MAX_FRAMES = 240
LUMA_DARK = 8.0
DARK_STREAK_SAMPLES = 3          # consecutive dark samples at 3 fps ~ 1 s
COLOR_PIXEL_SHARE = 0.004        # 0.4 % of frame pixels
COLOR_MAX_DISTANCE = 48.0        # Euclidean RGB distance
COLOR_MIN_DETECTION_RATE = 0.25

STATEMENT = ("Automated checks measure technical execution only (container "
             "validity, telemetry plausibility, audio levels, sampled-frame "
             "statistics) and are NOT a measure of virality or audience "
             "appeal; soft warnings always need manual review.")

_REPORT_TEXT = ("report saved to {path}")


# ---------------------------------------------------------------------------
# Event persistence
# ---------------------------------------------------------------------------

def persist_events(events_path: Optional[str], mp4_path: Path) -> Optional[str]:
    """Copy ``events.json`` next to the MP4 as ``<stem>.events.json``.

    The render workdir is a temp dir that is not long-lived, so this copy is
    the durable record.  Never overwrites: a name clash bumps to
    ``_1``, ``_2`` ... (Return the copied path, or None if there is no
    source file.)
    """
    if not events_path:
        return None
    src = Path(events_path)
    if not src.is_file():
        return None
    dest = _unique_next_to(mp4_path, ".events.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return str(dest)


def count_events(events_path: Optional[str]) -> Optional[int]:
    """Best-effort number of recorded events; None when invalid/missing."""
    if not events_path:
        return None
    try:
        doc = json.loads(Path(events_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("events"), list):
        return None
    return len(doc["events"])


def _unique_next_to(mp4_path: Path, suffix: str) -> Path:
    """<stem><suffix> bumped to _1/_2/... when the name is taken."""
    root = mp4_path.with_suffix("")
    path = Path(str(root) + suffix)
    if not path.exists():
        return path
    idx = 1
    while True:
        path = Path(str(root) + f"_{idx}" + suffix)
        if not path.exists():
            return path
        idx += 1


# ---------------------------------------------------------------------------
# Telemetry loading / expectations
# ---------------------------------------------------------------------------

def _load_events(events_file: Optional[str]) -> tuple[str, List[dict]]:
    """Returns (state, events) with state in missing|malformed|empty|valid."""
    if not events_file:
        return "missing", []
    path = Path(events_file)
    if not path.is_file():
        return "missing", []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "malformed", []
    if not isinstance(doc, dict) or not isinstance(doc.get("events"), list):
        return "malformed", []
    events = doc["events"]
    if not events:
        return "empty", []
    return "valid", events


def _count_types(events: List[dict]) -> Dict[str, Any]:
    types: Dict[str, Any] = {}
    lands = []
    falls = []
    for ev in events:
        t = str(ev.get("type", "unknown"))
        if t == "land":
            types["land"] = types.get("land", 0) + 1
            try:
                lands.append(float(ev.get("impact_speed", 0.0)))
            except (TypeError, ValueError):
                pass
        elif t == "crash":
            types["crash"] = types.get("crash", 0) + 1
        elif t == "fall":
            types["fall"] = types.get("fall", 0) + 1
            falls.append(ev)
        else:
            types[t] = types.get(t, 0) + 1
    if lands:
        types["max_land_impact_mps"] = round(max(lands), 2)
    types.pop("fall", None)
    types["fall"] = len(falls)
    return types


def _unrecovered_falls(events: List[dict]) -> List[int]:
    """Vehicles whose fall events never got a later land/finish by them."""
    fall_vehicle: Dict[int, None] = {}
    recovered: set = set()
    for ev in events:
        veh = int(ev.get("vehicle", -1) or -1)
        t_ = str(ev.get("type", ""))
        if t_ == "fall":
            fall_vehicle[veh] = None
        elif t_ in ("land", "finish") and veh in fall_vehicle:
            recovered.add(veh)
    return sorted(v for v in fall_vehicle if v not in recovered)


def _ev_time(ev: dict, default: float) -> float:
    try:
        return float(ev.get("t", default))
    except (TypeError, ValueError):
        return float(default)


def _dead_window(events: List[dict], duration: float) -> Optional[Dict[str, Any]]:
    if duration <= 0:
        return None
    times = sorted(_ev_time(ev, i) for i, ev in enumerate(events))
    bounds = [0.0] + [t for t in times if 0.0 <= t <= duration] + [duration]
    if len(bounds) < 2:
        return None
    gaps = [b - a for a, b in zip(bounds, bounds[1:])]
    biggest = max(gaps)
    if not biggest:
        return None
    pos = bounds[gaps.index(biggest)]
    return {"longest_gap_s": round(biggest, 2),
            "gap_starts_at_s": round(pos, 2),
            "first_event_s": round(times[0], 2) if times else None,
            "last_event_s": round(times[-1], 2) if times else None}


# ---------------------------------------------------------------------------
# Audio / frames
# ---------------------------------------------------------------------------

def _audio_rms_db(path: Path) -> Optional[float]:
    ffmpeg = ff.get_tools().get("ffmpeg")
    if not ffmpeg:
        return None
    proc = ff.run_cmd(
        ffmpeg, ["-hide_banner", "-i", str(path), "-map", "0:a?",
                 "-af", "astats=metadata=1:measure_overall=RMS_level:"
                        "measure_perchannel=none",
                 "-f", "null", "-"], timeout=300)
    for ln in (proc.stdout + proc.stderr).splitlines():
        if "RMS level dB" in ln:
            try:
                return float(ln.split(":")[1].strip())
            except (ValueError, IndexError):
                return None
    return None


def _sample_frames(path: Path) -> List[Path]:
    """Extract <= MAX_FRAMES tiny PNG frames; empty list when not possible."""
    ffmpeg = ff.get_tools().get("ffmpeg")
    if not ffmpeg:
        return []
    work = Path(tempfile.mkdtemp(prefix="crs_quality_frames_"))
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(path), "-vf",
             f"fps={FRAME_FPS},scale=96:54:flags=area",
             "-frames:v", str(MAX_FRAMES), str(work / "f%06d.png")],
            capture_output=True, timeout=300)
        if proc.returncode != 0:
            return []
        return sorted(work.glob("f*.png"))
    finally:
        pass  # caller analyses, then _cleanup_frames removes the tempdir


def _cleanup_frames(frames: List[Path]) -> None:
    if not frames:
        return
    shutil.rmtree(frames[0].parent, ignore_errors=True)


def _frame_stats(frames: List[Path], primary_rgb: Optional[tuple]
                 ) -> Dict[str, Any]:
    """Sampled-frame statistics: dark streaks + primary-color visibility."""
    from PIL import Image

    luma: List[float] = []
    detected = 0
    for png in frames:
        try:
            img = Image.open(png).convert("RGB")
        except OSError:
            continue
        px = list(img.getdata())
        if not px:
            continue
        lum = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px) / len(px)
        luma.append(lum)
        if primary_rgb is not None:
            pr, pg, pb = primary_rgb
            d2 = COLOR_MAX_DISTANCE * COLOR_MAX_DISTANCE
            hits = sum(
                1 for r, g, b in px
                if (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2 <= d2)
            if hits / len(px) >= COLOR_PIXEL_SHARE:
                detected += 1

    streak = 0
    worst_streak = 0
    for value in luma:
        if value < LUMA_DARK:
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0

    stats = {
        "samples": len(luma),
        "min_mean_luma": round(min(luma), 1) if luma else None,
        "dark_streak_samples": worst_streak,
        "primary_color_detection_rate": (
            round(detected / len(frames), 3) if frames and primary_rgb is not None
            else None),
    }
    return stats


def _hex_to_rgb(color) -> Optional[tuple]:
    if not isinstance(color, str) or not color.strip():
        return None
    text = color.strip().lstrip("#")
    if len(text) == 6:
        try:
            return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
        except ValueError:
            return None
    return None


def _primary_color_from_scenario(scenario_path: Optional[str]) -> Optional[tuple]:
    if not scenario_path:
        return None
    try:
        doc = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
        vehicles = doc.get("vehicles") or {}
        specs = vehicles.get("specs") if isinstance(vehicles, dict) \
            else vehicles
        if specs:
            return _hex_to_rgb(specs[0].get("color"))
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def score_render(mp4: Path, *, events_file: Optional[str] = None,
                 template_id: Optional[str] = None,
                 expected: Optional[Dict[str, Any]] = None,
                 primary_color: Optional[tuple] = None,
                 sample_frames: bool = True) -> Dict[str, Any]:
    """Probe + telemetry + audio + sampled-frame checks -> report dict.

    ``expected`` (all optional): {"width", "height", "fps", "want_audio"} —
    mismatches become hard failures. ``primary_color`` is an "#rrggbb" string
    (heuristic visibility check).
    """
    mp4 = Path(mp4)
    hard: List[str] = []
    soft: List[str] = []

    # --- container ----------------------------------------------------------
    info: Dict[str, Any] = {}
    if not mp4.is_file() or mp4.stat().st_size == 0:
        hard.append("video file is missing or zero bytes")
    else:
        try:
            info = ff.probe(mp4)
        except Exception as exc:  # noqa: BLE001
            hard.append(f"video could not be probed: {exc}")
            info = {}
    if info and not info.get("has_video"):
        hard.append("no video stream in file")

    if info and expected:
        exp_w = int(expected.get("width") or 0)
        exp_h = int(expected.get("height") or 0)
        exp_fps = float(expected.get("fps") or 0)
        want_audio = bool(expected.get("want_audio"))
        if exp_w and exp_h and (abs(info["width"] - exp_w) > 8
                                or abs(info["height"] - exp_h) > 8):
            hard.append(f"resolution {info['width']}x{info['height']} "
                        f"!= expected {exp_w}x{exp_h}")
        if exp_fps and abs(info["fps"] - exp_fps) > max(1.0, exp_fps * 0.05):
            hard.append(f"fps {info['fps']:.2f} != expected {exp_fps:.2f}")
        if want_audio and not info.get("has_audio"):
            hard.append("no audio stream (one was expected)")

    duration = float(info.get("duration") or 0.0)

    # --- telemetry ----------------------------------------------------------
    state, events = _load_events(events_file)
    tele_types = _count_types(events) if events else {}
    if state == "missing":
        soft.append("no telemetry events file — automated checks are limited "
                    "to container-level probing (single-car dodges may also "
                    "legitimately produce zero events)")
    elif state == "malformed":
        hard.append("telemetry events file exists but is malformed "
                    "(unreadable JSON or missing events list)")
    elif state == "empty":
        soft.append("telemetry file contains zero events — the recorder ran "
                    "but nothing was logged; check the log file")
    else:  # valid
        if template_id in LAUNCH_EXPECTED and not tele_types.get("launch"):
            hard.append(f"template '{template_id}' always launches but no "
                        "launch event was recorded")
        elif template_id == "random_stunt" and not tele_types.get("launch"):
            soft.append("random_stunt produced no launch event; this template "
                        "sometimes rolls ramp-less features — verify manually")
        if template_id in FINISH_EXPECTED and not tele_types.get("finish"):
            soft.append("no finish event recorded although this template "
                        "usually finishes (vehicles may be stuck)")
        if template_id in CRASH_WALL_EXPECTED and not tele_types.get("crash"):
            soft.append("crash_test recorded no collision events; walls can "
                        "be dodged by AI — check the video manually")
        if template_id in BIG_JUMP_TEMPLATES:
            max_imp = float(tele_types.get("max_land_impact_mps") or 0.0)
            if max_imp < MIN_BIG_IMPACT_MPS:
                soft.append(f"no land impact above {MIN_BIG_IMPACT_MPS} m/s "
                            f"(max {max_imp}); big-jump beat may be missing")
        unrecovered = _unrecovered_falls(events)
        if unrecovered:
            soft.append(f"fall events without a later land/finish for "
                        f"vehicle(s) {unrecovered} — NOT necessarily "
                        "unrecoverable; needs manual review")

    # --- audio --------------------------------------------------------------
    rms: Optional[float] = None
    if info.get("has_audio"):
        rms = _audio_rms_db(mp4)
        if rms is not None and (rms < RMS_DB_LOW or rms > RMS_DB_HIGH):
            soft.append(f"audio RMS {rms:.1f} dB outside sanity band "
                        f"[{RMS_DB_LOW:.0f}, {RMS_DB_HIGH:.0f}] dB")

    # --- dead window --------------------------------------------------------
    dead = _dead_window(events, duration) if events else None
    if dead and biggest_gap_too_long(dead["longest_gap_s"], duration):
        soft.append(f"long event-free stretch: {dead['longest_gap_s']} s "
                    f"(from {dead['gap_starts_at_s']} s) — verify pacing")

    # --- sampled frames -----------------------------------------------------
    frame_report: Optional[Dict[str, Any]] = None
    if sample_frames and info.get("has_video") and duration >= 3.0:
        frames = _sample_frames(mp4)
        try:
            if frames:
                stats = _frame_stats(frames, _hex_to_rgb(primary_color))
                frame_report = stats
                if stats["dark_streak_samples"] >= DARK_STREAK_SAMPLES:
                    soft.append(f"dark/black streak of {stats['dark_streak_samples']} "
                                f"sampled frames (min mean luma "
                                f"{stats['min_mean_luma']}) — check the cut")
                if (primary_color is not None
                        and stats["primary_color_detection_rate"] is not None
                        and stats["primary_color_detection_rate"]
                        < COLOR_MIN_DETECTION_RATE):
                    soft.append(
                        "primary vehicle color detected in only "
                        f"{stats['primary_color_detection_rate'] * 100:.0f}% "
                        "of sampled frames (heuristic; camera occlusion "
                        "logic means this needs manual review)")
        finally:
            _cleanup_frames(frames)
    elif sample_frames and info.get("has_video") and duration < 3.0:
        frame_report = {"samples": 0,
                        "note": "video too short for frame sampling"}

    # --- overall ------------------------------------------------------------
    overall = "fail" if hard else ("pass_with_warnings" if soft else "pass")
    report = {
        "schema": QUALITY_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video": mp4.name,
        "template_id": template_id,
        "overall": overall,
        "hard_failures": hard,
        "soft_warnings": soft,
        "info": {
            "video": {k: info.get(k) for k in
                      ("duration", "width", "height", "fps", "video_codec",
                       "audio_codec", "size_bytes")} if info else {},
            "telemetry": {
                "events_file": events_file,
                "state": state,
                "events_count": len(events),
                "types": tele_types,
            },
            "audio_rms_db": None if rms is None else round(rms, 2),
            "dead_window": dead,
            "frame_check": frame_report,
            "thresholds": _thresholds_doc(primary_color is not None),
            "statement": STATEMENT,
        },
    }
    report["quality_id"] = "q_" + _report_hash(report)
    return report


def biggest_gap_too_long(gap: float, duration: float) -> bool:
    return gap > max(DEAD_WINDOW_ABS_S, DEAD_WINDOW_FRAC * max(duration, 1e-6))


def _thresholds_doc(color_check: bool) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "audio_rms_db_band": [RMS_DB_LOW, RMS_DB_HIGH],
        "dead_window": {"abs_s": DEAD_WINDOW_ABS_S, "frac": DEAD_WINDOW_FRAC},
        "min_big_impact_mps": MIN_BIG_IMPACT_MPS,
        "launch_hard_templates": sorted(LAUNCH_EXPECTED),
        "finish_soft_templates": sorted(FINISH_EXPECTED),
        "luma_dark": LUMA_DARK,
        "dark_streak_min_samples": DARK_STREAK_SAMPLES,
    }
    if color_check:
        doc["color_check"] = {
            "pixel_share": COLOR_PIXEL_SHARE,
            "max_rgb_distance": COLOR_MAX_DISTANCE,
            "min_detection_rate": COLOR_MIN_DETECTION_RATE,
        }
    return doc


def _report_hash(report: Dict[str, Any]) -> str:
    payload = {k: v for k, v in report.items() if k not in ("created_at",)}
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.md5(blob).hexdigest()[:10]


def write_quality_report(mp4: Path, report: Dict[str, Any]) -> Path:
    """Write ``<stem>.quality.json`` atomically, never overwriting."""
    dest = _unique_next_to(mp4_path=Path(mp4), suffix=".quality.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=1), encoding="utf-8")
    tmp.replace(dest)
    return dest


def score_and_write(mp4: Path, *, events_file: Optional[str] = None,
                    template_id: Optional[str] = None,
                    expected: Optional[Dict[str, Any]] = None,
                    primary_color: Optional[str] = None,
                    sample_frames: bool = True) -> tuple:
    """Convenience used by both the single-render and batch paths so both
    produce exactly one ``<stem>.quality.json``."""
    report = score_render(mp4, events_file=events_file,
                          template_id=template_id, expected=expected,
                          primary_color=primary_color,
                          sample_frames=sample_frames)
    path = write_quality_report(mp4, report)
    return report, path
