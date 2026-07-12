from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from putz.audio_analysis import (
    AudioProfile,
    SilenceSpan,
    analyze_wav,
    plan_cut_bounds,
)


def test_plan_cut_bounds_uses_margins_without_profile() -> None:
    start, end = plan_cut_bounds(
        word_start=1.0,
        word_end=1.2,
        margin_before=0.1,
        margin_after=0.1,
        limit_start=0.0,
        limit_end=3.0,
        audio_profile=None,
    )
    assert (start, end) == (0.9, 1.3)


def test_plan_cut_bounds_respects_protected_limits_without_profile() -> None:
    start, end = plan_cut_bounds(
        word_start=1.0,
        word_end=1.2,
        margin_before=0.5,
        margin_after=0.5,
        limit_start=0.9,
        limit_end=1.3,
        audio_profile=None,
    )
    assert (start, end) == (0.9, 1.3)


def test_plan_cut_bounds_extends_into_silence_and_retains_pause() -> None:
    profile = AudioProfile(
        silence_spans=(
            SilenceSpan(0.82, 0.98),
            SilenceSpan(1.21, 1.36),
        ),
        noise_floor_db=-52.0,
    )

    start, end = plan_cut_bounds(
        word_start=1.0,
        word_end=1.2,
        margin_before=0.2,
        margin_after=0.2,
        limit_start=0.0,
        limit_end=3.0,
        audio_profile=profile,
    )

    # Lado inicial: silencio (0.82-0.98) -> corta em 0.82 + 0.05 retido.
    assert start == pytest.approx(0.87)
    # Lado final: silencio (1.21-1.36) -> corta em 1.36 - min(0.10, 0.075).
    assert end == pytest.approx(1.285)


def test_plan_cut_bounds_extension_never_crosses_protected_limit() -> None:
    profile = AudioProfile(
        silence_spans=(SilenceSpan(0.20, 0.98),),
        noise_floor_db=-52.0,
    )

    start, _end = plan_cut_bounds(
        word_start=1.0,
        word_end=1.2,
        margin_before=0.05,
        margin_after=0.05,
        limit_start=0.9,
        limit_end=3.0,
        audio_profile=profile,
    )

    # O silencio comeca em 0.20, mas a protegida termina em 0.9.
    assert start == pytest.approx(0.9)


def test_plan_cut_bounds_extension_is_capped() -> None:
    profile = AudioProfile(
        silence_spans=(SilenceSpan(0.0, 0.98),),
        noise_floor_db=-52.0,
    )

    start, _end = plan_cut_bounds(
        word_start=1.0,
        word_end=1.2,
        margin_before=0.05,
        margin_after=0.05,
        limit_start=0.0,
        limit_end=3.0,
        audio_profile=profile,
    )

    # Nunca estende mais que 0.35s alem do nucleo, mesmo com silencio longo.
    assert start == pytest.approx(0.65)


def test_plan_cut_bounds_snaps_to_energy_minimum_without_adjacent_silence() -> None:
    # Envelope com vale de energia em t=0.93; silencio existe mas longe.
    hop = 0.01
    rms = [-30.0] * 300
    for i in range(91, 96):
        rms[i] = -55.0  # vale em 0.91-0.95
    profile = AudioProfile(
        silence_spans=(SilenceSpan(2.5, 2.8),),
        noise_floor_db=-60.0,
        hop_sec=hop,
        rms_db=tuple(rms),
    )

    start, _end = plan_cut_bounds(
        word_start=1.0,
        word_end=1.2,
        margin_before=0.08,
        margin_after=0.08,
        limit_start=0.0,
        limit_end=3.0,
        audio_profile=profile,
    )

    # Base seria 0.92; o vale mais proximo dentro de +/-0.03 e escolhido.
    assert 0.91 <= start <= 0.95
    assert rms[int(round(start / hop))] == -55.0


def _write_wav(path: Path, pieces: list[tuple[float, float]], rate: int = 16000) -> None:
    """Gera WAV mono s16le: lista de (duracao_s, amplitude 0..1) com tom 300 Hz."""

    samples: list[int] = []
    for duration, amplitude in pieces:
        count = int(duration * rate)
        for i in range(count):
            value = amplitude * math.sin(2.0 * math.pi * 300.0 * (i / rate))
            samples.append(int(value * 32767))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def test_analyze_wav_detects_silence_between_tones(tmp_path: Path) -> None:
    wav = tmp_path / "synth.wav"
    _write_wav(wav, [(0.5, 0.5), (0.3, 0.0), (0.5, 0.5)])

    profile = analyze_wav(wav)

    assert profile.duration == pytest.approx(1.3, abs=0.01)
    assert profile.rms_db, "envelope RMS deve ser preenchido"
    spans = [s for s in profile.silence_spans if s.end - s.start >= 0.1]
    assert len(spans) == 1
    span = spans[0]
    assert span.start == pytest.approx(0.5, abs=0.04)
    assert span.end == pytest.approx(0.8, abs=0.04)
    assert profile.is_silent_between(0.55, 0.75)
    assert not profile.is_silent_between(0.1, 0.4)


def test_analyze_wav_empty_file_returns_neutral_profile(tmp_path: Path) -> None:
    wav = tmp_path / "empty.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"")

    profile = analyze_wav(wav)

    assert profile.silence_spans == ()
