from __future__ import annotations

from pathlib import Path

from putz.audio_analysis import AudioProfile, SilenceSpan, refine_cut_bounds


def test_refine_cut_bounds_returns_original_values_without_profile() -> None:
    assert refine_cut_bounds(
        candidate_start=0.9,
        candidate_end=1.3,
        word_start=1.0,
        word_end=1.2,
        audio_profile=None,
    ) == (0.9, 1.3)


def test_refine_cut_bounds_snaps_into_detected_silence() -> None:
    profile = AudioProfile(
        silence_spans=(
            SilenceSpan(0.82, 0.98),
            SilenceSpan(1.21, 1.36),
        ),
        noise_floor_db=-52.0,
    )

    start, end = refine_cut_bounds(
        candidate_start=0.8,
        candidate_end=1.4,
        word_start=1.0,
        word_end=1.2,
        audio_profile=profile,
    )

    assert start == 0.86
    assert end == 1.3
