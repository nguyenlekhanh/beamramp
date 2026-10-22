"""FFmpeg / ffprobe integration: locate, encode, verify."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


class FFmpegError(RuntimeError):
    pass


def find_executable(name: str, extra_dirs: Optional[List[Path]] = None) -> Optional[Path]:
    if extra_dirs:
        for d in extra_dirs:
            candidate = d / name
            if candidate.exists():
                return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def get_tools(extra_dirs: Optional[List[Path]] = None) -> Dict[str, Optional[Path]]:
    return {
        "ffmpeg": find_executable("ffmpeg.exe", extra_dirs),
        "ffprobe": find_executable("ffprobe.exe", extra_dirs),
    }


def run_cmd(exe: Path, args: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    createflags = 0
    if os.name == "nt":
        createflags = 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.run(
        [str(exe), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=createflags,
        timeout=timeout,
    )
    return proc


def encode_frames_to_mp4(frames_path: Path, output_mp4: Path, fps: int,
                          wav_path: Optional[Path] = None,
                          crf: int = 20, music_path: Optional[Path] = None,
                          music_volume: float = 0.35, duck_sfx: bool = False,
                          target_size: Optional[tuple] = None,
                          audio: Optional[Dict[str, Any]] = None) -> Path:
    """Encode an intermediate movie (AVI or PNG sequence) + audio into MP4.

    ``target_size`` (width, height) forces the output resolution: Windows can
    resize the capture window mid-render (e.g. 1920x1055), which would break
    the x264 yuv420p even-dimension requirement and the verify step.

    ``audio`` dict (preferred over legacy wav_path/music_path args):
      sfx_wav       Path | None   engine+SFX bed from audio_mixer
      music_path    Path | None   user music (any format ffmpeg reads)
      duck_key_wav  Path | None   mono duck envelope from audio_mixer
      music_volume  float 0..1
      duration      float         final audio length in seconds
    """
    ffmpeg = get_tools().get("ffmpeg")
    if not ffmpeg:
        raise FFmpegError("ffmpeg not found. Install: winget install Gyan.FFmpeg (then restart shell)")

    cfg = audio or {}
    sfx_wav: Optional[Path] = cfg.get("sfx_wav", wav_path)
    must_music: Optional[Path] = cfg.get("music_path") or music_path
    duck_key: Optional[Path] = cfg.get("duck_key_wav")
    mus_vol: float = float(cfg.get("music_volume", music_volume))
    duration: Optional[float] = cfg.get("duration")
    duration = duration if duration and duration > 0.5 else None

    args: List[str] = ["-y", "-hide_banner", "-loglevel", "error"]
    if frames_path.suffix.lower() == ".png":
        args += ["-framerate", str(fps), "-i", str(frames_path)]
    else:
        args += ["-i", str(frames_path)]

    if sfx_wav is not None:
        args += ["-i", str(sfx_wav)]          # input 1: engine+SFX bed
    if must_music:
        args += ["-stream_loop", "-1", "-i", str(must_music)]  # input 2: music
        if duck_key:
            args += ["-i", str(duck_key)]     # input 3: duck envelope key

    def _d() -> str:
        return f"{duration:.3f}"

    if sfx_wav is not None and must_music:
        if duck_key:
            args += ["-filter_complex",
                     "[1:a]atrim=end=%s[bed];"
                     "[2:a]volume=%.3f[mpre];"
                     "[3:a]aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=mono[key];"
                     "[mpre][key]sidechaincompress=threshold=0.030:ratio=8:attack=90:release=380:makeup=1[mus];"
                     "[mus]afade=t=in:d=0.8,afade=t=out:st=%.3f:d=0.8,atrim=end=%s[muf];"
                     "[bed][muf]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=disabled[aout]"
                     % (_d(), mus_vol, _d_end_fade(duration), _d()),
                     "-map", "0:v", "-map", "[aout]"]
        else:
            args += ["-filter_complex",
                     "[1:a]atrim=end=%s[bed];"
                     "[2:a]volume=%.3f,afade=t=in:d=0.8,afade=t=out:st=%.3f:d=0.8,atrim=end=%s[muf];"
                     "[bed][muf]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=disabled[aout]"
                     % (_d(), mus_vol, _d_end_fade(duration), _d()),
                     "-map", "0:v", "-map", "[aout]"]
    elif sfx_wav is not None:
        args += ["-filter_complex",
                 "[1:a]atrim=end=%s,afade=t=out:st=%.3f:d=0.4[aout]"
                 % (_d(), _d_end_fade(duration, fade=0.4)),
                 "-map", "0:v", "-map", "[aout]"]
    elif must_music:
        if duck_key:
            # music is input 1 and the duck key is input 2 in this branch
            args += ["-filter_complex",
                     "[1:a]volume=%.3f[mpre];"
                     "[2:a]aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=mono[key];"
                     "[mpre][key]sidechaincompress=threshold=0.030:ratio=8:attack=90:release=380:makeup=1,"
                     "afade=t=in:d=0.8,afade=t=out:st=%.3f:d=0.8,atrim=end=%s,"
                     "alimiter=limit=0.95:level=disabled[aout]"
                     % (mus_vol, _d_end_fade(duration), _d()),
                     "-map", "0:v", "-map", "[aout]"]
        else:
            args += ["-filter_complex",
                     "[1:a]volume=%.3f,afade=t=in:d=0.8,afade=t=out:st=%.3f:d=0.8,"
                     "atrim=end=%s,alimiter=limit=0.95:level=disabled[aout]"
                     % (mus_vol, _d_end_fade(duration), _d()),
                     "-map", "0:v", "-map", "[aout]"]
    else:
        # silent audio track so the MP4 always has a consistent stream
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                 "-shortest", "-map", "0:v", "-map", "1:a"]
    args += [
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
    ]
    if target_size is not None:
        tw, th = int(target_size[0]), int(target_size[1])
        args += ["-vf", f"scale={tw}:{trunc_even(th)}:flags=bicubic"]
    args += [
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-r", str(fps),
        str(output_mp4),
    ]
    proc = run_cmd(ffmpeg, args)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg encode failed: {proc.stderr.strip()[:800]}")
    return output_mp4


def _d_end_fade(duration: Optional[float], fade: float = 0.8) -> float:
    """Start time for a fade-out ending ``fade`` seconds before the end."""
    if not duration or duration <= fade + 0.2:
        return 0.0
    return max(0.0, duration - fade)


def trunc_even(value: int) -> int:
    """Round down to an even pixel count (yuv420p requires even dimensions)."""
    return value - (value % 2)


def probe(path: Path) -> Dict[str, Any]:
    """ffprobe the file; returns {duration, width, height, fps, has_video, has_audio, codec}."""
    ffprobe = get_tools().get("ffprobe")
    if not ffprobe:
        raise FFmpegError("ffprobe not found")
    args = ["-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = run_cmd(ffprobe, args, timeout=120)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {proc.stderr.strip()[:400]}")
    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format", {})
    info: Dict[str, Any] = {
        "duration": float(fmt.get("duration", 0.0)),
        "size_bytes": int(fmt.get("size", 0)),
        "has_video": False,
        "has_audio": False,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "video_codec": "",
        "audio_codec": "",
    }
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not info["has_video"]:
            info["has_video"] = True
            info["width"] = int(stream.get("width", 0))
            info["height"] = int(stream.get("height", 0))
            rate = stream.get("avg_frame_rate", "0/1")
            num, _, den = rate.partition("/")
            den_i = int(den) if den.isdigit() and int(den) else 1
            info["fps"] = int(num) / den_i if str(num).isdigit() else 0.0
            info["video_codec"] = stream.get("codec_name", "")
        elif stream.get("codec_type") == "audio" and not info["has_audio"]:
            info["has_audio"] = True
            info["audio_codec"] = stream.get("codec_name", "")
    return info


def verify_output(path: Path, expected_w: int, expected_h: int, expected_fps: float,
                  min_duration: float = 0.5, want_audio: bool = True) -> Optional[str]:
    """Returns None if OK, else a problem description string."""
    if not path.exists() or path.stat().st_size == 0:
        return "output file missing or zero bytes"
    info = probe(path)
    if not info["has_video"]:
        return "no video stream"
    if info["video_codec"] != "h264":
        return f"unexpected video codec: {info['video_codec']}"
    if abs(info["width"] - expected_w) > 8 or abs(info["height"] - expected_h) > 8:
        return f"resolution {info['width']}x{info['height']} != expected {expected_w}x{expected_h}"
    if info["duration"] < min_duration:
        return f"duration too short: {info['duration']:.2f}s"
    if want_audio and not info["has_audio"]:
        return "no audio stream (expected SFX/music)"
    return None
