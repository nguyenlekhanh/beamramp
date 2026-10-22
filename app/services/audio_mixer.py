"""Deterministic audio post-mix for ColorRush Studio exports.

Movie Maker never writes a reliable WAV sidecar (audited: work dirs contain
AVI only), so the export audio is synthesized here at 48 kHz stereo from
``events.json`` — sample-accurate against the video timeline because video
time == simulation time (slow-mo catch-up happens inside sim time).

The bed reuses the original synthesized assets in godot_project/assets/audio
(decoded with stdlib ``wave``) and fills any missing asset with a synthesized
equivalent, so a broken/missing file can never crash a render.

Timeline == ``events.json`` timestamps in seconds. All gains are linear
(0..1). Output is peak-limited once; deterministic given the same events.
"""
import math
import struct
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SAMPLE_RATE = 48000
TWO_PI = 2.0 * math.pi

DEFAULT_GAINS: Dict[str, float] = {
    "engine": 0.5,
    "sfx": 0.8,
    "music": 0.35,
}

# relative level of each cue inside its bus
_SRC_GAINS: Dict[str, float] = {
    "engine": 0.6,    # per vehicle; bus gain scales on top
    "launch": 0.5,
    "land": 0.6,
    "crash": 0.9,
    "fall": 0.55,
    "finish": 0.58,
}

# events below these thresholds stay silent (mirrors camera/slowmo routing)
MIN_LAND_IMPACT = 6.0
MIN_CRASH_SEVERITY = 6.0

# event type -> (duck_depth_db, hold, release, threshold)
DUCK_EVENTS: Dict[str, Tuple[float, float, float, float]] = {
    "launch": (-4.0, 0.35, 0.9, 0.0),
    "land": (-5.0, 0.5, 1.1, 11.0),
    "crash": (-6.0, 0.6, 1.3, 6.0),
    "fall": (-5.0, 0.5, 1.2, 0.0),
}


def _asset_path(name: str) -> Path:
    here = Path(__file__).resolve().parents[2]
    return here / "godot_project" / "assets" / "audio" / name


def _decode_wav(path: Path) -> Tuple[int, List[float]]:
    """Decode PCM16/PCM8 WAV to mono float at its native rate.

    Missing or corrupt files yield (0, []) — callers synthesize instead.
    """
    try:
        with wave.open(str(path)) as w:
            rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
            raw = w.readframes(w.getnframes())
        if width == 2:
            count = len(raw) // 2
            ints = struct.unpack("<%dh" % count, raw[: count * 2])
        elif width == 1:
            ints = [s - 128 for s in raw]
        else:
            return rate, []
        step = max(ch, 1)
        return rate, [s / 32768.0 for s in ints[::step]]
    except Exception:
        return 0, []


def _resample_to_mix_rate(mono: List[float], src_rate: int) -> List[float]:
    if src_rate == SAMPLE_RATE or not mono or src_rate <= 0:
        return list(mono)
    out_len = max(1, int(round(len(mono) * SAMPLE_RATE / src_rate)))
    out: List[float] = [0.0] * out_len
    step = src_rate / SAMPLE_RATE
    last = len(mono) - 1
    for i in range(out_len):
        p = i * step
        i0 = int(p)
        if i0 >= last:
            out[i] = mono[last]
        else:
            f = p - i0
            out[i] = mono[i0] * (1.0 - f) + mono[i0 + 1] * f
    return out


# ---------------------------------------------------------------- synth beds

def _synth_engine() -> List[float]:
    """1.6 s two-cylinder hum loop, edges crossfaded for seamless looping."""
    dur, n = 1.6, int(1.6 * SAMPLE_RATE)
    out = [0.0] * n
    for i in range(n):
        t = i / SAMPLE_RATE
        rev = (t / dur) * 3.0
        cy1 = 0.55 * math.sin(TWO_PI * (70.0 + 34.0 * rev) * t)
        cy2 = 0.35 * math.sin(TWO_PI * (166.0 + 46.0 * rev) * t + 1.3)
        rumble = 0.5 * math.sin(TWO_PI * 43.0 * t) + 0.2 * math.sin(TWO_PI * 21.5 * t)
        out[i] = 0.5 * (cy1 + cy2) + 0.35 * rumble
    _fade_edges(out, 0.03, loop=True)
    return out


