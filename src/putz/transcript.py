"""Geração da transcrição legível com tempos do vídeo (arquivo .txt).

Produz um texto com a transcrição reconhecida, agrupada por segmentos e com
o tempo do vídeo, marcando com ``[removida]`` cada palavra que foi
efetivamente cortada do vídeo limpo.

Domínio puro: recebe objetos já calculados; não importa ``gui``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Sequence

from .cutter import CutInterval, CutOccurrence, clean_timeline_join_map
from .transcriber import WordToken

def _fmt_ts(seconds: float | None) -> str:
    if seconds is None:
        return "--:--:--.---"
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def _removed_word_indexes(
    words: Sequence[WordToken],
    occurrences: Sequence[CutOccurrence],
) -> set[int]:
    """Mapeia cada ocorrência aceita para o índice da palavra correspondente."""

    removed: set[int] = set()
    for occ in occurrences:
        removed.update(occ.token_indexes)
    return removed


def build_transcript(
    words: Sequence[WordToken],
    occurrences: Sequence[CutOccurrence],
    cuts: Sequence[CutInterval],
    *,
    input_name: str,
    model_label: str,
    device_label: str,
) -> str:
    """Constrói o texto da transcrição com tempos e marcação de removidas."""

    removed = _removed_word_indexes(words, occurrences)
    cut_join_map = clean_timeline_join_map(cuts)
    occurrence_join_map: dict[int, float] = {}
    for cut in cuts:
        join_time = cut_join_map.get(cut.id, 0.0)
        for occ_index in cut.occurrence_indexes:
            occurrence_join_map[occ_index] = join_time
    word_join_map: dict[int, float] = {}
    for occ_index, occ in enumerate(occurrences):
        join_time = occurrence_join_map.get(occ_index)
        if join_time is None:
            continue
        for token_index in occ.token_indexes:
            word_join_map[token_index] = join_time

    lines: list[str] = []
    lines.append("Transcrição do PutzCleaner")
    lines.append(f"Arquivo: {input_name}")
    lines.append(f"Modelo: {model_label} | Dispositivo: {device_label}")
    lines.append(
        "As palavras seguidas de [removida] foram cortadas do vídeo limpo."
    )
    lines.append("Os tempos referem-se ao vídeo original.")
    lines.append("Quando disponível, a marcação informa a junção no vídeo limpo.")
    lines.append("")

    if not words:
        lines.append("(Nenhuma palavra foi reconhecida.)")
        return "\n".join(lines) + "\n"

    # Agrupar por segmento preservando a ordem de aparição.
    current_segment: int | None = None
    seg_words: list[tuple[int, WordToken]] = []

    def flush_segment() -> None:
        if not seg_words:
            return
        seg_start = seg_words[0][1].start
        seg_end = seg_words[-1][1].end
        pieces: list[str] = []
        for idx, word in seg_words:
            text = word.text.strip()
            if not text:
                continue
            if idx in removed:
                clean_time = word_join_map.get(idx)
                if clean_time is None:
                    pieces.append(f"{text} [removida {_fmt_ts(word.start)}]")
                else:
                    pieces.append(
                        f"{text} [removida -> vídeo limpo {_fmt_ts(clean_time)}]"
                    )
            else:
                pieces.append(text)
        prefix = f"[{_fmt_ts(seg_start)} -> {_fmt_ts(seg_end)}]"
        lines.append(f"{prefix} {' '.join(pieces)}")

    for i, word in enumerate(words):
        if current_segment is None:
            current_segment = word.segment_id
        if word.segment_id != current_segment:
            flush_segment()
            seg_words = []
            current_segment = word.segment_id
        seg_words.append((i, word))
    flush_segment()

    lines.append("")
    lines.append(f"Total de palavras removidas: {len(removed)}")

    return "\n".join(lines) + "\n"


def write_transcript_staged(destination: Path, text: str) -> None:
    """Escreve a transcrição em arquivo temporário e o move para ``destination``.

    ``destination`` é o caminho staged; a publicação para o nome final é feita
    pelo orquestrador, junto com o vídeo e o relatório.
    """

    destination = Path(destination)
    tmp = destination.parent / f".putzcleaner-transcript-{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, destination)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise

