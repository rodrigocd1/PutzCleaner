"""Transcrição local em português com faster-whisper.

Este módulo é puro domínio: não importa ``gui`` nem toca widgets Tkinter.
Ele recebe um WAV canônico (mono/16 kHz alinhado à timeline do vídeo) e
produz ``WordToken`` imutáveis com timestamps por palavra já sanitizados.

Consulte as seções 5, 8, 10, 11.1 e 13 do plano de implementação.
"""

from __future__ import annotations

import gc
import math
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

# ---------------------------------------------------------------------------
# Constantes de ASR (seção 8)
# ---------------------------------------------------------------------------

MODEL_MAP: dict[str, str] = {
    "small": "small",
    "medium": "medium",
    "large": "large-v3",
}

MIN_WORD_PROBABILITY = 0.60
MIN_WORD_DURATION_SEC = 0.02
MAX_WORD_DURATION_SEC = 3.00
TIMESTAMP_TOLERANCE_SEC = 0.02
MAX_SEGMENT_NO_SPEECH = 0.60
MIN_SEGMENT_AVG_LOGPROB = -1.00
MAX_TERMS = 200
MAX_TERM_LENGTH = 50
EPSILON = 1e-6

# Tolerância para deduplicar duplicatas evidentes na fronteira de segmentos.
_DEDUP_TIME_TOLERANCE_SEC = 0.02

# Divergência máxima aceitável entre a duração reportada pelo ASR e a timeline.
_DURATION_MISMATCH_SEC = 0.05


class TranscriptionError(RuntimeError):
    """Erro de domínio da transcrição, com mensagem amigável ao usuário."""


class TranscriptionCancelled(RuntimeError):
    """Sinaliza que o cancelamento foi solicitado durante a transcrição."""


# ---------------------------------------------------------------------------
# Modelo de dados (seção 11.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WordToken:
    text: str
    normalized: str
    start: float | None
    end: float | None
    probability: float | None
    segment_id: int
    segment_avg_logprob: float | None
    segment_no_speech_prob: float | None


@dataclass(frozen=True)
class TranscriptionResult:
    words: tuple[WordToken, ...]
    audio_duration: float
    language: str
    language_probability: float
    model_requested: str
    model_resolved: str


# ---------------------------------------------------------------------------
# Normalização segura de tokens (seção 10)
# ---------------------------------------------------------------------------


def normalize_token(value: str) -> str:
    """Normaliza um token preservando acentos e vogais repetidas.

    Passos: NFC, strip, casefold, remoção de pontuação/símbolos apenas nas
    extremidades (por categoria Unicode), strip final. Nunca remove acentos,
    nunca colapsa repetições, nunca usa substring/regex/fuzzy.
    """

    if not isinstance(value, str):
        raise TypeError("normalize_token espera str")

    text = unicodedata.normalize("NFC", value)
    text = text.strip()
    text = text.casefold()

    # Remove pontuação/símbolos apenas nas extremidades usando categoria Unicode.
    def _is_edge_strippable(ch: str) -> bool:
        category = unicodedata.category(ch)
        # P* = pontuação, S* = símbolos.
        return category.startswith("P") or category.startswith("S")

    start = 0
    end = len(text)
    while start < end and _is_edge_strippable(text[start]):
        start += 1
    while end > start and _is_edge_strippable(text[end - 1]):
        end -= 1
    text = text[start:end]

    return text.strip()


class TermValidationError(ValueError):
    """Erro de validação da lista editável de palavras."""


def validate_terms(raw_lines: Iterable[str]) -> tuple[str, ...]:
    """Valida e normaliza a lista editável de vícios de fala (seção 10).

    - uma entrada por linha;
    - remove linhas vazias;
    - rejeita whitespace interno;
    - rejeita entrada que normalize para vazio;
    - rejeita mais de ``MAX_TERM_LENGTH`` caracteres (na forma original);
    - deduplica pela forma normalizada preservando a ordem;
    - no máximo ``MAX_TERMS`` entradas.
    """

    seen: set[str] = set()
    result: list[str] = []

    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if len(stripped) > MAX_TERM_LENGTH:
            raise TermValidationError(
                f"A entrada '{stripped[:20]}...' excede {MAX_TERM_LENGTH} caracteres."
            )
        # Whitespace interno indica mais de uma palavra por linha.
        if any(ch.isspace() for ch in stripped):
            raise TermValidationError("Use apenas uma palavra ou som por linha.")
        normalized = normalize_token(stripped)
        if not normalized:
            raise TermValidationError(
                f"A entrada '{stripped}' não contém uma palavra válida."
            )
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) > MAX_TERMS:
            raise TermValidationError(
                f"A lista possui mais de {MAX_TERMS} palavras."
            )

    if not result:
        raise TermValidationError("A lista de palavras não pode ficar vazia.")

    return tuple(result)


# ---------------------------------------------------------------------------
# Sanitização numérica
# ---------------------------------------------------------------------------


def _sanitize_float(value: object) -> float | None:
    """Converte um valor para float finito, ou retorna None.

    Rejeita booleanos, conversões inválidas, NaN e infinito.
    """

    if isinstance(value, bool):
        return None
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


# ---------------------------------------------------------------------------
# Transcriber
# ---------------------------------------------------------------------------