def _synth_whoosh() -> List[float]:
    """0.9 s band-noise sweep (takeoff airborne cue)."""
    n = int(0.9 * SAMPLE_RATE)
    out = [0.0] * n
    state, lp = 12345, 0.0
    for i in range(n):
        frac = i / n
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        noise = state / 0x7FFFFFFF - 1.0
        center = 400.0 + 2400.0 * (0.5 - 0.5 * math.cos(TWO_PI * frac))
        alpha = min(0.98, TWO_PI * center / SAMPLE_RATE)
        lp += alpha * (noise - lp)
        out[i] = 0.8 * (noise - lp) * math.sin(TWO_PI * frac) ** 2
    return out


def _synth_thud() -> List[float]:
    """0.45 s landing thump: pitch-dropping sine + damped noise attack."""
    dur, n = 0.45, int(0.45 * SAMPLE_RATE)
    out = [0.0] * n
    state, lp = 987654, 0.0
    for i in range(n):
        t, k = i / SAMPLE_RATE, i / n
        env = math.exp(-k * 7.5)
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        noise = state / 0x7FFFFFFF - 1.0
        alpha = 0.35 if k < 0.06 else 0.05
        lp += alpha * (noise - lp)
        out[i] = env * (0.9 * math.sin(TWO_PI * 70.0 * t * math.exp(-k * 1.5)) + 0.55 * lp)
    return out


def _synth_impact() -> List[float]:
    """0.35 s sharp crash burst (HP noise + ringing partials)."""
    dur, n = 0.35, int(0.35 * SAMPLE_RATE)
    out = [0.0] * n
    state, lp = 424242, 0.0
    for i in range(n):
        t, k = i / SAMPLE_RATE, i / n
        env = math.exp(-k * 9.0)
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        noise = state / 0x7FFFFFFF - 1.0
        lp += 0.55 * (noise - lp)
        hp = noise - lp
        ring = 0.4 * math.sin(TWO_PI * 320.0 * t) + 0.25 * math.sin(TWO_PI * 517.0 * t)
        out[i] = env * (0.75 * hp + 0.4 * ring)
    return out


def _synth_finish() -> List[float]:
    """0.7 s two-tone completion blip."""
    dur, n = 0.7, int(0.7 * SAMPLE_RATE)
    out = [0.0] * n
    for i in range(n):
        t = i / SAMPLE_RATE
        env = math.exp(-(t / dur) * 4.0) * min(1.0, t / 0.02)
        freq = 660.0 if t < 0.22 else 880.0
        out[i] = 0.5 * env * math.sin(TWO_PI * freq * t)
    return out


