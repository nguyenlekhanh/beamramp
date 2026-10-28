"""Montage assembly: joins verified clips into one polished MP4.

Source of truth for inputs = batch manifests: only jobs whose recorded
status is 'succeeded' AND whose file re-probes healthy are usable.

FFmpeg filter-graph based (no per-frame Python decoding):
  * every clip is normalized to the montage fps/size/pixfmt first
  * fit policy 'pad' (fit + letterbox box, never distorts) or 'crop'
  * transitions: hard 'cut' (concat) or short 'fade' (xfade chain),
    audio: acrossfade for fades, concat for cuts
  * clips without an audio stream get a silent filler so A/V never drifts
  * optional background music overlay (explicit user file only)
"""

from __future__ import annotations

import datetime
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import ffmpeg_service as ff
from ..render_queue.jobs import BatchManifest

MONTAGE_SCHEMA = "crs.montage/1"
DEFAULT_FADE = 0.5
MIN_CLIP_SECONDS = 1.0
SUPPORTED_TRANSITIONS = ("cut", "fade")
SUPPORTED_FIT = ("pad", "crop")
CRF_BY_QUALITY = {"low": 23, "medium": 20, "high": 17}

# width/height by (aspect, resolution key); even numbers only.
MONTAGE_SIZES = {
    "9:16": {"720p": (720, 1280), "1080p": (1080, 1920)},
    "16:9": {"720p": (1280, 720), "1080p": (1920, 1080)},
    "1:1": {"720p": (720, 720), "1080p": (1080, 1080)},
    "4:5": {"720p": (864, 1080), "1080p": (1080, 1350)},
}
SUPPORTED_RESOLUTIONS = ("720p", "1080p")

LogCb = Optional[Callable[[str], None]]


class MontageError(ValueError):
    """Raised for invalid montage configurations or rejected outputs."""


class MontageInputError(MontageError):
    """A specific clip is missing, corrupt or incompatible."""


@dataclass
class ClipSpec:
    path: Path
    label: str = ""
    source: dict = field(default_factory=dict)
    info: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return float(self.info.get("duration", 0.0))

    @property
    def has_audio(self) -> bool:
        return bool(self.info.get("has_audio"))


@dataclass
class MontageConfig:
    aspect: str = "16:9"
    resolution: str = "720p"
    fps: int = 30
    quality: str = "high"
    transition: str = "cut"
    fade_duration: float = DEFAULT_FADE
    fit_policy: str = "pad"
    music_path: Optional[str] = None
    music_volume: float = 0.35

    @property
    def out_size(self) -> Tuple[int, int]:
        by_res = MONTAGE_SIZES.get(self.aspect)
        if by_res is None:
            raise MontageError(f"unsupported aspect: {self.aspect!r}")
        size = by_res.get(self.resolution)
        if size is None:
            raise MontageError(
                f"unsupported resolution {self.resolution!r} "
                f"for aspect {self.aspect!r}")
        return size


def clips_from_manifest(manifest: BatchManifest, *,
                        recheck: bool = False,
                        log_cb: LogCb = None) -> List[ClipSpec]:
    """Verified, manifest-ordered clips. Only 'succeeded' jobs are candidates.

    Recheck compares ffprobe dimensions/fps against the recorded verify data
    so a stale or swapped file is caught instead of silently used.
    """
    clips: List[ClipSpec] = []
    for job in manifest.jobs:
        if job.status != "succeeded":
            continue  # failed/cancelled jobs are never included by default
        label = job.name or job.job_id
        path = Path(job.output_path or "")
        if not path.name or not path.exists():
            raise MontageInputError(
                f"clip for '{label}' is missing on disk: {path}")
        try:
            info = ff.probe(path)
        except ff.FFmpegError as exc:
            raise MontageInputError(f"clip for '{label}' is unreadable: {exc}") from exc
        if not info.get("has_video"):
            raise MontageInputError(f"clip for '{label}' has no video stream")
        if recheck:
            recorded = job.verify or {}
            for key in ("width", "height", "fps"):
                if recorded.get(key) and abs(float(recorded[key]) - float(info[key])) > 40:
                    raise MontageInputError(
                        f"clip for '{label}' changed: {key} "
                        f"{info.get(key)} vs recorded {recorded.get(key)}")
        clips.append(ClipSpec(
            path=path, label=label,
            source={"batch_id": manifest.batch_id, "job_id": job.job_id,
                    "template_id": job.template_id, "seed": job.seed,
                    "name": job.name},
            info=info))
    if not clips:
        raise MontageInputError("this batch has no succeeded jobs with playable files")
    return clips


