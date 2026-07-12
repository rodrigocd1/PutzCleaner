"""Heuristicas de deteccao de vicios de fala.

Decide *o que* e um vicio removivel; nunca decide *onde* cortar (isso e do
cutter/audio_analysis). Tres niveis de matching, sempre por token inteiro:

1. exato       — forma normalizada identica ao termo configurado;
2. alongamento — token com sequencia de >=3 caracteres repetidos cujo
                 colapso (sem acentos) coincide com a base do termo;
3. frase       — termos com espaco casam janelas de tokens consecutivos.

Cada match carrega uma classe de palavra (limiar de confianca proprio) e um
modo de contexto (exigencia de pausas ao redor), que o cutter aplica.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

from .transcriber import WordToken, normalize_term

# ---------------------------------------------------------------------------
# Classes de palavra e modos de contexto
# ---------------------------------------------------------------------------

WORD_CLASS_FILLER = "vicio_puro"
WORD_CLASS_LEXICAL = "lexical"

CONTEXT_ALWAYS = "sempre"
CONTEXT_ISOLATED = "isolado"
CONTEXT_ISOLATED_STRICT = "isolado_estrito"

# Palavras que existem como vocabulario legitimo do portugues e por isso so
# podem ser removidas quando isoladas por pausas (uso como muleta).
LEXICAL_TERMS = frozenset(
    {
        "tipo",
        "assim",
        "então",
        "entao",
        "sabe",
        "certo",
        "bom",
        "olha",
        "beleza",
        "enfim",
        "tá",
        "ta",
        "ok",
        "digamos",
    }
)

# Vogais orais "cruas": como token unico sao quase sempre fala legitima
# ("é" copula, "e" conjuncao, "a"/"o" artigos). So podem ser removidas com
# isolamento estrito; a forma alongada ("ééé") continua livre, pois o
# alongamento em si ja evidencia hesitacao.
_DANGEROUS_PLAIN = frozenset(
    {"e", "é", "è", "ê", "a", "á", "à", "â", "o", "ó", "ô", "i", "í", "u", "ú"}
)

# Exigencia minima de pausa (em segundos) por modo de contexto.
PAUSE_FOR_ISOLATED_SEC = 0.18
PAUSE_FOR_ISOLATED_STRICT_SEC = 0.25

# Vicios com confianca muito baixa e colados na fala dos dois lados tendem a
# ser pedacos mal reconhecidos de palavras reais — nunca remover.
LOW_CONFIDENCE_GLUED_PROB = 0.35
GLUED_GAP_SEC = 0.06

# Duracao maxima plausivel do nucleo reconhecido, por classe.
MAX_FILLER_DURATION_SEC = 1.50
MAX_LEXICAL_DURATION_SEC = 1.20

# Offset do limiar lexical sobre o limiar base configurado pelo usuario.
_LEXICAL_PROB_OFFSET = 0.30
_LEXICAL_PROB_FLOOR = 0.55
_LEXICAL_PROB_CEIL = 0.85

MAX_PHRASE_GAP_SEC = 0.30

REASON_CONTEXT_NOT_ISOLATED = "contexto_nao_isolado"
REASON_PHRASE_INCOMPLETE = "frase_incompleta"
REASON_LOW_CONFIDENCE_GLUED = "baixa_confianca_sem_pausa"
REASON_DURATION_IMPLAUSIBLE = "duracao_implausivel"


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
    word_class: str
    context_mode: str


@dataclass(frozen=True)
class DetectionMatch:
    configured_term: str
    normalized_term: str
    word_class: str
    context_mode: str


def _classify_term(normalized: str, parts: tuple[str, ...]) -> tuple[str, str]:
    """Retorna (word_class, context_mode) para um termo configurado."""

    if len(parts) > 1:
        # Frases: o bigrama ja e distintivo, mas se contem palavra lexical
        # ("tipo assim") exigimos isolamento para nao cortar uso legitimo
        # ("tipo assim que funciona").
        if any(part in LEXICAL_TERMS for part in parts):
            return WORD_CLASS_FILLER, CONTEXT_ISOLATED
        return WORD_CLASS_FILLER, CONTEXT_ALWAYS
    if normalized in LEXICAL_TERMS:
        return WORD_CLASS_LEXICAL, CONTEXT_ISOLATED
    if normalized in _DANGEROUS_PLAIN:
        return WORD_CLASS_FILLER, CONTEXT_ISOLATED_STRICT
    return WORD_CLASS_FILLER, CONTEXT_ALWAYS


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
        word_class, context_mode = _classify_term(normalized, parts)
        specs.append(
            TermSpec(
                configured=term,
                normalized=normalized,
                folded=folded,
                collapsed=collapse_repetitions(folded),
                parts=parts,
                folded_parts=tuple(fold_accents(part) for part in parts),
                token_count=len(parts),
                is_lexical=word_class == WORD_CLASS_LEXICAL,
                word_class=word_class,
                context_mode=context_mode,
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
    """Matching de token unico: exato primeiro, depois alongamento."""

    folded_token = fold_accents(token.normalized)
    elongated = has_repeated_run(folded_token)

    for spec in specs:
        if spec.token_count != 1:
            continue
        if token.normalized == spec.normalized:
            return DetectionMatch(
                spec.configured, spec.normalized, spec.word_class, spec.context_mode
            )

        # Alongamento: "nééé"/"neeee" casam a base "ne" sem enumerar variantes.
        # O proprio alongamento evidencia hesitacao, entao o contexto e livre
        # mesmo para bases perigosas ("ééé" nunca e a copula "é").
        if (
            not spec.is_lexical
            and elongated
            and collapse_repetitions(folded_token) == spec.collapsed
        ):
            return DetectionMatch(
                spec.configured, spec.normalized, WORD_CLASS_FILLER, CONTEXT_ALWAYS
            )
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


def min_probability_for_class(word_class: str, base_probability: float) -> float:
    """Limiar efetivo por classe (secao 3.3 do plano v2).

    Vicios puros usam o limiar configurado; palavras lexicais exigem um
    limiar bem mais alto, derivado do configurado para que os presets
    continuem com um unico controle.
    """

    if word_class == WORD_CLASS_LEXICAL:
        derived = max(base_probability + _LEXICAL_PROB_OFFSET, _LEXICAL_PROB_FLOOR)
        return min(_LEXICAL_PROB_CEIL, derived)
    return base_probability


def max_duration_for_class(word_class: str) -> float:
    if word_class == WORD_CLASS_LEXICAL:
        return MAX_LEXICAL_DURATION_SEC
    return MAX_FILLER_DURATION_SEC


def context_block_reason(
    context_mode: str,
    pause_before: float,
    pause_after: float,
) -> str | None:
    """Aplica a exigencia de isolamento do modo de contexto.

    ``pause_before``/``pause_after`` sao as pausas efetivas ao redor do alvo
    (confirmadas por silencio quando ha perfil de audio; senao, o gap entre
    timestamps). Retorna None quando a remocao e permitida.
    """

    if context_mode == CONTEXT_ALWAYS:
        return None
    required = (
        PAUSE_FOR_ISOLATED_STRICT_SEC
        if context_mode == CONTEXT_ISOLATED_STRICT
        else PAUSE_FOR_ISOLATED_SEC
    )
    if pause_before < required or pause_after < required:
        return REASON_CONTEXT_NOT_ISOLATED
    return None


def glued_low_confidence_reason(
    probability: float | None,
    gap_before: float,
    gap_after: float,
) -> str | None:
    """Rejeita matches de baixa confianca totalmente colados na fala.

    Um "né" com probabilidade 0,25 sem nenhuma pausa ao redor e, com alta
    frequencia, um pedaco mal segmentado de uma palavra real — remover
    truncaria fala legitima.
    """

    if probability is None:
        return None
    if (
        probability < LOW_CONFIDENCE_GLUED_PROB
        and gap_before < GLUED_GAP_SEC
        and gap_after < GLUED_GAP_SEC
    ):
        return REASON_LOW_CONFIDENCE_GLUED
    return None


def lexical_context_reason(
    index: int,
    valid_tokens: Sequence[object],
    is_lexical: bool,
) -> str | None:
    """Compatibilidade com a API antiga (gaps por timestamp, sem silencio)."""

    if not is_lexical:
        return None

    current = valid_tokens[index]
    prev_gap = 999.0
    next_gap = 999.0
    if index > 0:
        prev_gap = current.start - valid_tokens[index - 1].end
    if index + 1 < len(valid_tokens):
        next_gap = valid_tokens[index + 1].start - current.end
    return context_block_reason(CONTEXT_ISOLATED, prev_gap, next_gap)
