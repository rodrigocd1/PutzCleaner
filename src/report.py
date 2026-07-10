"""Relatório JSON auditável do PutzCleaner (seção 19).

Domínio puro: recebe objetos/dicionários já calculados e monta o payload.
Timestamps sempre se referem ao vídeo original. Arredonda apenas na
serialização; o cálculo é feito com precisão completa nas outras fases.
"""

from __future__ import annotations

import json
import os
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Mapping

from cutter import CutPlan, MediaInfo, RenderResult

SCHEMA_VERSION = 1


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def build_report(
    *,
    input_path: Path,
    output_path: Path,
    media_info: MediaInfo,
    plan: CutPlan,
    render: RenderResult,
    configured_terms: tuple[str, ...],
    model_requested: str,
    model_resolved: str,
    device_requested: str = "auto",
    device_used: str = "cpu",
    margin_before: float,
    margin_after: float,
    min_probability: float = 0.60,
    faster_whisper_version: str,
    ffmpeg_version: str,
    warnings: list[str] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """Monta o dicionário do relatório conforme o schema mínimo da seção 19."""

    when = generated_at or datetime.now().astimezone()

    # Mapa ocorrência -> corte_id e limites finais do corte.
    occurrence_cut: dict[int, tuple[int, float, float]] = {}
    for cut in plan.cuts:
        for occ_index in cut.occurrence_indexes:
            occurrence_cut[occ_index] = (cut.id, cut.start, cut.end)

    ocorrencias = []
    for i, occ in enumerate(plan.occurrences):
        cut_id, cut_start, cut_end = occurrence_cut.get(i, (None, None, None))
        ocorrencias.append(
            {
                "palavra_removida": occ.configured_term,
                "palavra_configurada": occ.configured_term,
                "texto_reconhecido": occ.recognized_text,
                "timestamp_inicial": _round(occ.word_start),
                "timestamp_final": _round(occ.word_end),
                "confianca": _round(occ.probability),
                "corte_id": cut_id,
                "candidato_inicio": _round(occ.candidate_start),
                "candidato_fim": _round(occ.candidate_end),
                "corte_final_inicio": _round(cut_start),
                "corte_final_fim": _round(cut_end),
            }
        )

    cortes = [
        {
            "id": cut.id,
            "inicio": _round(cut.start),
            "fim": _round(cut.end),
            "duracao": _round(cut.end - cut.start),
        }
        for cut in plan.cuts
    ]

    total_removido = sum(cut.end - cut.start for cut in plan.cuts)

    por_motivo = Counter(item.reason for item in plan.ignored)
    ignorados_itens = [
        {
            "texto_reconhecido": item.text,
            "timestamp_inicial": _round(item.start),
            "timestamp_final": _round(item.end),
            "confianca": _round(item.probability),
            "motivo": item.reason,
        }
        for item in plan.ignored
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "concluido",
        "gerado_em": when.isoformat(timespec="seconds"),
        "arquivo_original": str(input_path),
        "arquivo_gerado": str(output_path),
        "configuracao": {
            "palavras_removidas": list(configured_terms),
            "modelo_selecionado": model_requested,
            "modelo_resolvido": model_resolved,
            "dispositivo_selecionado": device_requested,
            "dispositivo_usado": device_used,
            "idioma": "pt",
            "margem_antes": _round(margin_before),
            "margem_depois": _round(margin_after),
            "limiar_confianca": _round(min_probability),
            "distancia_uniao": 0.12,
        },
        "midia": {
            "duracao_formato_original": _round(media_info.format_duration),
            "duracao_timeline": _round(media_info.timeline_duration),
            "duracao_saida_esperada": _round(plan.expected_output_duration),
            "duracao_saida_real": _round(render.actual_duration),
            "codec_video": render.video_codec,
            "codec_audio": render.audio_codec,
        },
        "resumo": {
            "total_ocorrencias": len(plan.occurrences),
            "total_cortes": len(plan.cuts),
            "duracao_total_removida": _round(total_removido),
        },
        "ocorrencias": ocorrencias,
        "cortes": cortes,
        "ignorados": {
            "total": len(plan.ignored),
            "por_motivo": dict(por_motivo),
            "itens": ignorados_itens,
        },
        "ferramentas": {
            "faster_whisper": faster_whisper_version,
            "ffmpeg": ffmpeg_version,
        },
        "avisos": list(warnings or []),
    }


def serialize_report(payload: Mapping[str, object]) -> str:
    """Serializa o relatório com allow_nan=False para nunca gerar JSON inválido."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )


def write_report_staged(destination: Path, payload: Mapping[str, object]) -> None:
    """Escreve o relatório em arquivo temporário e o move para ``destination``.

    ``destination`` é o caminho staged (``.putzcleaner-<uuid>.json``); a
    publicação para o nome final é feita pelo orquestrador (seção 18.5).
    """

    destination = Path(destination)
    text = serialize_report(payload)

    tmp = destination.parent / f".putzcleaner-report-{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.write("\n")
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