def validate_clip_specs(clips: List[ClipSpec], cfg: MontageConfig) -> None:
    if not clips:
        raise MontageError("at least one clip is required")
    if len(clips) > 20:
        raise MontageError("too many clips (max 20)")
    if cfg.transition not in SUPPORTED_TRANSITIONS \
            or cfg.fit_policy not in SUPPORTED_FIT:
        raise MontageError(
            f"transition must be in {SUPPORTED_TRANSITIONS}, fit in {SUPPORTED_FIT}")
    if int(cfg.fps) not in (24, 30, 60):
        raise MontageError(f"fps must be 24/30/60 (got {cfg.fps})")
    if cfg.quality not in CRF_BY_QUALITY:
        raise MontageError(f"quality must be low/medium/high (got {cfg.quality!r})")
    if not 0.0 <= float(cfg.music_volume) <= 1.0:
        raise MontageError("music_volume must be within 0..1")
    if len(clips) > 1 and cfg.transition == "fade":
        if not 0.15 <= float(cfg.fade_duration) <= 1.0:
            raise MontageError("fade_duration must be within 0.15..1.0 s")
        shortest = min(c.duration for c in clips)
        if shortest <= cfg.fade_duration + 0.3:
            raise MontageError(
                f"shortest clip ({shortest:.2f}s) too short for a "
                f"{cfg.fade_duration}s fade; use 'cut' or a shorter fade")
    for c in clips:
        if c.duration < MIN_CLIP_SECONDS:
            raise MontageInputError(
                f"clip '{c.label}' too short: {c.duration:.2f}s "
                f"(min {MIN_CLIP_SECONDS}s)")


def estimate_duration(clips: List[ClipSpec], cfg: MontageConfig) -> float:
    """Video-timeline duration estimate before rendering."""
    total = sum(c.duration for c in clips)
    if len(clips) > 1 and cfg.transition == "fade":
        total -= (len(clips) - 1) * float(cfg.fade_duration)
    return max(0.0, total)


def _unique_out(path: Path) -> Path:
    if not path.exists():
        return path
    stem, ext = path.stem, path.suffix
    for i in range(1, 500):
        cand = path.with_name(f"{stem}_{i}{ext}")
        if not cand.exists():
            return cand
    raise MontageError("could not find a free output name")


def _norm_video(i: int, w: int, h: int, fps: int, policy: str,
                include_setpts: bool = True) -> str:
    """Uniform normalization; `pad` letterboxes, `crop` center-crops; no stretch.

    `include_setpts=False` is required for xfade inputs: setpts resets the
    stream's frame-rate metadata to 1/0 and xfade then refuses CFR inputs.
    """
    if policy == "crop":
        scale = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    else:
        scale = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                 f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    tail = ",setpts=PTS-STARTPTS" if include_setpts else ""
    return f"[{i}:v]fps={fps},{scale},setsar=1,format=yuv420p{tail}[v{i}]"


def _norm_audio(i: int, has_audio: bool, dur: float) -> str:
    """Uniform 48k stereo chain; silent filler when the clip lacks audio."""
    if has_audio:
        # NOTE: no comma right after an input label (ffmpeg parse error).
        return (f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates="
                f"48000:channel_layouts=stereo,atrim=duration={dur:.3f},"
                f"apad=whole_dur={dur:.3f},asetpts=PTS-STARTPTS[a{i}]")
    return ("anullsrc=r=48000:cl=stereo,aresample=48000,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"atrim=duration={dur:.3f},apad=whole_dur={dur:.3f},"
            f"asetpts=PTS-STARTPTS[a{i}]")