class Transcriber:
    """Encapsula um único ``WhisperModel`` carregado sob demanda."""

    def __init__(self, model_directory: Path) -> None:
        self._model_directory = Path(model_directory)
        self._model = None
        self._loaded_resolved_name: str | None = None

    def _ensure_model(
        self,
        requested_model: str,
        log_callback: Callable[[str], None],
    ):
        if requested_model not in MODEL_MAP:
            raise TranscriptionError(
                f"Modelo desconhecido: {requested_model!r}."
            )
        resolved = MODEL_MAP[requested_model]

        if self._model is not None and self._loaded_resolved_name == resolved:
            return self._model

        # Trocar modelo: descartar referência anterior antes de coletar.
        if self._model is not None:
            self._model = None
            self._loaded_resolved_name = None
            gc.collect()

        log_callback(
            f"Carregando o modelo {requested_model}. "
            "No primeiro uso, o download pode demorar."
        )

        # Importação tardia: faster_whisper é pesado e depende de caches de env.
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # noqa: BLE001 - queremos mensagem amigável
            raise TranscriptionError(
                "Não foi possível carregar a biblioteca de transcrição "
                "(faster-whisper). Execute novamente o setup.bat."
            ) from exc

        try:
            model = WhisperModel(
                resolved,
                device="cpu",
                compute_type="int8",
                download_root=str(self._model_directory),
            )
        except MemoryError as exc:
            raise TranscriptionError(
                "Memória insuficiente para carregar este modelo. "
                "Tente o modelo small ou medium."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise TranscriptionError(
                "Falha ao carregar o modelo. Verifique a conexão de internet "
                "para o download inicial e tente novamente."
            ) from exc

        self._model = model
        self._loaded_resolved_name = resolved
        return model

    def transcribe(
        self,
        canonical_audio_path: Path,
        timeline_duration: float,
        requested_model: str,
        cancel_event: threading.Event,
        log_callback: Callable[[str], None],
        progress_callback: Callable[[float], None],
    ) -> TranscriptionResult:
        if cancel_event.is_set():
            raise TranscriptionCancelled()

        resolved = MODEL_MAP.get(requested_model, requested_model)
        model = self._ensure_model(requested_model, log_callback)

        if cancel_event.is_set():
            raise TranscriptionCancelled()

        log_callback("Iniciando transcrição em português.")

        try:
            segments, info = model.transcribe(
                str(canonical_audio_path),
                language="pt",
                task="transcribe",
                beam_size=5,
                temperature=0.0,
                word_timestamps=True,
                vad_filter=True,
                condition_on_previous_text=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise TranscriptionError(
                "Falha ao iniciar a transcrição do áudio."
            ) from exc

        info_duration = _sanitize_float(getattr(info, "duration", None))
        if info_duration is None:
            raise TranscriptionError(
                "A transcrição não reportou uma duração válida do áudio."
            )
        if abs(info_duration - timeline_duration) > _DURATION_MISMATCH_SEC:
            raise TranscriptionError(
                "A duração do áudio transcrito diverge da timeline do vídeo; "
                "o WAV canônico pode estar inválido."
            )

        language = str(getattr(info, "language", "pt") or "pt")
        language_probability = (
            _sanitize_float(getattr(info, "language_probability", None)) or 0.0
        )

        words: list[WordToken] = []
        last_progress = 0.0

        for segment in segments:
            if cancel_event.is_set():
                raise TranscriptionCancelled()

            segment_id = int(getattr(segment, "id", 0) or 0)
            seg_avg_logprob = _sanitize_float(
                getattr(segment, "avg_logprob", None)
            )
            seg_no_speech = _sanitize_float(
                getattr(segment, "no_speech_prob", None)
            )

            segment_words = getattr(segment, "words", None)
            if segment_words is None:
                log_callback(
                    f"Segmento {segment_id} sem timestamps por palavra; ignorado."
                )
            else:
                for word in segment_words:
                    raw_text = str(getattr(word, "word", "") or "")
                    token = WordToken(
                        text=raw_text,
                        normalized=normalize_token(raw_text),
                        start=_sanitize_float(getattr(word, "start", None)),
                        end=_sanitize_float(getattr(word, "end", None)),
                        probability=_sanitize_float(
                            getattr(word, "probability", None)
                        ),
                        segment_id=segment_id,
                        segment_avg_logprob=seg_avg_logprob,
                        segment_no_speech_prob=seg_no_speech,
                    )
                    words.append(token)

            # Progresso monotônico pela razão temporal válida do segmento.
            seg_end = _sanitize_float(getattr(segment, "end", None))
            if seg_end is not None and timeline_duration > 0:
                ratio = max(0.0, min(1.0, seg_end / timeline_duration))
                if ratio > last_progress:
                    last_progress = ratio
                    progress_callback(ratio)

        deduped = _dedupe_boundary_words(words)

        log_callback(
            f"Transcrição concluída: {len(deduped)} palavras reconhecidas."
        )

        return TranscriptionResult(
            words=tuple(deduped),
            audio_duration=info_duration,
            language=language,
            language_probability=language_probability,
            model_requested=requested_model,
            model_resolved=resolved,
        )


def _dedupe_boundary_words(words: list[WordToken]) -> list[WordToken]:
    """Remove duplicatas evidentes na fronteira de segmentos.

    Critério: mesma forma normalizada e timestamps inicial/final dentro de
    ``_DEDUP_TIME_TOLERANCE_SEC``; mantém a de maior probabilidade. Não
    deduplica palavras diferentes sobrepostas.
    """

    if len(words) < 2:
        return list(words)

    result: list[WordToken] = []
    for word in words:
        if result:
            prev = result[-1]
            if (
                word.normalized
                and prev.normalized == word.normalized
                and prev.start is not None
                and prev.end is not None
                and word.start is not None
                and word.end is not None
                and abs(prev.start - word.start) <= _DEDUP_TIME_TOLERANCE_SEC
                and abs(prev.end - word.end) <= _DEDUP_TIME_TOLERANCE_SEC
            ):
                prev_prob = prev.probability if prev.probability is not None else -1.0
                cur_prob = word.probability if word.probability is not None else -1.0
                if cur_prob > prev_prob:
                    result[-1] = word
                continue
        result.append(word)

    return result
