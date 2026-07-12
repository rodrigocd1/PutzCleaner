from __future__ import annotations

import pytest

from transcriber import (
    TermValidationError,
    WordToken,
    normalize_token,
    validate_terms,
)


def test_normalize_token_preserves_accents_and_repetitions() -> None:
    assert normalize_token("  “Nééé?!”  ") == "nééé"


def test_normalize_token_removes_only_edge_punctuation() -> None:
    assert normalize_token("...hum...") == "hum"


def test_validate_terms_deduplicates_and_normalizes() -> None:
    terms = validate_terms([" Né ", "hum", "", "HUM", "né"])
    assert terms == ("né", "hum")


def test_validate_terms_accepts_short_phrase() -> None:
    assert validate_terms(["tipo assim"]) == ("tipo assim",)


def test_validate_terms_rejects_empty_input() -> None:
    with pytest.raises(TermValidationError):
        validate_terms(["", "   "])
