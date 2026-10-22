"""Unit tests for the deterministic audio post-mix (app/services/audio_mixer)."""
import struct
import wave
from pathlib import Path

import pytest

from app.services import audio_mixer as am
from app.models.scenario import ScenarioError, validate_document


TT = 48_000.0


def _read_wav(path):
    with wave.open(str(path)) as w:
        ch, rate, n = w.getnchannels(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    vals = [v[0] for v in struct.iter_unpack("<h", raw)]
    return ch, rate, n, vals


def test_empty_events_engine_bed_is_audible(tmp_path):
    stats = am.build_mix([], 8.0, vehicle_count=2,
                         out_path=tmp_path / "bed.wav")
    ch, rate, n, vals = _read_wav(tmp_path / "bed.wav")
    assert ch == 2 and rate == 48_000
    assert n >= 8.0 * 48_000
    peak = max(vals) / 32767.0
    assert 0.01 <= peak <= 1.0, stats


def test_build_mix_is_deterministic(tmp_path):
    events = [
        {"type": "launch", "t": 2.0, "vehicle": 0},
        {"type": "land", "t": 4.0, "vehicle": 0, "impact_speed": 12.0},
        {"type": "crash", "t": 5.0, "vehicle": 1, "severity": 8.0},
        {"type": "finish", "t": 6.0, "vehicle": 0},
    ]
    am.build_mix(events, 8.0, out_path=tmp_path / "a.wav")
    am.build_mix(events, 8.0, out_path=tmp_path / "b.wav")
    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()


def test_duration_matches_request(tmp_path):
    am.build_mix([], 5.0, vehicle_count=1, out_path=tmp_path / "d.wav")
    _, _, n, _ = _read_wav(tmp_path / "d.wav")
    # +0.25 s tail room is allowed, nothing beyond that
    assert 5.0 * TT <= n <= 5.5 * TT


def test_impact_threshold_gating(tmp_path):
    soft = am.build_mix([{"type": "land", "t": 3.0, "vehicle": 0,
                          "impact_speed": 4.0}], 6.0, vehicle_count=1,
                        out_path=tmp_path / "soft.wav")
    hard = am.build_mix([{"type": "land", "t": 3.0, "vehicle": 0,
                          "impact_speed": 18.0}], 6.0, vehicle_count=1,
                        out_path=tmp_path / "hard.wav")
    assert hard["peak"] > soft["peak"]


def test_negative_severity_events_do_not_crash(tmp_path):
    stats = am.build_mix([
        {"type": "crash", "t": 2.0, "vehicle": 0, "severity": 2.0},
        {"type": "land", "t": 2.5, "vehicle": 0, "impact_speed": -1.0},
    ], 4.0, vehicle_count=1, out_path=tmp_path / "n.wav")
    assert stats["peak"] > 0.0  # engines still audible, cues skipped


def test_write_peak_limiter(tmp_path):
    n = int(1.0 * TT)
    mix = am.Mixer(n)
    buf = [0.9] * n
    mix.add_cue(buf, 0, 2.0, 0.0)   # sums to 1.8 -> must be limited
    rms, peak, scale = mix.write(tmp_path / "lim.wav")
    _, _, _, vals = _read_wav(tmp_path / "lim.wav")
    assert scale < 1.0
    assert peak <= 0.94 + 1e-6
    assert max(vals) / 32767.0 <= 0.95


def test_duck_key_dips_and_recovers(tmp_path):
    key = am.build_duck_key(
        [{"type": "launch", "t": 2.0, "vehicle": 0},
         {"type": "land", "t": 5.0, "vehicle": 0, "impact_speed": 4.0}],
        10.0, out_path=tmp_path / "duck.wav")
    _, _, _, vals = _read_wav(tmp_path / "duck.wav")
    # key is ffmpeg-polarity: 0 at idle, ~0.37 (-4 dB) inside the duck window
    at_rest = vals[int(1.0 * TT)] / 32768.0
    in_duck = vals[int(2.2 * TT)] / 32768.0
    after_release = vals[int(3.9 * TT)] / 32768.0
    assert abs(at_rest) < 0.01
    assert in_duck >= 0.30
    assert abs(after_release) < 0.01
    assert key["frames"] == 10.0 * TT


def test_synthesized_fallbacks_are_finite():
    for synth in (am._synth_engine, am._synth_whoosh, am._synth_thud,
                  am._synth_impact, am._synth_finish):
        buf = synth()
        peak = max(abs(v) for v in buf)
        assert peak > 0.05, synth.__name__
        assert peak <= 1.2, synth.__name__


def test_corrupt_wav_falls_back_to_synthesis(tmp_path):
    bad = tmp_path / "not_a_wav.wav"
    bad.write_bytes(b"RIFF0000WAVEBAD")
    rate, mono = am._decode_wav(bad)
    assert mono == []   # never raises
    buf = am._asset("nosuchfile.wav", am._synth_thud)
    assert buf, "synthetic fallback must kick in"


def test_volume_bounds_schema():
    from app.services.scenario_service import build_scenario
    doc = build_scenario("mega_ramp", 7, vehicle_count=1,
                         duration=8.0).to_document()
    doc["output"].update({"engine_volume": 0.4, "sfx_volume": 0.9,
                          "music_volume": 0.2, "music_enabled": True,
                          "sfx_enabled": True})
    validate_document(doc)  # no raise
    doc2 = dict(doc)
    doc2["output"] = dict(doc["output"], engine_volume=1.5)
    with pytest.raises(ScenarioError):
        validate_document(doc2)
    doc3 = dict(doc)
    doc3["output"] = dict(doc["output"], music_enabled="yes")
    with pytest.raises(ScenarioError):
        validate_document(doc3)


def test_render_config_volume_validation():
    from app.services.render_service import ConfigError, validate_config
    base = {"template_id": "crash_test", "duration": 6, "aspect": "9:16",
            "resolution": "720p", "quality": "low", "seed": 1,
            "out_dir": "somewhere"}
    for bad in ({"engine_volume": 1.5}, {"sfx_volume": -0.2},
                {"music_volume": 2.0}):
        cfg = dict(base, **bad)
        with pytest.raises(ConfigError):
            validate_config(cfg)
    ok = dict(base, engine_volume=0.5, sfx_enabled=False,
              music_enabled=True, music_volume=0.0)
    validate_config(ok)
