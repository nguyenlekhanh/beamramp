"""Milestone 3.6A: safe, isolated external-GLB asset preview service.

Brings ONE user GLB into the Godot project (copied, never moved/overwritten),
runs the headless import step, then either opens an isolated preview window
or produces a machine-readable compatibility report (schema crs.asset_report/1)
parsed from Godot stdout.

This service never touches the production vehicle/track system: the preview
scene (`godot_project/scenes/asset_preview.tscn`) boots without main.tscn, so
vehicle physics, AI, camera direction, audio, recording and batch rendering
code paths are never entered.

Processing must run off the UI thread (the UI wraps these calls in
ServiceWorker).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.services import godot_service

PROJECT_ROOT = godot_service.PROJECT_ROOT
GODOT_PROJECT = godot_service.GODOT_PROJECT
USER_ASSETS_DIR = GODOT_PROJECT / "assets" / "user_assets"
REPORTS_DIR = PROJECT_ROOT / "output" / "asset_reports"
PREVIEW_SCENE_RELPATH = "scenes/asset_preview.tscn"
ASSET_SCHEMA = "crs.asset_report/1"

VERDICTS = ("load_failed", "loaded_visual_review_required", "loaded_with_warnings")

_REPORT_RE = re.compile(r"\[CRS asset\] REPORT (\{.*\})")
_ERROR_RE = re.compile(r"\[CRS asset\] ERROR (.*)")

SUPPORTED_SUFFIXES = {".glb"}          # .gltf explicitly unsupported in 3.6A


class AssetError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# validation + collision-safe copy
# ---------------------------------------------------------------------------

def validate_asset(path: str) -> Path:
    """Raise AssetError for anything this phase cannot handle; return Path."""
    src = Path(str(path).strip('"')).absolute()
    suffix = src.suffix.lower()
    if suffix in (".gltf", ".gdo"):
        raise AssetError(
            f".{suffix.lstrip('.')} assets are not supported in this phase "
            "(single-file .glb only); export again as .glb from your tool")
    if suffix not in SUPPORTED_SUFFIXES:
        raise AssetError(
            f"unsupported asset type {suffix or '(no extension)'}: "
            f"{src.name} - this phase accepts .glb files only")
    if not src.is_file():
        raise AssetError(f"asset file not found: {src}")
    if src.stat().st_size == 0:
        raise AssetError(f"asset file is empty: {src}")
    with open(src, "rb") as fh:
        magic = fh.read(4)
    if magic != b"glTF":
        raise AssetError(f"not a GLB container (bad magic bytes): {src.name}")
    return src


def _unique_dest(dest_dir: Path, basename: str) -> Path:
    """model.glb -> model.glb, then model_1.glb, model_2.glb ... never overwrite."""
    dest = dest_dir / basename
    if not dest.exists():
        return dest
    stem, dot, ext = basename.partition(".")
    idx = 1
    while True:
        dest = dest_dir / f"{stem}_{idx}{dot}{ext}"
        if not dest.exists():
            return dest
        idx += 1


def copy_asset(src: Path) -> Path:
    """Copy (never move, never overwrite) the user's asset into the project.

    Returns the copied path; the original file is left untouched.
    """
    src = validate_asset(src)
    USER_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(USER_ASSETS_DIR, src.name)
    shutil.copyfile(src, dest)
    return dest


def user_assets() -> List[Path]:
    if not USER_ASSETS_DIR.exists():
        return []
    return sorted(p for p in USER_ASSETS_DIR.glob("*.glb") if p.is_file())


def prepare_asset(asset_path: str,
                  log_cb: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Validate -> copy -> headless import (used by the UI import button).

    Never moves/deletes the user's original file; import failures raise.
    """
    src = validate_asset(asset_path)
    copied = copy_asset(src)
    if log_cb:
        log_cb(f"[asset] validated {src.name}; copied to {copied.name}")
    imp = import_assets()
    if not imp["ok"]:
        raise AssetError(
            "Godot import step failed (code %d). Log tail:\n%s"
            % (imp["returncode"], "\n".join(imp["log_tail"][-25:])))
    if log_cb:
        log_cb(f"[asset] imported {copied.name} "
               f"(headless import {imp['elapsed']:.1f}s)")
    return {"source": str(src), "copied": str(copied)}


