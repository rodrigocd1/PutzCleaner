from __future__ import annotations

from cutter import CutOccurrence
from transcript import build_transcript
from transcriber import WordToken


def _word(text: str, normalized: str, start: float, end: float, segment_id: int) -> WordToken:
    return WordToken(
        text=text,
        normalized=normalized,
        start=start,
        end=end,
        probability=0.9,
        segment_id=segment_id,
        segment_avg_logprob=-0.1,
        segment_no_speech_prob=0.1,
    )


def test_build_transcript_marks_removed_word() -> None:
    words = [
        _word("Olá", "olá", 0.0, 0.3, 0),
        _word("né", "né", 0.4, 0.5, 0),
        _word("tudo", "tudo", 0.6, 0.9, 0),
    ]
    occurrences = [
        CutOccurrence("né", "né", 0.4, 0.5, 0.8, 0.35, 0.58),
    ]

    transcript = build_transcript(
        words,
        occurrences,
        input_name="entrada.mp4",
        model_label="small (small)",
        device_label="cpu",
    )

    assert "né [removida 00:00:00.400]" in transcript
    assert "Total de palavras removidas: 1" in transcript
