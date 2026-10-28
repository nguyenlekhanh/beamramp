"""Thumbnail workflow: timestamp candidates + simple heuristic ranking.

Ranking is a *suggestion*: frames score better when
  * brightness is near mid-tone (avoids dark/overexposed shots)
  * local scene-change activity is higher (action > static frame)
No ML; ffmpeg signalstats + scene select only. The source video is never
modified.
"""

from __future__ import annotations

import datetime
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import ffmpeg_service as ff

THUMB_SCHEMA = "crs.thumbs/1"


class ThumbnailError(ValueError):
    """Raised for bad thumbnail requests or failing extraction runs."""


@dataclass
class ThumbCandidate:
    timestamp: float
    path: Path
    brightness: float   # signalstats YAVG, ~0 (dark)..100 (bright)
    motion: float       # 0..1 normalized scene activity near this timestamp
    rank: float = 0.0

    def as_dict(self) -> dict:
        return {"timestamp": round(self.timestamp, 3), "path": str(self.path),
                "brightness": round(self.brightness, 2),
                "motion": round(self.motion, 4), "rank": round(self.rank, 4)}


def _score(brightness: float, motion: float) -> float:
    mid = 0.5 * max(0.0, 100.0 - abs(brightness - 45.0) * 1.6)
    return mid + 40.0 * motion


def _scene_map(video: Path) -> Dict[float, float]:
    """video timestamp -> raw scene score (one extra decode pass)."""
    ffmpeg = ff.get_tools().get("ffmpeg")
    proc = ff.run_cmd(
        ffmpeg, ["-hide_banner", "-nostdin", "-i", str(video),
                 "-vf", "select='gte(scene,0)',metadata=print",
                 "-an", "-f", "null", "-"], timeout=900)
    pairs: Dict[float, float] = {}
    t_last = -1.0
    for line in (proc.stdout + proc.stderr).splitlines():
        if "pts_time:" in line:
            try:
                t_last = float(line.split("pts_time:")[-1].split()[0].strip())
            except (IndexError, ValueError):
                pass
        elif "lavfi.scene_score" in line and t_last >= 0:
            try:
                pairs[t_last] = float(line.split("=", 1)[-1])
            except ValueError:
                pass
    return pairs


def generate_candidates(video: Path, *, count: int = 6, out_dir: Path,
                        thumb_width: int = 480,
                        log_cb: Optional[Callable[[str], None]] = None,
                        progress_cb: Optional[Callable[[float], None]] = None,
                        scene_map: Optional[Dict[float, float]] = None
                        ) -> dict:
    """Scaled PNG candidates + ranked list + candidates manifest."""
    log_cb = log_cb or (lambda _l: None)
    if not 1 <= int(count) <= 12:
        raise ThumbnailError("count must be within 1..12")
    video = Path(video)
    if not video.exists():
        raise ThumbnailError(f"video missing: {video}")
    info = ff.probe(video)
    dur = float(info.get("duration") or 0.0)
    if dur < 1.0:
        raise ThumbnailError(f"video too short: {dur:.2f}s")
    ffmpeg = ff.get_tools().get("ffmpeg")
    if not ffmpeg:
        raise ThumbnailError("ffmpeg not found")

    if scene_map:
        motion_map = scene_map
    else:
        motion_map = _scene_map(video)

    stamps = [dur * (0.10 + 0.80 * (i / max(1, count - 1)))
              for i in range(count)] if count > 1 else [dur * 0.5]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates: List[ThumbCandidate] = []
    for i, t in enumerate(stamps):
        raw = out_dir / f"thumb_{i:02d}_{t:.2f}s.png"
        procx = ff.run_cmd(
            ffmpeg, ["-hide_banner", "-nostdin", "-y",
                     "-ss", f"{max(0.0, t - 0.1):.3f}", "-i", str(video),
                     "-frames:v", "1", "-vf", f"scale={thumb_width}:-2",
                     str(raw)], timeout=180)
        if procx.returncode != 0 or not raw.exists():
            log_cb(f"[thumbs] candidate {i} extraction failed; skipped")
            continue
        pb = ff.run_cmd(
            ffmpeg, ["-hide_banner", "-nostdin", "-i", str(raw),
                     "-vf", "signalstats,metadata=print",
                     "-f", "null", "-"], timeout=120)
        bright = 40.0
        for line in (pb.stdout + pb.stderr).splitlines():
            if "YAVG" in line:
                try:
                    bright = float(line.split("YAVG=")[-1].strip())
                    break
                except ValueError:
                    pass
        near = max((v for k, v in motion_map.items() if abs(k - t) <= 1.0),
                   default=0.0)
        motion = min(1.0, near * 8.0)
        candidates.append(ThumbCandidate(
            timestamp=t, path=raw, brightness=bright, motion=motion))

    for c in candidates:
        c.rank = _score(c.brightness, c.motion)
    candidates.sort(key=lambda c: -c.rank)
    manifest = {
        "schema": THUMB_SCHEMA,
        "thumbs_id": "thumbs_" + uuid.uuid4().hex[:10],
        "video": str(video),
        "duration": round(dur, 3),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "candidates": [c.as_dict() for c in candidates],
        "thumb_dir": str(out_dir),
    }
    mpath = out_dir / "thumbnail_candidates.json"
    tmp = mpath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(mpath)
    if progress_cb:
        progress_cb(1.0)
    log_cb(f"[thumbs] {len(candidates)} candidates in {out_dir}")
    manifest["manifest_path"] = str(mpath)
    return manifest


def save_selected(candidate_path: Path, dest_dir: Path,
                  name: str = "thumbnail") -> Path:
    """Copy the chosen frame as the package thumbnail (no overwrites)."""
    src = Path(candidate_path)
    if not src.exists():
        raise ThumbnailError(f"candidate frame missing: {src}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    final = dest_dir / f"{name}_{src.stem}"
    final = final.with_name(final.name + f"_{stamp}.png")
    for i in range(1, 500):
        if not final.exists():
            break
        final = final.with_name(f"{final.stem}_{i}.png")
    shutil.copy2(src, final)
    return final
