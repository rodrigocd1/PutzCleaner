"""Analise de energia/silencio do WAV canonico e refinamento de bordas.

O WAV canonico (mono, 16 kHz, s16le) ja esta alinhado a timeline do video,
entao qualquer tempo calculado aqui pode ser usado direto no plano de cortes.

Duas responsabilidades:

1. ``analyze_wav``: envelope RMS (janela 25 ms, hop 10 ms), piso de ruido
   (percentil 10) e trechos de silencio (piso + 8 dB por >= 60 ms).
2. ``plan_cut_bounds``: decide as bordas finais de um corte usando o silencio
   real ao redor da palavra — estende o corte para dentro da pausa retendo
   uma micro-pausa natural na juncao, ou, sem silencio, encosta a borda no
   minimo local de energia para nao cortar fonemas.
"""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass, field
from pathlib import Path

# Parametros do envelope.
_WINDOW_SEC = 0.025
_HOP_SEC = 0.010
_SILENCE_MARGIN_DB = 8.0
_MIN_SILENCE_SEC = 0.06

# Refinamento de bordas (secao 2 do plano v2).
SILENCE_EXTEND_MAX_SEC = 0.35
SILENCE_ADJACENCY_SEC = 0.08
RETAIN_SILENCE_BEFORE_SEC = 0.05
RETAIN_SILENCE_AFTER_SEC = 0.10
ENERGY_SNAP_RADIUS_SEC = 0.03


@dataclass(frozen=True)
class SilenceSpan:
    start: float
    end: float


@dataclass(frozen=True)
class AudioProfile:
    silence_spans: tuple[SilenceSpan, ...]
    noise_floor_db: float
    hop_sec: float = _HOP_SEC
    rms_db: tuple[float, ...] = field(default=(), repr=False)
    duration: float = 0.0
    silence_threshold_db: float = -60.0

    def silence_overlap(self, start: float, end: float) -> float:
        """Total de silencio (s) dentro de [start, end]."""

        if end <= start:
            return 0.0
        total = 0.0
        for span in self.silence_spans:
            lo = max(start, span.start)
            hi = min(end, span.end)
            if hi > lo:
                total += hi - lo
        return total

    def is_silent_between(self, start: float, end: float, coverage: float = 0.8) -> bool:
        if end <= start:
            return True
        return self.silence_overlap(start, end) >= (end - start) * coverage

    def silence_ending_near(self, instant: float) -> SilenceSpan | None:
        """Silencio que termina ate ``SILENCE_ADJACENCY_SEC`` antes de ``instant``."""

        best: SilenceSpan | None = None
        for span in self.silence_spans:
            if span.start >= instant:
                break
            if span.end >= instant - SILENCE_ADJACENCY_SEC:
                if best is None or span.end > best.end:
                    best = span
        return best

    def silence_starting_near(self, instant: float) -> SilenceSpan | None:
        """Silencio que comeca ate ``SILENCE_ADJACENCY_SEC`` depois de ``instant``."""

        for span in self.silence_spans:
            if span.end <= instant:
                continue
            if span.start <= instant + SILENCE_ADJACENCY_SEC:
                return span
            break
        return None

    def local_minimum_time(self, lo: float, hi: float, prefer: float) -> float:
        """Tempo do minimo de energia em [lo, hi]; ``prefer`` desempata."""

        if not self.rms_db or hi <= lo:
            return prefer
        i0 = max(0, int(lo / self.hop_sec))
        i1 = min(len(self.rms_db) - 1, int(hi / self.hop_sec))
        if i1 < i0:
            return prefer
        best_i = min(
            range(i0, i1 + 1),
            key=lambda i: (self.rms_db[i], abs(i * self.hop_sec - prefer)),
        )
        return min(max(best_i * self.hop_sec, lo), hi)


def analyze_wav(path: Path) -> AudioProfile:
    with wave.open(str(path), "rb") as handle:
        sample_rate = handle.getframerate()
        sample_width = handle.getsampwidth()
        channels = handle.getnchannels()
        n_frames = handle.getnframes()
        frames = handle.readframes(n_frames)

    if sample_rate <= 0 or sample_width <= 0 or channels <= 0 or not frames:
        return AudioProfile(silence_spans=(), noise_floor_db=-90.0)

    duration = n_frames / float(sample_rate)
    window_frames = max(1, int(sample_rate * _WINDOW_SEC))
    hop_frames = max(1, int(sample_rate * _HOP_SEC))
    hop_sec = hop_frames / float(sample_rate)
    full_scale = float(2 ** (8 * sample_width - 1))

    db_values = _rms_envelope_db(
        frames, sample_width, channels, window_frames, hop_frames, full_scale
    )
    if not db_values:
        return AudioProfile(silence_spans=(), noise_floor_db=-90.0, duration=duration)

    sorted_values = sorted(db_values)
    floor = sorted_values[max(0, int(len(sorted_values) * 0.1) - 1)]
    silence_threshold = floor + _SILENCE_MARGIN_DB

    silence_spans = _extract_silence_spans(db_values, hop_sec, silence_threshold)

    return AudioProfile(
        silence_spans=tuple(silence_spans),
        noise_floor_db=floor,
        hop_sec=hop_sec,
        rms_db=tuple(db_values),
        duration=duration,
        silence_threshold_db=silence_threshold,
    )


def _rms_envelope_db(
    frames: bytes,
    sample_width: int,
    channels: int,
    window_frames: int,
    hop_frames: int,
    full_scale: float,
) -> list[float]:
    """Envelope RMS em dBFS. numpy quando disponivel; audioop como reserva."""

    if sample_width == 2:
        try:
            return _rms_envelope_numpy(
                frames, channels, window_frames, hop_frames, full_scale
            )
        except ImportError:
            pass
    return _rms_envelope_audioop(
        frames, sample_width, channels, window_frames, hop_frames, full_scale
    )