def _output_rms(path: Path) -> str:
    ffmpeg = ff.get_tools().get("ffmpeg")
    if not ffmpeg:
        return "?"
    proc = ff.run_cmd(
        ffmpeg, ["-hide_banner", "-i", str(path), "-map", "0:a",
                 "-af", "astats=metadata=1:measure_overall=RMS_level:"
                        "measure_perchannel=none",
                 "-f", "null", "-"], timeout=300)
    for ln in (proc.stdout + proc.stderr).splitlines():
        if "RMS level dB" in ln:
            return ln.split(":")[1].strip()
    return "?"


def build_montage(clips: List[ClipSpec], cfg: MontageConfig, *,
                  out_dir: Path, batch_ids: Optional[List[str]] = None,
                  name: str = "", log_cb: LogCb = None,
                  progress_cb: Optional[Callable[[float], None]] = None
                  ) -> dict:
    """Assemble one montage MP4 + montage manifest from verified clips."""
    log_cb = log_cb or (lambda _l: None)
    validate_clip_specs(clips, cfg)
    ffmpeg = ff.get_tools().get("ffmpeg")
    if not ffmpeg:
        raise MontageError("ffmpeg not found")

    w, h = cfg.out_size
    eve_w, eve_h = ff.trunc_even(w), ff.trunc_even(h)
    est = estimate_duration(clips, cfg)
    n = len(clips)
    fade = float(cfg.fade_duration) if cfg.transition == "fade" else 0.0
    out_name = name or f"montage_{datetime.datetime.now():%Y%m%d_%H%M%S}.mp4"
    if not out_name.lower().endswith(".mp4"):
        out_name += ".mp4"
    out_path = _unique_out(Path(out_dir) / out_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if est < 0.5:
        raise MontageError(f"montage too short: {est:.2f}s")

    # ---- inputs & normalization chains ---------------------------------
    inputs: List[str] = []
    for c in clips:
        inputs += ["-i", str(c.path)]
    music_idx = n
    music_meta = None
    if cfg.music_path and cfg.music_path.strip():
        music = Path(cfg.music_path)
        if not music.exists():
            raise MontageInputError(f"music file missing: {music}")
        music_meta = ff.probe(music)
        if not (music_meta.get("has_audio") or music_meta.get("audio_codec")):
            raise MontageInputError(f"music file has no audio stream: {music}")
        inputs += ["-i", str(music)]

    vparts = [_norm_video(i, eve_w, eve_h, cfg.fps, cfg.fit_policy)
              for i in range(n)]
    dur_list = [c.duration for c in clips]
    apart = [_norm_audio(i, clips[i].has_audio, dur_list[i]) for i in range(n)]

    if n == 1 or cfg.transition == "cut":
        if n > 1:
            vparts.append("".join(f"[v{i}]" for i in range(n))
                          + f"concat=n={n}:v=1:a=0[vcat]")
            apart.append("".join(f"[a{i}]" for i in range(n))
                         + f"concat=n={n}:v=0:a=1[acat]")
        vmain, amain = ("[v0]", "[a0]") if n == 1 else ("[vcat]", "[acat]")
    else:
        if fade <= 0.0:
            raise MontageError("fade transition needs fade_duration > 0")
        # xfade path: NO setpts before xfade (kills frame-rate metadata).
        vparts = [_norm_video(i, eve_w, eve_h, cfg.fps, cfg.fit_policy,
                              include_setpts=False) for i in range(n)]
        last_v, last_a = "v0", "a0"
        acc = dur_list[0]
        for j in range(1, n):
            vparts.append(f"[{last_v}][v{j}]xfade=transition=fade:"
                          f"duration={fade:.3f}:offset={acc - fade:.3f}[vx{j}]")
            apart.append(f"[{last_a}][a{j}]acrossfade=d={fade:.3f}"
                         f":c1=tri:c2=tri[ax{j}]")
            acc += dur_list[j] - fade
            last_v, last_a = f"vx{j}", f"ax{j}"
        vmain, amain = f"[{last_v}]", f"[{last_a}]"

    # ---- music overlay --------------------------------------------------
    if cfg.music_path and cfg.music_path.strip():
        fade_out = min(1.0, est / 4.0)
        apart.append(
            f"[{music_idx}:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates="
            f"48000:channel_layouts=stereo,atrim=duration={est:.3f},"
            f"volume={float(cfg.music_volume):.4g},apad,"
            f"afade=out:st={max(0.0, est - fade_out):.3f}:d={fade_out:.3f}[am]")
        apart.append(f"{amain}[am]amix=inputs=2:duration=first:"
                     f"dropout_transition=0:normalize=0[amix2]")
        amain = "[amix2]"

    d_end = max(0.05, est - 0.05)
    vparts.append(f"{vmain}trim=duration={d_end:.3f},setpts=PTS-STARTPTS[vfin]")
    apart.append(f"{amain}atrim=duration={d_end:.3f},asetpts=PTS-STARTPTS[afin]")
    filters = ";".join(p for p in (vparts + apart) if p)

    args = ["-y", "-hide_banner", "-nostdin"] + inputs + [
        "-filter_complex", filters,
        "-map", "[vfin]", "-map", "[afin]",
        "-c:v", "libx264", "-preset", "medium",
        "-crf", str(CRF_BY_QUALITY[cfg.quality]),
        "-r", str(cfg.fps), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart", str(out_path)]
    log_cb(f"[montage] building {out_path.name} ({n} clips, est {est:.1f}s)")
    if progress_cb:
        progress_cb(0.05)
    proc = ff.run_cmd(ffmpeg, args, timeout=2400)
    if proc.returncode != 0:
        raise MontageError("montage encode failed:\n" + proc.stderr[-1200:])
    if progress_cb:
        progress_cb(0.85)

    # ---- verify the final file ------------------------------------------
    info = ff.probe(out_path)
    err = ff.verify_output(out_path, expected_w=eve_w, expected_h=eve_h,
                           expected_fps=cfg.fps,
                           min_duration=min(0.8, est), want_audio=True)
    if err:
        raise MontageError("montage verification failed: " + err)
    rms = _output_rms(out_path)
    measured = float(info.get("duration") or 0.0)
    if abs(measured - est) > 1.5:
        log_cb(f"[montage] WARNING measured {measured:.2f}s vs "
               f"estimate {est:.2f}s")

    manifest = {
        "schema": MONTAGE_SCHEMA,
        "montage_id": "montage_" + uuid.uuid4().hex[:10],
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "out_path": str(out_path),
        "out_dir": str(out_path.parent),
        "name": out_path.name,
        "batch_ids": list(batch_ids or []),
        "config": {
            "aspect": cfg.aspect, "resolution": cfg.resolution,
            "fps": int(cfg.fps), "quality": cfg.quality,
            "transition": cfg.transition,
            "fade_duration": float(cfg.fade_duration),
            "fit_policy": cfg.fit_policy,
            "music_path": (str(cfg.music_path) if cfg.music_path else None),
            "music_volume": float(cfg.music_volume),
        },
        "inputs": [
            {"clip_path": str(c.path), "label": c.label,
             "source": dict(c.source), "duration": round(c.duration, 3),
             "has_audio": c.has_audio,
             "verify": {k: c.info.get(k) for k in ("width", "height", "fps")}}
            for c in clips
        ],
        "estimate_duration": round(est, 3),
        "measured_duration": round(measured, 3),
        "audio_rms_db": rms,
        "verified": {
            "ok": True,
            "width": info.get("width"), "height": info.get("height"),
            "fps": info.get("fps"), "has_audio": info.get("has_audio"),
        },
    }
    mpath = out_path.with_suffix(".montage.json")
    tmp = mpath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, mpath)
    if progress_cb:
        progress_cb(1.0)
    log_cb(f"[montage] OK {out_path.name} ({measured:.1f}s, RMS {rms} dB)")
    manifest["manifest_path"] = str(mpath)
    return manifest
