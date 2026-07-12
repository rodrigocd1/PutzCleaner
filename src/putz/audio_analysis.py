"""Analise simples de silencio no WAV canonico."""

from __future__ import annotations

import audioop
import math
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SilenceSpan:
    start: float
    end: float


@dataclass(frozen=True)
class AudioProfile:
    silence_spans: tuple[SilenceSpan, ...]
    noise_floor_db: float


def analyze_wav(path: Path) -> AudioProfile:
    with wave.open(str(path), "rb") as handle:
        sample_rate = handle.getframerate()
        sample_width = handle.getsampwidth()
        channels = handle.getnchannels()
        frames = handle.readframes(handle.getnframes())

    if sample_rate <= 0 or sample_width <= 0 or channels <= 0:
        return AudioProfile(silence_spans=(), noise_floor_db=-90.0)

    frame_size = sample_width * channels
    window_frames = max(1, int(sample_rate * 0.025))
    hop_frames = max(1, int(sample_rate * 0.01))
    window_bytes = window_frames * frame_size
    hop_bytes = hop_frames * frame_size

    db_values: list[float] = []
    times: list[float] = []
    for offset in range(0, max(len(frames) - window_bytes + 1, 1), hop_bytes):
        chunk = frames[offset : offset + window_bytes]
        if not chunk:
            continue
        rms = audioop.rms(chunk, sample_width)
        normalized = max(rms / float(2 ** (8 * sample_width - 1)), 1e-6)
        db_values.append(20.0 * math.log10(normalized))
        times.append(offset / frame_size / sample_rate)

    if not db_values:
        return AudioProfile(silence_spans=(), noise_floor_db=-90.0)

    sorted_values = sorted(db_values)
    floor = sorted_values[max(0, int(len(sorted_values) * 0.1) - 1)]
    silence_threshold = floor + 8.0

    silence_spans: list[SilenceSpan] = []
    current_start: float | None = None
    previous_time = 0.0
    for time_point, db_value in zip(times, db_values):
        if db_value <= silence_threshold:
            if current_start is None:
                current_start = time_point
            previous_time = time_point + 0.025
            continue
        if current_start is not None and previous_time - current_start >= 0.06:
            silence_spans.append(SilenceSpan(current_start, previous_time))
        current_start = None
    if current_start is not None and previous_time - current_start >= 0.06:
        silence_spans.append(SilenceSpan(current_start, previous_time))

    return AudioProfile(silence_spans=tuple(silence_spans), noise_floor_db=floor)


def refine_cut_bounds(
    *,
    candidate_start: float,
    candidate_end: float,
    word_start: float,
    word_end: float,
    audio_profile: AudioProfile | None,
) -> tuple[float, float]:
    if audio_profile is None or not audio_profile.silence_spans:
        return candidate_start, candidate_end

    start = _snap_start(candidate_start, word_start, audio_profile.silence_spans)
    end = _snap_end(word_end, candidate_end, audio_profile.silence_spans)
    return start, end


def _snap_start(
    candidate_start: float,
    word_start: float,
    spans: tuple[SilenceSpan, ...],
) -> float:
    for span in spans:
        if span.end < candidate_start or span.start > word_start:
            continue
        silence_start = max(candidate_start, span.start)
        silence_end = min(word_start, span.end)
        if silence_end - silence_start < 0.06:
            continue
        retained = min(0.04, (silence_end - silence_start) / 2.0)
        return min(word_start, silence_start + retained)
    return candidate_start


def _snap_end(
    word_end: float,
    candidate_end: float,
    spans: tuple[SilenceSpan, ...],
) -> float:
    for span in spans:
        if span.end < word_end or span.start > candidate_end:
            continue
        silence_start = max(word_end, span.start)
        silence_end = min(candidate_end, span.end)
        if silence_end - silence_start < 0.06:
            continue
        retained = min(0.06, (silence_end - silence_start) / 2.0)
        return max(word_end, silence_end - retained)
    return candidate_end
