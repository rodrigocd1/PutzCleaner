from __future__ import annotations

from putz.removal_check import _scan_detected_terms
from transcriber import WordToken


def _word(text: str, normalized: str, start: float, end: float) -> WordToken:
    return WordToken(
        text=text,
        normalized=normalized,
        start=start,
        end=end,
        probability=0.9,
        segment_id=0,
        segment_avg_logprob=-0.1,
        segment_no_speech_prob=0.1,
    )


def test_scan_detected_terms_finds_single_and_phrase_terms() -> None:
    words = [
        _word("tipo", "tipo", 0.1, 0.2),
        _word("assim", "assim", 0.21, 0.35),
        _word("né,", "né", 0.5, 0.6),
    ]

    detected = _scan_detected_terms(words, ("tipo assim", "né"))

    assert detected == ("tipo assim", "né")
