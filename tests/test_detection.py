from __future__ import annotations

from putz.detection import (
    BOUNDARY_PAUSE_BONUS_SEC,
    CONTEXT_ALWAYS,
    CONTEXT_ISOLATED,
    CONTEXT_ISOLATED_STRICT,
    REASON_CONTEXT_NOT_ISOLATED,
    REASON_LOW_CONFIDENCE_GLUED,
    WORD_CLASS_FILLER,
    WORD_CLASS_LEXICAL,
    collapse_repetitions,
    compile_term_specs,
    context_block_reason,
    detect_match,
    fold_accents,
    glued_low_confidence_reason,
    has_boundary_punctuation,
    has_repeated_run,
    min_probability_for_class,
    pause_bonus_from_punctuation,
)
from transcriber import WordToken


def _token(text: str, normalized: str | None = None) -> WordToken:
    return WordToken(
        text=text,
        normalized=normalized or text.casefold(),
        start=1.0,
        end=1.2,
        probability=0.9,
        segment_id=0,
        segment_avg_logprob=-0.2,
        segment_no_speech_prob=0.1,
    )


def test_fold_and_collapse_helpers() -> None:
    assert fold_accents("nééé") == "neee"
    assert collapse_repetitions("neee") == "ne"
    assert has_repeated_run("neee")
    assert not has_repeated_run("nee")


def test_compile_term_specs_classifies_terms() -> None:
    specs = {s.normalized: s for s in compile_term_specs(["né", "tipo", "é", "tipo assim"])}

    assert specs["né"].word_class == WORD_CLASS_FILLER
    assert specs["né"].context_mode == CONTEXT_ALWAYS
    assert specs["tipo"].word_class == WORD_CLASS_LEXICAL
    assert specs["tipo"].context_mode == CONTEXT_ISOLATED
    assert specs["é"].word_class == WORD_CLASS_FILLER
    assert specs["é"].context_mode == CONTEXT_ISOLATED_STRICT
    # Frase contendo palavra lexical exige isolamento.
    assert specs["tipo assim"].context_mode == CONTEXT_ISOLATED


def test_detect_match_nasal_filler_is_not_blocked() -> None:
    specs = compile_term_specs(["ã"])
    match = detect_match(_token("ã"), specs)
    assert match is not None
    assert match.context_mode == CONTEXT_ALWAYS


def test_detect_match_plain_vowel_requires_strict_isolation() -> None:
    specs = compile_term_specs(["é"])
    match = detect_match(_token("é"), specs)
    assert match is not None
    assert match.context_mode == CONTEXT_ISOLATED_STRICT


def test_detect_match_elongated_vowel_is_free_context() -> None:
    specs = compile_term_specs(["é"])
    match = detect_match(_token("ééé"), specs)
    assert match is not None
    assert match.context_mode == CONTEXT_ALWAYS
    assert match.word_class == WORD_CLASS_FILLER


def test_detect_match_elongation_covers_variants_without_enumeration() -> None:
    specs = compile_term_specs(["né"])
    for variant in ("nééé", "neee", "neeeee", "nêêê"):
        assert detect_match(_token(variant), specs) is not None, variant


def test_detect_match_never_matches_inside_larger_word() -> None:
    specs = compile_term_specs(["né", "e"])
    assert detect_match(_token("nervoso"), specs) is None
    # "neeem" colapsa para "nem", que não é a base "ne".
    assert detect_match(_token("neeem"), specs) is None


def test_min_probability_for_class_derives_lexical_threshold() -> None:
    assert min_probability_for_class(WORD_CLASS_FILLER, 0.2) == 0.2
    assert min_probability_for_class(WORD_CLASS_LEXICAL, 0.2) == 0.55
    assert min_probability_for_class(WORD_CLASS_LEXICAL, 0.4) == 0.7
    assert min_probability_for_class(WORD_CLASS_LEXICAL, 0.6) == 0.85


def test_context_block_reason_by_mode() -> None:
    assert context_block_reason(CONTEXT_ALWAYS, 0.0, 0.0) is None
    assert context_block_reason(CONTEXT_ISOLATED, 0.2, 0.2) is None
    assert (
        context_block_reason(CONTEXT_ISOLATED, 0.2, 0.1)
        == REASON_CONTEXT_NOT_ISOLATED
    )
    assert context_block_reason(CONTEXT_ISOLATED_STRICT, 0.3, 0.3) is None
    assert (
        context_block_reason(CONTEXT_ISOLATED_STRICT, 0.2, 0.3)
        == REASON_CONTEXT_NOT_ISOLATED
    )


def test_glued_low_confidence_reason() -> None:
    assert glued_low_confidence_reason(0.25, 0.01, 0.02) == REASON_LOW_CONFIDENCE_GLUED
    assert glued_low_confidence_reason(0.25, 0.2, 0.01) is None
    assert glued_low_confidence_reason(0.25, 0.01, 0.02, punctuated_boundary=True) is None
    assert glued_low_confidence_reason(0.5, 0.01, 0.01) is None
    assert glued_low_confidence_reason(None, 0.01, 0.01) is None


def test_boundary_punctuation_helpers() -> None:
    assert has_boundary_punctuation("né,")
    assert not has_boundary_punctuation("né")
    assert pause_bonus_from_punctuation("né?") == BOUNDARY_PAUSE_BONUS_SEC
    assert pause_bonus_from_punctuation("tipo") == 0.0