def _fade_edges(samples: List[float], fade: float, loop: bool) -> None:
    nf = min(int(fade * SAMPLE_RATE), (len(samples) - 1) // 2)
    if nf <= 0:
        return
    for i in range(nf):
        g = i / nf
        samples[i] *= g
        if loop:
            samples[-1 - i] *= g


def _asset(name: str, synth) -> List[float]:
    rate, mono = _decode_wav(_asset_path(name))
    if mono:
        return _resample_to_mix_rate(mono, rate)
    return synth()


# ------------------------------------------------------------------- panning

def _pan_eq(pan: float) -> Tuple[float, float]:
    """Equal-power pan; pan in -1..+1 returns (left, right) gains."""
    a = (pan + 1.0) * math.pi / 4.0
    return math.cos(a), math.sin(a)


# --------------------------------------------------------------------- mixer

class Mixer:
    """Accumulates one 48k stereo float mix; perfect for events.json cues."""

    def __init__(self, n_frames: int):
        self.n = n_frames
        self.left: List[float] = [0.0] * n_frames
        self.right: List[float] = [0.0] * n_frames

    def add_loop(self, mono: List[float], from_i: int, to_i: int,
                 gain: float, pan: float) -> None:
        """Add ``mono`` looping into [from_i, to_i) with 50 ms edge fades."""
        if gain <= 0.0 or not mono or to_i <= from_i:
            return
        eq_l, eq_r = _pan_eq(pan)
        total, ramp = len(mono), SAMPLE_RATE // 20
        span = to_i - from_i
        for dst in range(from_i, min(to_i, self.n)):
            local = dst - from_i
            s = mono[local % total] * gain
            if local < ramp or span - local < ramp:
                s *= 0.25
            self.left[dst] += s * eq_l
            self.right[dst] += s * eq_r

    def add_cue(self, mono: List[float], at: int, gain: float, pan: float) -> None:
        if gain <= 0.0 or not mono or at >= self.n:
            return
        eq_l, eq_r = _pan_eq(pan)
        for j, s0 in enumerate(mono):
            dst = at + j
            if dst >= self.n:
                break
            self.left[dst] += s0 * gain * eq_l
            self.right[dst] += s0 * gain * eq_r

    def write(self, path: Path, limit: float = 0.94) -> Tuple[float, float, float]:
        """Write 48k stereo PCM16 WAV with one deterministic peak scale."""
        peak = max(
            max((abs(v) for v in self.left), default=0.0),
            max((abs(v) for v in self.right), default=0.0),
        )
        scale = min(1.0, limit / peak) if peak > limit else 1.0
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            for i in range(self.n):
                for v in (self.left[i], self.right[i]):
                    x = int(max(-32768, min(32767, round(v * scale * 32767.0))))
                    w.writeframesraw(struct.pack("<h", x))
        sq = 0.0
        for i in range(self.n):
            sq += self.left[i] * self.left[i] + self.right[i] * self.right[i]
        rms = math.sqrt(sq / (2 * self.n)) * scale if self.n else 0.0
        return rms, peak * scale, scale


def _pan_for(order: int) -> float:
    """Vehicle 0 leans slightly left, vehicle 1 slightly right, rest centered."""
    return {-1: 0.0, 0: -0.35, 1: 0.35}.get(order, 0.0)


def build_mix(events: List[Dict[str, Any]], duration: float,
              engine_volume: float = DEFAULT_GAINS["engine"],
              sfx_volume: float = DEFAULT_GAINS["sfx"],
              vehicle_count: Optional[int] = None,
              out_path: Optional[Path] = None) -> Dict[str, Any]:
    """Render the engine+SFX bed WAV for one scenario.

    ``duration`` in seconds == final video duration.
    ``vehicle_count`` guarantees engine loops even with zero events.
    Returns stats dict; ``stats["wav_path"]`` holds the written WAV.
    """
    n = int(round(duration * SAMPLE_RATE)) + SAMPLE_RATE // 4  # 0.25 s tail
    mix = Mixer(n)
    mix.buffers: dict = {
        "launch": _asset("whoosh.wav", _synth_whoosh),
        "land": _asset("land_thud.wav", _synth_thud),
        "crash": _asset("impact_hit.wav", _synth_impact),
        "finish": _asset("finish_chime.wav", _synth_finish),  # missing -> synth
    }

    veh_order: Dict[int, int] = {}
    launch_seen: set = set()
    finish_at: Dict[int, float] = {}
    launch_count = land_count = crash_count = fall_count = finish_count = 0
    for ev in events:
        t = float(ev.get("t", 0.0))
        if t >= duration:
            continue
        v = int(ev.get("vehicle", 0))
        if v not in veh_order:
            veh_order[v] = len(veh_order)
        pan = _pan_for(veh_order[v])
        etype = str(ev.get("type", ""))
        if etype == "launch":
            if v in launch_seen:
                continue
            launch_seen.add(v)
            mix.add_cue(mix.buffers["launch"], int(t * SAMPLE_RATE),
                        _SRC_GAINS["launch"] * sfx_volume, pan)
            launch_count += 1
        elif etype == "land":
            imp = float(ev.get("impact_speed", 0.0))
            if imp <= MIN_LAND_IMPACT:
                continue
            scale = min(1.0, 0.4 + imp / 30.0)
            mix.add_cue(mix.buffers["land"], int(t * SAMPLE_RATE),
                        _SRC_GAINS["land"] * sfx_volume * scale, pan)
            land_count += 1
        elif etype == "crash":
            sev = float(ev.get("severity", 0.0))
            if sev <= MIN_CRASH_SEVERITY:
                continue
            scale = min(1.0, 0.35 + sev / 20.0)
            mix.add_cue(mix.buffers["crash"], int(t * SAMPLE_RATE),
                        _SRC_GAINS["crash"] * sfx_volume * scale, pan)
            crash_count += 1
        elif etype == "fall":
            mix.add_cue(mix.buffers["land"], int(t * SAMPLE_RATE),
                        _SRC_GAINS["fall"] * sfx_volume, pan)
            fall_count += 1
        elif etype == "finish":
            if v in finish_at:
                continue
            finish_at[v] = t
            mix.add_cue(mix.buffers["finish"], int(t * SAMPLE_RATE),
                        _SRC_GAINS["finish"] * sfx_volume, pan)
            finish_count += 1

    eng_gain = _SRC_GAINS["engine"] * engine_volume
    # engines always run per vehicle seen or declared (events may be empty)
    n_veh = len(veh_order)
    for i in veh_order:  # veh_order keys are vehicle indices
        n_veh = max(n_veh, i + 1)
    for v in range(max(n_veh, vehicle_count or 0)):
        stop = min(finish_at.get(v, duration), duration)
        mix.add_loop(_asset("engine_loop.wav", _synth_engine),
                     0, int(stop * SAMPLE_RATE),
                     eng_gain, _pan_for(v))

    stats: Dict[str, Any] = {
        "duration": duration,
        "launch": launch_count, "land": land_count, "crash": crash_count,
        "fall": fall_count, "finish": finish_count,
        "vehicles": n_veh,
    }
    wav_path = out_path or Path(".") / "crs_bed.wav"
    rms, peak, limiter_scale = mix.write(wav_path)
    stats.update({"wav_path": str(wav_path), "rms": rms, "peak": peak,
                  "limit_scale": limiter_scale, "sample_rate": SAMPLE_RATE,
                  "frames": n})
    return stats


def build_duck_key(events: List[Dict[str, Any]], duration: float,
                   out_path: Optional[Path] = None) -> Dict[str, Any]:
    """Mono sidechain key WAV for music ducking (ffmpeg polarity).

    ffmpeg ``sidechaincompress`` ducks the main input while the key is loud,
    so this writes ``1 - envelope``: near zero at idle, ~0.37..0.44 while an
    event window is active. Triggers mirror the mix: launch, land>11, crash>6,
    fall. Overlapping windows keep the deepest duck; attack ~0.12 s.
    """
    n = int(round(duration * SAMPLE_RATE))
    env = [1.0] * n
    attack = SAMPLE_RATE // 12  # ~83 ms attack ramp
    for ev in events:
        etype = str(ev.get("type", ""))
        spec = DUCK_EVENTS.get(etype)
        if not spec:
            continue
        depth_db, hold, release, thresh = spec
        t = float(ev.get("t", 0.0))
        if t >= duration:
            continue
        val = float(ev.get("impact_speed", ev.get("severity", 0.0)))
        if etype in ("land", "crash") and val <= thresh:
            continue
        target = 10.0 ** (depth_db / 20.0)
        i0 = max(0, int(t * SAMPLE_RATE))
        i_hold = min(n, int((t + hold) * SAMPLE_RATE))
        i_rel = min(n, int((t + hold + release) * SAMPLE_RATE))
        for i in range(i0, i_hold):
            k = min(1.0, (i - i0) / attack)
            env[i] = min(env[i], 1.0 - k * (1.0 - target))
        for i in range(i_hold, i_rel):
            span = max(1, i_rel - i_hold)
            k = min(1.0, (i - i_hold) / span)
            env[i] = min(env[i], target + k * (1.0 - target))
    path = out_path or Path(".") / "crs_duck_key.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for v in env:
            w.writeframesraw(struct.pack(
                "<h", int(max(-32768, min(32767, round((1.0 - v) * 32767.0))))))
    return {"duck_key_path": str(path), "frames": n, "duration": duration}
