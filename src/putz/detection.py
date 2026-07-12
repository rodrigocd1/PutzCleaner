"""Heuristicas de deteccao de vicios de fala."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

from .transcriber import WordToken, normalize_term

LEXICAL_TERMS = frozenset({"tipo", "assim"})
_PAUSE_FOR_LEXICAL_SEC = 0.18
_REPEATED_VOCAL_FILLERS = frozenset({"e", "a", "n", "ne"})
MAX_PHRASE_GAP_SEC = 0.30

REASON_CONTEXT_NOT_ISOLATED = "contexto_nao_isolado"
REASON_PHRASE_INCOMPLETE = "frase_incompleta"


@dataclass(frozen=True)
class TermSpec:
    configured: str
    normalized: str
    folded: str
    collapsed: str
    parts: tuple[str, ...]
    folded_parts: tuple[str, ...]
    token_count: int
    is_lexical: bool


@dataclass(frozen=True)
class DetectionMatch:
    configured_term: str
    normalized_term: str


def compile_term_specs(configured_terms: Iterable[str]) -> tuple[TermSpec, ...]:
    specs: list[TermSpec] = []
    seen: set[str] = set()
    for term in configured_terms:
        normalized = normalize_term(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        folded = fold_accents(normalized)
        parts = tuple(normalized.split())
        specs.append(
            TermSpec(
                configured=term,
                normalized=normalized,
                folded=folded,
                collapsed=collapse_repetitions(folded),
                parts=parts,
                folded_parts=tuple(fold_accents(part) for part in parts),
                token_count=len(parts),
                is_lexical=normalized in LEXICAL_TERMS,
            )
        )
    return tuple(sorted(specs, key=lambda spec: spec.token_count, reverse=True))


def fold_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def collapse_repetitions(value: str) -> str:
    if not value:
        return value
    pieces = [value[0]]
    for ch in value[1:]:
        if ch != pieces[-1]:
            pieces.append(ch)
    return "".join(pieces)


def has_repeated_run(value: str, minimum: int = 3) -> bool:
    if not value:
        return False
    run = 1
    previous = value[0]
    for ch in value[1:]:
        if ch == previous:
            run += 1
            if run >= minimum:
                return True
        else:
            run = 1
            previous = ch
    return False


def detect_match(token: WordToken, specs: Sequence[TermSpec]) -> DetectionMatch | None:
    for spec in specs:
        if spec.token_count != 1:
            continue
        if token.normalized == spec.normalized:
            if spec.collapsed in _REPEATED_VOCAL_FILLERS and len(spec.normalized) == 1:
                if not has_repeated_run(spec.folded) and not has_repeated_run(
                    fold_accents(token.normalized)
                ):
                    continue
            return DetectionMatch(spec.configured, spec.normalized)

        folded_token = fold_accents(token.normalized)
        if (
            not spec.is_lexical
            and has_repeated_run(folded_token)
            and collapse_repetitions(folded_token) == spec.collapsed
        ):
            return DetectionMatch(spec.configured, spec.normalized)
    return None


def detect_phrase_match(
    valid_tokens: Sequence[object],
    start_index: int,
    spec: TermSpec,
) -> tuple[int, ...] | None:
    if spec.token_count <= 1:
        return None
    end_index = start_index + spec.token_count
    if end_index > len(valid_tokens):
        return None

    window = valid_tokens[start_index:end_index]
    base_segment = window[0].token.segment_id
    for offset, (vt, expected) in enumerate(zip(window, spec.parts)):
        if vt.token.normalized != expected:
            return None
        if vt.token.segment_id != base_segment:
            return None
        if offset > 0 and vt.start - window[offset - 1].end > MAX_PHRASE_GAP_SEC:
            return None
    return tuple(vt.index for vt in window)


def lexical_context_reason(
    index: int,
    valid_tokens: Sequence[object],
    is_lexical: bool,
) -> str | None:
    if not is_lexical:
        return None

    current = valid_tokens[index]
    prev_gap = 999.0
    next_gap = 999.0
    if index > 0:
        prev_gap = current.start - valid_tokens[index - 1].end
    if index + 1 < len(valid_tokens):
        next_gap = valid_tokens[index + 1].start - current.end
    if prev_gap < _PAUSE_FOR_LEXICAL_SEC or next_gap < _PAUSE_FOR_LEXICAL_SEC:
        return REASON_CONTEXT_NOT_ISOLATED
    return None
