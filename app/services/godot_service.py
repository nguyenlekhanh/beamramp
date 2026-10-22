"""Godot engine integration: locate binary, run preview and movie-render passes."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GODOT_PROJECT = PROJECT_ROOT / "godot_project"
TOOLS_DIR = PROJECT_ROOT / "tools"

_GODOT_PATTERN = re.compile(r"Godot.*win64(_console)?\.exe$", re.IGNORECASE)


def find_godot() -> Optional[Path]:
    env_path = os.environ.get("CRS_GODOT_EXE")
    if env_path and Path(env_path).exists():
        return Path(env_path)
    if TOOLS_DIR.exists():
        for exe in sorted(TOOLS_DIR.glob("Godot*win64*.exe")):
            if "console" in exe.name.lower():
                return exe
        for exe in sorted(TOOLS_DIR.glob("Godot*win64*.exe")):
            return exe
    found = None
    for base in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
                 PROJECT_ROOT.parent.parent):
        if base.exists():
            for exe in (*base.glob("Godot*/*.exe"), *base.glob("Godot*.exe")):
                if _GODOT_PATTERN.search(exe.name):
                    return exe
    return found


def _runtime_env(output_w: int, output_h: int, quality: str,
                 scenario_path: Path, movie_format: str,
                 allow_audio: bool, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = os.environ.copy()
    # Godot resolves relative resource paths against the project dir, so the
    # scenario path MUST be absolute or the sim silently boots empty.
    env.update({
        "CRS_SCENARIO": str(Path(scenario_path).resolve()),
        "CRS_OUTPUT_W": str(output_w),
        "CRS_OUTPUT_H": str(output_h),
        "CRS_QUALITY": quality,
        "CRS_MOVIE_FORMAT": movie_format,
        "CRS_ALLOW_AUDIO": "1" if allow_audio else "0",
    })
    if extra:
        env.update(extra)
    return env


def run_preview(scenario_path: Path, output_w: int, output_h: int, quality: str,
                allow_audio: bool = True, on_line: Optional[Callable[[str], None]] = None,
                timeout: int = 3600,
                cancel_event: Optional[threading.Event] = None) -> int:
    """Open a live simulation window (no recording). Returns Godot exit code."""
    godot = find_godot()
    if not godot:
        raise RuntimeError("Godot not found under ./tools or PATH (see README setup)")
    args = [
        str(godot), "--path", str(GODOT_PROJECT),
        "--resolution", f"{output_w}x{output_h}",
    ]
    if quality == "low":
        args += ["--rendering-method", "gl_compatibility"]
    proc = subprocess.Popen(
        args, env=_runtime_env(output_w, output_h, quality, scenario_path, "none", allow_audio),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if on_line:
                on_line(line)
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError("preview closed by user")
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.poll() is None:
            proc.wait()
    return proc.returncode


# Raised by run_render when the caller requests cancellation.
class RenderCancelled(RuntimeError):
    pass


_SIM_DONE_RE = re.compile(r"\[CRS\] Done sim_time=([0-9.]+)s events=(\d+)")
_SLOWMO_RE = re.compile(r"\[CRS slowmo\] kind=(\w+) t=([0-9.]+) dur=([0-9.]+)")


def run_sim_probe(scenario_path: Path, timeout: int = 300,
                  cancel_event: Optional[threading.Event] = None) -> Dict[str, Any]:
    """Run the simulation headless WITHOUT movie recording (fast integrity probe).

    Parses the log for the completion line, script errors and slowmo spacing.
    Returns dict: exit_code, sim_time, events_count, script_errors, slowmo, log.
    """
    godot = find_godot()
    if not godot:
        raise RuntimeError("Godot not found under ./tools (README setup)")
    work_dir = Path(tempfile.mkdtemp(prefix="crs_probe_"))
    env = os.environ.copy()
    env.update({
        "CRS_SCENARIO": str(scenario_path),
        "CRS_OUTPUT_W": "320", "CRS_OUTPUT_H": "180",
        "CRS_QUALITY": "low", "CRS_MOVIE_FORMAT": "none",         "CRS_ALLOW_AUDIO": "0",
        "CRS_EVENTS": str(work_dir / "events.json"),
        "CRS_DBG_ALL": os.environ.get("CRS_DBG_ALL", "0"),
    })
    args = [str(godot), "--path", str(GODOT_PROJECT)]
    started = time.time()
    proc = subprocess.Popen(args, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    log: List[str] = []
    try:
        if proc.stdout:
            for line in proc.stdout:
                line = line.rstrip()
                log.append(line)
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    raise RenderCancelled("probe cancelled")
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"headless sim probe timed out after {timeout}s "
                           f"(log tail: {' | '.join(log[-10:])})")
    elapsed = time.time() - started
    sim_time, events_count = 0.0, -1
    for line in log:
        m = _SIM_DONE_RE.search(line)
        if m:
            sim_time = float(m.group(1))
            events_count = int(m.group(2))
    errors = [ln for ln in log if "SCRIPT ERROR" in ln or "Parse Error" in ln]
    slowmo = [{"kind": m.group(1), "t": float(m.group(2)), "dur": float(m.group(3))}
              for line in "\n".join(log).splitlines() if (m := _SLOWMO_RE.search(line))]
    return {
        "exit_code": proc.returncode,
        "sim_time": sim_time,
        "events_count": events_count,
        "script_errors": errors[-5:],
        "slowmo": slowmo,
        "elapsed": elapsed,
        "log": log[-60:],
        "log_full": "\n".join(log),
        "events_path": str(work_dir / "events.json"),
    }


def run_render(scenario_path: Path, out_mp4: Path, output_w: int, output_h: int,
               fps: int, quality: str = "medium",
               allow_audio: bool = True, movie_format: str = "avi",
               progress_cb: Optional[Callable[[Optional[int]], None]] = None,
               timeout: int = 7200,
               cancel_event: Optional[threading.Event] = None,
               audio: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one Godot pass with Movie Maker; then encode to MP4 via FFmpeg.

    ``audio`` (optional dict) configures the deterministic post-mix:
      duration, sfx_enabled, engine_volume, sfx_volume, music_path,
      music_enabled, music_volume. When omitted, exports keep a silent
      audio track. Returns dict with keys: movie_path, mp4_path, duration,
      frames, events_path, elapsed, avg_fps_render, audio.
    """
    from . import ffmpeg_service as ff
    from . import audio_mixer

    godot = find_godot()
    if not godot:
        raise RuntimeError("Godot not found under ./tools (README setup)")

    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="crs_render_"))
    movie_base = work_dir / "crs_frames"
    if movie_format == "png":
        movie_arg = f"{work_dir / 'frames'}/frames.png"
    else:
        movie_arg = f"{movie_base}.avi"

    env = _runtime_env(output_w, output_h, quality, scenario_path,
                       movie_format, allow_audio,
                       {"CRS_EVENTS": str(work_dir / "events.json")})
    args = [
        str(godot), "--path", str(GODOT_PROJECT),
        "--resolution", f"{output_w}x{output_h}",
        "--write-movie", movie_arg,
        "--fixed-fps", str(fps),
    ]
    if quality == "low":
        args += ["--rendering-method", "gl_compatibility"]

    started = time.time()
    proc = subprocess.Popen(args, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    render_log: List[str] = []
    try:
        if proc.stdout:
            for line in proc.stdout:
                line = line.rstrip()
                render_log.append(line)
                if progress_cb:
                    m = re.search(r"Movie frame (\d+)", line)
                    if m:
                        try:
                            progress_cb(int(m.group(1)))
                        except Exception:
                            pass
                if cancel_event is not None and cancel_event.is_set():
                    raise RenderCancelled("render cancelled by user")
        proc.wait(timeout=timeout)
        if cancel_event is not None and cancel_event.is_set():
            raise RenderCancelled("render cancelled by user")
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("Godot render timed out")
    except RenderCancelled:
        proc.kill()
        proc.wait(timeout=30)
        raise
    elapsed = time.time() - started

    # guard against empty-boot renders (e.g. scenario path unresolvable):
    # Godot still records a ~1s movie of an empty scene, which would only
    # surface later as a verify failure instead of a clear error.
    log_text = "\n".join(render_log)
    if "Scenario not found" in log_text or "Ready: vehicles=0" in log_text:
        raise RuntimeError("Godot booted the sim empty ("
                           + ("scenario file not found" if "Scenario not found" in log_text
                              else "no vehicles spawned") + "); log:\n" + "\n".join(render_log[-40:]))

    movie_file = Path(movie_arg)
    if movie_format == "png":
        pattern_prefix = str(work_dir / "frames/frames")
        n_frames = len(list((work_dir / "frames").glob("frames*.png")))
        if n_frames == 0:
            raise RuntimeError("no frames were rendered; Godot log:\n" + "\n".join(render_log[-40:]))
        encoded_input = f"{pattern_prefix}%04d.png"
    else:
        if not movie_file.exists() or movie_file.stat().st_size == 0:
            raise RuntimeError("Godot produced no movie file; log:\n" + "\n".join(render_log[-40:]))
        encoded_input = str(movie_file)
        n_frames = -1

    wav_file = Path(str(movie_file).replace(".avi", ".wav"))
    events_path: Optional[Path] = None
    em = None
    if env.get("CRS_EVENTS") and Path(env["CRS_EVENTS"]).exists():
        events_path = Path(env["CRS_EVENTS"])

    # deterministic audio bed from the recorded events (see audio_mixer);
    # Movie Maker WAV capture proved unreliable, so events drive the mix.
    audio_cfg: Dict[str, Any] = dict(audio or {})
    audio_cfg["duration"] = float(audio_cfg.get("duration") or 0.0)
    if audio_cfg.get("sfx_enabled", True) and audio_cfg["duration"] > 0.5:
        events: List[Dict[str, Any]] = []
        if audio_cfg.get("events_path") or events_path:
            try:
                ev_path = Path(audio_cfg.get("events_path") or events_path)
                ev_data = json.loads(ev_path.read_text()) or {}
                events = ev_data.get("events", [])
                if not audio_cfg.get("vehicle_count"):
                    audio_cfg["vehicle_count"] = len(ev_data.get("vehicles", []))
            except Exception as exc:
                render_log.append(f"[audio] events unreadable ({exc}); bed without cues")
        bed_stats = audio_mixer.build_mix(
            events, audio_cfg["duration"],
            engine_volume=float(audio_cfg.get("engine_volume", 0.5)),
            sfx_volume=float(audio_cfg.get("sfx_volume", 0.8)),
            vehicle_count=int(audio_cfg.get("vehicle_count") or 0),
            out_path=work_dir / "crs_bed.wav")
        audio_cfg["sfx_wav"] = Path(bed_stats["wav_path"])
        if audio_cfg.get("music_path") and audio_cfg.get("music_enabled", True):
            duck = audio_mixer.build_duck_key(
                events, audio_cfg["duration"], out_path=work_dir / "crs_duck.wav")
            audio_cfg["duck_key_wav"] = Path(duck["duck_key_path"])
    if audio_cfg.get("music_path") and audio_cfg.get("music_enabled", True):
        audio_cfg["music_path"] = Path(audio_cfg["music_path"])
    else:
        audio_cfg["music_path"] = None

    mp4 = ff.encode_frames_to_mp4(
        Path(encoded_input) if movie_format == "png" else movie_file,
        out_mp4, fps, target_size=(output_w, output_h), audio=audio_cfg)
    info = ff.probe(mp4)
    result = {
        "mp4_path": str(mp4),
        "movie_path": str(movie_file),
        "work_dir": str(work_dir),
        "events_path": str(events_path) if events_path else None,
        "frames": n_frames,
        "duration": info["duration"],
        "fps_reported": info["fps"],
        "width": info["width"], "height": info["height"],
        "has_audio": info["has_audio"],
        "audio_bed": audio_cfg.get("sfx_wav"),
        "elapsed": elapsed,
        "avg_render_fps": (n_frames / elapsed) if (n_frames and elapsed) else None,
        "render_log": render_log[-80:],
    }
    return result
