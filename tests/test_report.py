from __future__ import annotations

from datetime import datetime
from pathlib import Path

from cutter import (
    CutInterval,
    CutOccurrence,
    CutPlan,
    IgnoredOccurrence,
    KeepInterval,
    MediaInfo,
    MediaStream,
    RenderResult,
)
from report import build_report


def test_build_report_snapshot_like_payload() -> None:
    media = MediaInfo(
        timeline_duration=10.0,
        format_duration=10.1,
        format_start_time=0.0,
        video_stream=MediaStream(0, "video", "h264", 0.0, 10.0, False),
        audio_stream=MediaStream(1, "audio", "aac", 0.0, 10.0, False),
        width=1920,
        height=1080,
        fps=30.0,
    )
    plan = CutPlan(
        occurrences=(
            CutOccurrence("né", "né", "né", 1.0, 1.2, 0.91, 0.95, 1.28),
        ),
        ignored=(
            IgnoredOccurrence("assim", "assim", 2.0, 2.2, 0.3, "baixa_confianca"),
        ),
        cuts=(CutInterval(1, 0.95, 1.28, (0,)),),
        keeps=(
            KeepInterval(0.0, 0.95),
            KeepInterval(1.28, 10.0),
        ),
        expected_output_duration=9.67,
    )
    render = RenderResult(Path("staged.mp4"), 9.67, "h264", "aac")

    payload = build_report(
        input_path=Path("entrada.mp4"),
        output_path=Path("saida.mp4"),
        media_info=media,
        plan=plan,
        render=render,
        configured_terms=("né", "assim"),
        model_requested="small",
        model_resolved="small",
        device_requested="auto",
        device_used="cpu",
        margin_before=0.05,
        margin_after=0.08,
        min_probability=0.6,
        faster_whisper_version="1.2.1",
        ffmpeg_version="ffmpeg test",
        generated_at=datetime.fromisoformat("2026-07-11T21:45:00-03:00"),
    )

    assert payload["schema_version"] == 1
    assert payload["resumo"] == {
        "total_ocorrencias": 1,
        "total_cortes": 1,
        "duracao_total_removida": 0.33,
    }
    assert payload["ignorados"]["por_motivo"] == {"baixa_confianca": 1}
    assert payload["ocorrencias"][0]["corte_id"] == 1
