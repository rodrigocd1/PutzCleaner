"""Transcrição local em português com faster-whisper.

Este módulo é puro domínio: não importa ``gui`` nem toca widgets Tkinter.
Ele recebe um WAV canônico (mono/16 kHz alinhado à timeline do vídeo) e
produz ``WordToken`` imutáveis com timestamps por palavra já sanitizados.

Consulte as seções 5, 8, 10, 11.1 e 13 do plano de implementação.
"""

from __future__ import annotations

import gc
import math
import os
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

# Dispositivos oferecidos na interface (seção 5: CPU/int8 é o padrão seguro;
# "auto" usa GPU CUDA se disponível e cai para CPU; "cuda" força GPU).
DEVICE_CHOICES: tuple[str, ...] = ("auto", "cpu", "cuda")
_COMPUTE_CPU = "int8"
_COMPUTE_CUDA = "float16"

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
    device_requested: str
    device_used: str
    compute_type: str


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
        # Chave do modelo em cache: (resolved, device, compute_type).
        self._loaded_key: tuple[str, str, str] | None = None
        # CPU: usar todos os núcleos lógicos por padrão.
        self._cpu_threads = os.cpu_count() or 0

    @staticmethod
    def cuda_available() -> bool:
        """Retorna True se houver ao menos uma GPU CUDA utilizável."""

        try:
            import ctranslate2

            return int(ctranslate2.get_cuda_device_count()) > 0
        except Exception:  # noqa: BLE001
            return False

    def _device_attempts(self, requested_device: str) -> list[tuple[str, str]]:
        """Lista ordenada de (device, compute_type) a tentar."""

        req = (requested_device or "auto").lower()
        if req == "cpu":
            return [("cpu", _COMPUTE_CPU)]
        if req in ("cuda", "gpu"):
            return [("cuda", _COMPUTE_CUDA)]
        # auto: GPU se disponível, com fallback para CPU.
        if self.cuda_available():
            return [("cuda", _COMPUTE_CUDA), ("cpu", _COMPUTE_CPU)]
        return [("cpu", _COMPUTE_CPU)]

    def _ensure_model(
        self,
        requested_model: str,
        requested_device: str,
        log_callback: Callable[[str], None],
    ) -> tuple[object, str, str]:
        if requested_model not in MODEL_MAP:
            raise TranscriptionError(
                f"Modelo desconhecido: {requested_model!r}."
            )
        resolved = MODEL_MAP[requested_model]
        attempts = self._device_attempts(requested_device)

        # Reaproveitar o modelo se o já carregado atende a algum destino aceito.
        acceptable = {(resolved, d, c) for d, c in attempts}
        if self._model is not None and self._loaded_key in acceptable:
            _, device, compute = self._loaded_key
            return self._model, device, compute

        # Trocar modelo/dispositivo: descartar referência anterior antes de coletar.
        if self._model is not None:
            self._model = None
            self._loaded_key = None
            gc.collect()

        # Importação tardia: faster_whisper é pesado e depende de caches de env.
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # noqa: BLE001 - queremos mensagem amigável
            raise TranscriptionError(
                "Não foi possível carregar a biblioteca de transcrição "
                "(faster-whisper). Execute novamente o setup.bat."
            ) from exc

        last_error: Exception | None = None
        for device, compute in attempts:
            if device == "cpu":
                log_callback(
                    f"Carregando o modelo {requested_model} em CPU "
                    f"({self._cpu_threads or 'padrão'} núcleos). "
                    "No primeiro uso, o download pode demorar."
                )
            else:
                log_callback(
                    f"Carregando o modelo {requested_model} na GPU (CUDA). "
                    "No primeiro uso, o download pode demorar."
                )
            try:
                kwargs: dict[str, object] = {
                    "device": device,
                    "compute_type": compute,
                    "download_root": str(self._model_directory),
                }
                if device == "cpu" and self._cpu_threads:
                    kwargs["cpu_threads"] = self._cpu_threads
                model = WhisperModel(resolved, **kwargs)
                # Em GPU o carregamento pode ter sucesso mas a inferência falhar
                # se faltarem cuBLAS/cuDNN; um warmup detecta isso a tempo.
                if device == "cuda":
                    _warmup_gpu(model)
            except MemoryError as exc:
                raise TranscriptionError(
                    "Memória insuficiente para carregar este modelo. "
                    "Tente o modelo small ou medium."
                ) from exc
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log_callback(
                    f"Falha ao usar {device}: {exc}"
                )
                model = None
                gc.collect()
                continue

            self._model = model
            self._loaded_key = (resolved, device, compute)
            log_callback(
                f"Modelo pronto em {'GPU (CUDA)' if device == 'cuda' else 'CPU'}."
            )
            return model, device, compute

        # Todas as tentativas falharam.
        if any(d == "cuda" for d, _ in attempts) and len(attempts) == 1:
            raise TranscriptionError(
                "Não foi possível usar a GPU (CUDA). Verifique se há uma placa "
                "NVIDIA com os drivers e bibliotecas CUDA/cuDNN instalados, ou "
                "selecione CPU. Detalhe: "
                f"{last_error}"
            )
        raise TranscriptionError(
            "Falha ao carregar o modelo. Verifique a conexão de internet para o "
            f"download inicial e tente novamente. Detalhe: {last_error}"
        )

    def transcribe(
        self,
        canonical_audio_path: Path,
        timeline_duration: float,
        requested_model: str,
        requested_device: str,
        cancel_event: threading.Event,
        log_callback: Callable[[str], None],
        progress_callback: Callable[[float], None],
    ) -> TranscriptionResult:
        if cancel_event.is_set():
            raise TranscriptionCancelled()

        resolved = MODEL_MAP.get(requested_model, requested_model)
        model, device_used, compute_type = self._ensure_model(
            requested_model, requested_device, log_callback
        )

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
            device_requested=requested_device,
            device_used=device_used,
            compute_type=compute_type,
        )


def _warmup_gpu(model) -> None:
    """Força uma inferência mínima para validar as bibliotecas CUDA.

    Levanta exceção se a GPU não puder executar (ex.: cuBLAS/cuDNN ausentes),
    permitindo o fallback para CPU no modo automático.
    """

    import numpy as np

    t = np.arange(1600, dtype=np.float32) / 16000.0
    audio = (0.05 * np.sin(2.0 * np.pi * 300.0 * t)).astype(np.float32)
    segments, _ = model.transcribe(
        audio,
        language="pt",
        beam_size=1,
        vad_filter=False,
        word_timestamps=False,
    )
    for _ in segments:
        pass


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
