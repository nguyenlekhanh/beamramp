"""Content packages: final MP4 + thumbnail + YouTube-publish metadata.

One folder per video under ``output/packages``:
    <slug>/
      video.mp4            (copy of the final montage/clip)
      thumbnail.png        (optional, user-selected)
      metadata.json        (schema crs.package/1)
      youtube.txt          (title/description/hashtags to copy-paste)

Title, description and tags are user-supplied and echoed verbatim — this
service never invents claims about the video content and never uploads.
"""

from __future__ import annotations

import datetime
import json
import shutil
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from . import ffmpeg_service as ff

PACKAGE_SCHEMA = "crs.package/1"
PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "output" / "packages"
MAX_TAGS = 20


class PackageError(ValueError):
    """Raised when a package cannot be created."""


def slugify(text: str, limit: int = 48) -> str:
    out = "".join(ch if ch.isalnum() else "_" for ch in (text or "").strip())
    out = "_".join(out.split("_")).strip("_")[:limit] or "video"
    return out


def _next_free(package_dir: Path) -> Path:
    if not package_dir.exists():
        return package_dir
    for i in range(1, 500):
        cand = package_dir.with_name(f"{package_dir.name}_{i}")
        if not cand.exists():
            return cand
    raise PackageError("could not find a free package folder name")


def create_package(video: Path, *, title: str, description: str = "",
                   tags: Optional[List[str]] = None,
                   aspect: str = "", thumbnail: Optional[Path] = None,
                   montage_manifest: Optional[Path] = None,
                   source_jobs: Optional[List[dict]] = None,
                   settings: Optional[dict] = None,
                   out_root: Optional[Path] = None,
                   log_cb=None) -> dict:
    """Build one content package folder; returns its summary dict."""
    log = log_cb if callable(log_cb) else (lambda _l: None)
    video = Path(video)
    if not video.exists() or video.stat().st_size == 0:
        raise PackageError(f"final video missing: {video}")
    info = ff.probe(video)
    if not info.get("has_video"):
        raise PackageError(f"video has no stream: {video}")
    title = (title or "").strip()
    if not title:
        raise PackageError("title is required (user-editable; not invented)")
    if len(title) > 120:
        raise PackageError("title longer than 120 characters")
    tags = [t.strip() for t in (tags or []) if str(t).strip()]
    if len(tags) > MAX_TAGS:
        raise PackageError(f"too many tags (max {MAX_TAGS})")

    pkg = _next_free((Path(out_root) if out_root else PACKAGE_ROOT) /
                     slugify(title))
    pkg.mkdir(parents=True, exist_ok=True)

    vid_target = pkg / "video.mp4"
    shutil.copy2(video, vid_target)

    thumb_target = None
    if thumbnail is not None and Path(thumbnail).exists():
        thumb_target = pkg / "thumbnail.png"
        shutil.copy2(thumbnail, thumb_target)

    aspect_str = aspect or _guess_aspect(info)
    metadata = {
        "schema": PACKAGE_SCHEMA,
        "package_id": "pkg_" + uuid.uuid4().hex[:10],
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "title": title,
        "description": description.strip(),
        "tags": tags,
        "aspect": aspect_str,
        "duration": round(float(info.get("duration") or 0.0), 3),
        "resolution": f"{info.get('width')}x{info.get('height')}",
        "fps": info.get("fps"),
        "has_audio": bool(info.get("has_audio")),
        "video": "video.mp4",
        "thumbnail": "thumbnail.png" if thumb_target else None,
        "montage_manifest": (str(montage_manifest) if montage_manifest and
                             Path(montage_manifest).exists() else None),
        "source_jobs": list(source_jobs or []),
        "settings": dict(settings or {}),
    }
    (pkg / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    hashtags = " ".join("#" + t.lstrip("#").replace(" ", "") for t in tags)
    lines = [title, "", description.strip(), ""]
    if hashtags:
        lines += [hashtags, ""]
    (pkg / "youtube.txt").write_text("\n".join(lines), encoding="utf-8")
    log(f"[package] built {pkg.name} "
        f"({'with' if thumb_target else 'no'} thumbnail)")
    return {"package_dir": str(pkg), "metadata_path": str(pkg / "metadata.json"),
            "youtube_txt_path": str(pkg / "youtube.txt"),
            "video_path": str(vid_target),
            "thumbnail_path": str(thumb_target) if thumb_target else None,
            "package_id": metadata["package_id"]}


def _guess_aspect(info: dict) -> str:
    w = float(info.get("width") or 0)
    h = float(info.get("height") or 0)
    if not (w and h):
        return ""
    ratio = w / h
    for name, target in (("9:16", 0.5625), ("16:9", 1.7778), ("1:1", 1.0)):
        if abs(ratio - target) < 0.035:
            return name
    return f"{ratio:.3f}"