# ---------------------------------------------------------------------------
# Godot headless import
# ---------------------------------------------------------------------------

def import_assets(timeout: int = 300, cancel_event: Optional[threading.Event] = None
                  ) -> Dict[str, Any]:
    """Run the Godot headless import pass so new files in the project become
    loadable resources. Returns {ok, returncode, elapsed, log_tail}."""
    godot = godot_service.find_godot()
    if not godot:
        raise AssetError(
            "Godot not found under ./tools or PATH - install Godot 4.4 "
            "into tools\\ or set CRS_GODOT_EXE")
    started = time.time()
    proc = subprocess.Popen(
        [str(godot), "--headless", "--import", "--path", str(GODOT_PROJECT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
    lines: List[str] = []
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                raise AssetError("import cancelled by user")
    finally:
        proc.stdout.close()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"ok": False, "returncode": proc.returncode,
                "elapsed": time.time() - started,
                "log_tail": lines[-40:] + ["<import timed out>"]}
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "elapsed": time.time() - started, "log_tail": lines[-40:]}


# ---------------------------------------------------------------------------
# report generation (headless) + parsing
# ---------------------------------------------------------------------------

def _write_config(config_path: Path, cfg: Dict[str, Any]) -> None:
    config_path.write_text(json.dumps(cfg), encoding="utf-8")


def _unique_report_path() -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    base = REPORTS_DIR / f"asset_report_{timestamp}"
    dest = Path(str(base) + ".json")
    idx = 1
    while dest.exists():
        dest = Path(f"{base}_{idx}.json")
        idx += 1
    return dest


def parse_report_line(text: str) -> Optional[Dict[str, Any]]:
    """Extract + validate the [CRS asset] REPORT json from Godot output."""
    for line in text.splitlines():
        m = _REPORT_RE.search(line.strip())
        if not m:
            continue
        try:
            doc = json.loads(m.group(1))
        except ValueError:
            raise AssetError("Godot printed a malformed asset report line")
        return validate_report_dict(doc)
    return None


def parse_error_line(text: str) -> Optional[Dict[str, Any]]:
    """Fallback parser for load failures, which print ERROR (not REPORT)."""
    for line in text.splitlines():
        m = _ERROR_RE.search(line.strip())
        if not m:
            continue
        try:
            doc = json.loads(m.group(1))
            return validate_report_dict(doc)
        except (ValueError, AssetError):
            continue
    return None


def validate_report_dict(doc: Any) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        raise AssetError("asset report is not a JSON object")
    if doc.get("schema") != ASSET_SCHEMA:
        raise AssetError(f"asset report has unexpected schema: "
                         f"{doc.get('schema')!r} (expected {ASSET_SCHEMA})")
    if doc.get("verdict") not in VERDICTS:
        raise AssetError(f"asset report verdict missing or unknown: "
                         f"{doc.get('verdict')!r}")
    return doc


def generate_report(asset_path: str, *, scale: float = 1.0,
                    pitch_deg: float = 0.0, yaw_deg: float = 0.0,
                    timeout: int = 240, skip_import: bool = False,
                    cancel_event: Optional[threading.Event] = None,
                    log_cb: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Full pipeline: validate -> copy -> headless import -> headless report.

    The returned report contains `copied_asset_path`, `python_report_path`
    and `import_log_tail` (python-side additions). Never claims success when
    the Godot import/load step failed - the report verdict carries that.
    ``skip_import`` exists for preview-cache busting tests with deliberately
    broken assets; production use always imports first (default False).
    """
    src = validate_asset(asset_path)
    copied = copy_asset(src)

    def log(msg: str) -> None:
        if log_cb:
            log_cb(msg)

    log(f"[asset] validated {src.name}; copied to {copied}")

    imp = {"ok": True, "returncode": 0, "elapsed": 0.0, "log_tail": []}
    if not skip_import:
        imp = import_assets(timeout=timeout, cancel_event=cancel_event)
        if not imp["ok"]:
            raise AssetError(
                "Godot import step failed (code %d). Log tail:\n%s"
                % (imp["returncode"], "\n".join(imp["log_tail"][-25:])))
        log(f"[asset] headless import OK ({imp['elapsed']:.1f}s)")

    work_dir = Path(tempfile.mkdtemp(prefix="crs_asset_"))
    report_path = _unique_report_path()
    config_path = work_dir / "preview_config.json"
    _write_config(config_path, {
        "glb": str(copied.resolve()),
        "copied_asset_path": str(copied.resolve()),
        "scale": scale, "pitch_deg": pitch_deg, "yaw_deg": yaw_deg,
        "mode": "report", "report_path": str(report_path.resolve()),
    })

    godot = godot_service.find_godot()
    if not godot:
        raise AssetError("Godot not found under ./tools (see docs/asset_import.md)")
    args = [str(godot), "--headless", "--path", str(GODOT_PROJECT),
            str((GODOT_PROJECT / PREVIEW_SCENE_RELPATH).resolve())]
    started = time.time()
    try:
        proc = subprocess.Popen(args, env=dict(_env_for(config_path)),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        out_lines: List[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            out_lines.append(line)
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                raise AssetError("asset report cancelled by user")
        proc.wait(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssetError("asset report generation timed out after "
                         f"{timeout}s - see docs/asset_import.md")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    caption = time.time() - started
    text = "\n".join(out_lines)

    doc = parse_report_line(text)
    if doc is None:
        doc = parse_error_line(text)          # load failures print ERROR json
    if doc is None:
        # no parseable report: surface Godot's own error output verbatim
        raise AssetError("Godot produced no asset report (exit code %d). "
                         "Log tail:\n%s" % (code, text[-40:]))
    doc["source_asset_path"] = str(src)          # user's original file
    doc["python_report_path"] = str(report_path)
    doc["import_elapsed"] = round(imp["elapsed"], 1)
    doc["preview_elapsed"] = round(caption, 1)
    # the canonical report file already exists (written by Godot); the parsed
    # version adds python-side keys:
    report_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    log(f"[asset] report: {doc['verdict']} ({len(doc.get('warnings') or [])} "
        f"warnings) -> {report_path}")
    return doc


def _env_for(config_path: Path) -> Dict[str, str]:
    env = os.environ.copy()
    env["CRS_ASSET_PREVIEW"] = str(config_path.resolve())
    return env


# ---------------------------------------------------------------------------
# live preview window
# ---------------------------------------------------------------------------

def open_preview(asset_path: str, *, scale: float = 1.0, pitch_deg: float = 0.0,
                 yaw_deg: float = 0.0, resolution: str = "800x600",
                 log_cb: Optional[Callable[[str], None]] = None
                 ) -> Dict[str, Any]:
    """Validate -> copy -> import -> launch the isolated preview window.

    Fire-and-forget: returns immediately after starting Godot (the user
    closes the window when done). Never blocks for the process to finish.
    """
    src = validate_asset(asset_path)
    copied = copy_asset(src)
    imp = import_assets()
    if not imp["ok"]:
        raise AssetError("Godot import step failed before preview; log tail:\n"
                         + "\n".join(imp["log_tail"][-25:]))

    work_dir = Path(tempfile.mkdtemp(prefix="crs_asset_"))
    config_path = work_dir / "preview_config.json"
    _write_config(config_path, {
        "glb": str(copied.resolve()),
        "copied_asset_path": str(copied.resolve()),
        "scale": scale, "pitch_deg": pitch_deg, "yaw_deg": yaw_deg,
        "mode": "window",
    })
    godot = godot_service.find_godot()
    if not godot:
        raise AssetError("Godot not found under ./tools (see docs/asset_import.md)")
    wp, hp = resolution.split("x")
    args = [str(godot), "--path", str(GODOT_PROJECT), "--resolution", f"{wp}x{hp}",
            str((GODOT_PROJECT / PREVIEW_SCENE_RELPATH).resolve())]
    proc = subprocess.Popen(args, env=_env_for(config_path),
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    if log_cb:
        log_cb(f"[asset] preview window opened for {copied.name} "
               f"(scale {scale}, yaw {yaw_deg})")
    return {"proc": proc, "copied_asset_path": str(copied),
            "source_asset_path": str(src)}


# ---------------------------------------------------------------------------
# M3.6B: external vehicle visual (opt-in via environment)
# ---------------------------------------------------------------------------

VISUAL_SCALE_MIN = 0.05
VISUAL_SCALE_MAX = 20.0


def prepare_visual_env(glb_path: str, *, scale: float = 1.0,
                       yaw_deg: float = 0.0, pitch_deg: float = 0.0,
                       auto_ground: bool = True, wheel_mode: str = "keep",
                       apply: bool = False,
                       log_cb: Optional[Callable[[str], None]] = None
                       ) -> Dict[str, str]:
    """Enable the external vehicle visual for production renders (M3.6B).

    Validates + copy-protects (never overwrite) + IMPORTS the GLB, then
    returns the environment dict that opts the Godot sim into using the
    asset as the vehicle's visible body. Because godot_service builds its
    runtime env from ``os.environ``, either pass ``apply=True`` (writes into
    the current process env) or merge the dict manually into whatever launch
    call you control (run_render / run_sim_probe inherit os.environ).

    Keys produced: CRS_VEHICLE_GLB (res:// path of the copy), CRS_VEHICLE_GLB_SCALE
    (clamped 0.05..20), CRS_VEHICLE_GLB_YAW_DEG, CRS_VEHICLE_GLB_PITCH_DEG,
    CRS_VEHICLE_GLB_AUTO_GROUND, CRS_VEHICLE_GLB_WHEELS ("keep"|"hide").
    The original GLB is never modified; scale does NOT touch the physics body.
    """
    if wheel_mode not in ("keep", "hide"):
        raise AssetError(f"wheel_mode must be 'keep' or 'hide', got {wheel_mode!r}")
    src = validate_asset(glb_path)
    copied = copy_asset(src)
    if log_cb:
        log_cb(f"[asset] validated {src.name}; visual copy at {copied}")
    imp = import_assets()
    if not imp["ok"]:
        raise AssetError("Godot import step failed before enabling the "
                         "vehicle visual; log tail:\n"
                         + "\n".join(imp["log_tail"][-25:]))
    if log_cb:
        log_cb(f"[asset] headless import OK ({imp['elapsed']:.1f}s)")
    try:
        rel = copied.resolve().relative_to(GODOT_PROJECT.resolve())
    except ValueError as exc:  # pragma: no cover - copies always live in-project
        raise AssetError(f"copied asset is outside the Godot project: {copied}") from exc
    scale = min(max(float(scale), VISUAL_SCALE_MIN), VISUAL_SCALE_MAX)
    env = {
        "CRS_VEHICLE_GLB": "res://" + rel.as_posix(),
        "CRS_VEHICLE_GLB_SCALE": f"{scale:g}",
        "CRS_VEHICLE_GLB_YAW_DEG": f"{float(yaw_deg):g}",
        "CRS_VEHICLE_GLB_PITCH_DEG": f"{float(pitch_deg):g}",
        "CRS_VEHICLE_GLB_AUTO_GROUND": "1" if auto_ground else "0",
        "CRS_VEHICLE_GLB_WHEELS": wheel_mode,
    }
    if apply:
        os.environ.update(env)
    return env