def _rms_envelope_numpy(
    frames: bytes,
    channels: int,
    window_frames: int,
    hop_frames: int,
    full_scale: float,
) -> list[float]:
    import numpy as np

    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
    if channels > 1:
        samples = samples[: (len(samples) // channels) * channels]
        samples = samples.reshape(-1, channels).mean(axis=1)
    if len(samples) < window_frames:
        window_frames = max(1, len(samples))

    squared = np.concatenate(([0.0], np.cumsum(samples * samples)))
    starts = np.arange(0, max(len(samples) - window_frames + 1, 1), hop_frames)
    sums = squared[starts + min(window_frames, len(samples))] - squared[starts]
    rms = np.sqrt(sums / float(window_frames))
    normalized = np.maximum(rms / full_scale, 1e-6)
    return (20.0 * np.log10(normalized)).tolist()


def _rms_envelope_audioop(
    frames: bytes,
    sample_width: int,
    channels: int,
    window_frames: int,
    hop_frames: int,
    full_scale: float,
) -> list[float]:
    import audioop

    frame_size = sample_width * channels
    window_bytes = window_frames * frame_size
    hop_bytes = hop_frames * frame_size

    db_values: list[float] = []
    for offset in range(0, max(len(frames) - window_bytes + 1, 1), hop_bytes):
        chunk = frames[offset : offset + window_bytes]
        if not chunk:
            continue
        rms = audioop.rms(chunk, sample_width)
        normalized = max(rms / full_scale, 1e-6)
        db_values.append(20.0 * math.log10(normalized))
    return db_values


def _extract_silence_spans(
    db_values: list[float],
    hop_sec: float,
    silence_threshold: float,
) -> list[SilenceSpan]:
    spans: list[SilenceSpan] = []
    run_start: int | None = None
    for i, db_value in enumerate(db_values):
        if db_value <= silence_threshold:
            if run_start is None:
                run_start = i
            continue
        if run_start is not None:
            _append_span(spans, run_start, i - 1, hop_sec)
            run_start = None
    if run_start is not None:
        _append_span(spans, run_start, len(db_values) - 1, hop_sec)
    return spans


def _append_span(
    spans: list[SilenceSpan], first_index: int, last_index: int, hop_sec: float
) -> None:
    start = first_index * hop_sec
    end = last_index * hop_sec + _WINDOW_SEC
    if end - start >= _MIN_SILENCE_SEC:
        spans.append(SilenceSpan(start, end))


# ---------------------------------------------------------------------------
# Refinamento de bordas do corte
# ---------------------------------------------------------------------------


def plan_cut_bounds(
    *,
    word_start: float,
    word_end: float,
    margin_before: float,
    margin_after: float,
    limit_start: float,
    limit_end: float,
    audio_profile: AudioProfile | None,
) -> tuple[float, float]:
    """Decide as bordas do corte para um alvo aprovado.

    - Com silencio adjacente: estende o corte para dentro da pausa (ate
      ``SILENCE_EXTEND_MAX_SEC`` alem do nucleo, ou a margem se maior),
      retendo uma micro-pausa natural na juncao.
    - Sem silencio: usa a margem configurada e encosta a borda no minimo
      local de energia, evitando cortar em cima de fonema.
    - Nunca ultrapassa ``limit_start``/``limit_end`` (palavras protegidas)
      nem invade o nucleo do alvo.
    """

    base_start = min(max(limit_start, word_start - margin_before, 0.0), word_start)
    base_end = max(min(limit_end, word_end + margin_after), word_end)

    if audio_profile is None or not audio_profile.silence_spans:
        return base_start, base_end

    start = _plan_start_bound(
        audio_profile, word_start, margin_before, limit_start, base_start
    )
    end = _plan_end_bound(
        audio_profile, word_end, margin_after, limit_end, base_end
    )
    return start, end


def _plan_start_bound(
    profile: AudioProfile,
    word_start: float,
    margin_before: float,
    limit_start: float,
    base_start: float,
) -> float:
    span = profile.silence_ending_near(word_start)
    if span is not None:
        span_lo = span.start
        span_hi = min(span.end, word_start)
        usable = max(0.0, span_hi - span_lo)
        retained = min(RETAIN_SILENCE_BEFORE_SEC, usable / 2.0)
        candidate = span_lo + retained
        candidate = max(
            candidate,
            word_start - max(margin_before, SILENCE_EXTEND_MAX_SEC),
            limit_start,
            0.0,
        )
        if candidate <= word_start:
            return candidate
        return base_start

    lo = max(limit_start, base_start - ENERGY_SNAP_RADIUS_SEC, 0.0)
    hi = min(word_start, base_start + ENERGY_SNAP_RADIUS_SEC)
    return profile.local_minimum_time(lo, hi, base_start)


def _plan_end_bound(
    profile: AudioProfile,
    word_end: float,
    margin_after: float,
    limit_end: float,
    base_end: float,
) -> float:
    span = profile.silence_starting_near(word_end)
    if span is not None:
        span_lo = max(span.start, word_end)
        span_hi = span.end
        usable = max(0.0, span_hi - span_lo)
        retained = min(RETAIN_SILENCE_AFTER_SEC, usable / 2.0)
        candidate = span_hi - retained
        candidate = min(
            candidate,
            word_end + max(margin_after, SILENCE_EXTEND_MAX_SEC),
            limit_end,
        )
        if candidate >= word_end:
            return candidate
        return base_end

    lo = max(word_end, base_end - ENERGY_SNAP_RADIUS_SEC)
    hi = min(limit_end, base_end + ENERGY_SNAP_RADIUS_SEC)
    return profile.local_minimum_time(lo, hi, base_end)
