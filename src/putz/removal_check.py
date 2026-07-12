"""Verificacao leve de residuo apos a renderizacao final."""

from __future__ import annotations

import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .cutter import CutInterval, CutPlan, Toolchain, clean_timeline_join_map
from .detection import compile_term_specs, detect_match, detect_phrase_match
from .transcriber import Transcriber, WordToken

WINDOW_RADIUS_SEC = 1.5
VERIFY_MODEL = "small"
VERIFY_DEVICE = "cpu"
_CREATE_NO_WINDOW = 0x08000000 if __import__("os").name == "nt" else 0


@dataclass(frozen=True)
class RemovalVerificationCut:
    cut_id: int
    join_time: float
    status: str
    detected_terms: tuple[str, ...] = ()
    transcript_excerpt: str = ""
    detail: str = ""


@dataclass(frozen=True)
class RemovalVerificationResult:
    status: str
    cuts: tuple[RemovalVerificationCut, ...]
    model_requested: str = VERIFY_MODEL
    device_used: str = VERIFY_DEVICE
    detail: str = ""


@dataclass(frozen=True)
class _WindowToken:
    index: int
    start: float
    end: float
    token: WordToken


def verify_removal(
    *,
    toolchain: Toolchain,
    output_video: Path,
    output_duration: float,
    plan: CutPlan,
    configured_terms: Sequence[str],
    transcriber: Transcriber,
    cancel_event: threading.Event,
    work_dir: Path,
    log_callback: Callable[[str], None],
) -> RemovalVerificationResult:
    if not plan.cuts:
        return RemovalVerificationResult(status="nao_executada", cuts=())

    join_map = clean_timeline_join_map(plan.cuts)
    checks: list[RemovalVerificationCut] = []
    try:
        for cut in plan.cuts:
            if cancel_event.is_set():
                return RemovalVerificationResult(
                    status="indisponivel",
                    cuts=tuple(checks),
                    detail="Verificação cancelada.",
                )
            join_time = join_map.get(cut.id, 0.0)
            checks.append(
                _verify_cut_window(
                    toolchain=toolchain,
                    output_video=output_video,
                    output_duration=output_duration,
                    cut=cut,
                    join_time=join_time,
                    configured_terms=configured_terms,
                    transcriber=transcriber,
                    cancel_event=cancel_event,
                    work_dir=work_dir,
                    log_callback=log_callback,
                )
            )
    except Exception as exc:  # noqa: BLE001
        return RemovalVerificationResult(
            status="indisponivel",
            cuts=tuple(checks),
            detail=str(exc),
        )

    status = "ok"
    if any(item.status == "residuo_detectado" for item in checks):
        status = "residuo_detectado"
    elif any(item.status != "ok" for item in checks):
        status = "indisponivel"
    return RemovalVerificationResult(status=status, cuts=tuple(checks))


def _verify_cut_window(
    *,
    toolchain: Toolchain,
    output_video: Path,
    output_duration: float,
    cut: CutInterval,
    join_time: float,
    configured_terms: Sequence[str],
    transcriber: Transcriber,
    cancel_event: threading.Event,
    work_dir: Path,
    log_callback: Callable[[str], None],
) -> RemovalVerificationCut:
    start = max(0.0, join_time - WINDOW_RADIUS_SEC)
    end = min(output_duration, join_time + WINDOW_RADIUS_SEC)
    duration = max(0.0, end - start)
    if duration <= 0.10:
        return RemovalVerificationCut(
            cut_id=cut.id,
            join_time=join_time,
            status="indisponivel",
            detail="Janela de verificação curta demais.",
        )

    wav_path = work_dir / f"removal-check-{cut.id}-{uuid.uuid4().hex}.wav"
    _extract_window_wav(toolchain, output_video, start, duration, wav_path)
    log_callback(
        f"Verificando resíduo no corte {cut.id} (janela {start:.2f}s–{end:.2f}s do vídeo limpo)."
    )

    result = transcriber.transcribe(
        wav_path,
        duration,
        VERIFY_MODEL,
        VERIFY_DEVICE,
        cancel_event,
        lambda _message: None,
        lambda _progress: None,
    )
    detected_terms = _scan_detected_terms(result.words, configured_terms)
    excerpt = " ".join(word.text.strip() for word in result.words if word.text.strip())
    return RemovalVerificationCut(
        cut_id=cut.id,
        join_time=join_time,
        status="residuo_detectado" if detected_terms else "ok",
        detected_terms=detected_terms,
        transcript_excerpt=excerpt,
    )


def _extract_window_wav(
    toolchain: Toolchain,
    output_video: Path,
    start: float,
    duration: float,
    wav_path: Path,
) -> None:
    args = [
        str(toolchain.ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{duration:.6f}",
        "-i",
        str(output_video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-nostats",
        str(wav_path),
    ]
    result = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
        timeout=max(30, int(duration) + 30),
        shell=False,
        check=False,
    )
    if result.returncode != 0 or not wav_path.is_file():
        detail = result.stderr.strip() or "ffmpeg falhou."
        raise RuntimeError(f"Falha ao extrair áudio da verificação: {detail}")


def _scan_detected_terms(
    words: Sequence[WordToken],
    configured_terms: Sequence[str],
) -> tuple[str, ...]:
    specs = compile_term_specs(configured_terms)
    valid_tokens = [
        _WindowToken(index=i, start=word.start or 0.0, end=word.end or 0.0, token=word)
        for i, word in enumerate(words)
        if word.normalized and word.start is not None and word.end is not None
    ]
    detected: list[str] = []
    seen: set[str] = set()

    for position, token in enumerate(valid_tokens):
        for spec in specs:
            if spec.token_count > 1:
                indexes = detect_phrase_match(valid_tokens, position, spec)
                if indexes is None:
                    continue
                if spec.configured not in seen:
                    seen.add(spec.configured)
                    detected.append(spec.configured)
                continue
            match = detect_match(token.token, (spec,))
            if match is None or match.configured_term in seen:
                continue
            seen.add(match.configured_term)
            detected.append(match.configured_term)

    return tuple(detected)
