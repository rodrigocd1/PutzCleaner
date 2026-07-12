from __future__ import annotations

from pathlib import Path

from cutter import (
    REASON_DURATION_INVALID,
    REASON_LOW_CONFIDENCE,
    REASON_OVERLAP_PROTECTED,
    REASON_TS_MISSING,
    REASON_TS_OUT_OF_BOUNDS,
    CutInterval,
    KeepInterval,
    MediaInfo,
    MediaStream,
    _build_filtergraph,
    _build_keeps,
    _clamp_interval,
    _confidence_reason,
    _merge_candidates,
    _video_encoder_args,
    build_cut_plan,
    compute_output_paths,
)
from putz.audio_analysis import AudioProfile, SilenceSpan
from transcriber import WordToken


def _word(
    text: str,
    start: float | None,
    end: float | None,
    probability: float | None = 0.9,
    *,
    normalized: str | None = None,
    segment_id: int = 0,
    segment_avg_logprob: float | None = -0.2,
    segment_no_speech_prob: float | None = 0.1,
) -> WordToken:
    return WordToken(
        text=text,
        normalized=normalized or text.casefold(),
        start=start,
        end=end,
        probability=probability,
        segment_id=segment_id,
        segment_avg_logprob=segment_avg_logprob,
        segment_no_speech_prob=segment_no_speech_prob,
    )


def test_clamp_interval_clamps_small_overflow_and_underflow() -> None:
    assert _clamp_interval(-0.01, 10.01, 10.0) == (0.0, 10.0)


def test_clamp_interval_rejects_missing_and_out_of_bounds() -> None:
    assert _clamp_interval(None, 1.0, 10.0) == REASON_TS_MISSING
    assert _clamp_interval(-0.5, 1.0, 10.0) == REASON_TS_OUT_OF_BOUNDS


def test_confidence_reason_accepts_safe_token() -> None:
    token = _word("né", 1.0, 1.2, 0.8, normalized="né")
    assert _confidence_reason(token, 0.6) is None


def test_confidence_reason_rejects_low_confidence_and_bad_duration() -> None:
    low = _word("né", 1.0, 1.2, 0.2, normalized="né")
    invalid = _word("né", 1.0, 1.0, 0.9, normalized="né")
    assert _confidence_reason(low, 0.6) == REASON_LOW_CONFIDENCE
    assert _confidence_reason(invalid, 0.6) == REASON_DURATION_INVALID


def test_build_cut_plan_creates_safe_cut_and_keeps() -> None:
    words = [
        _word("olá", 0.0, 0.4, normalized="olá"),
        _word("né", 1.0, 1.2, normalized="né"),
        _word("tchau", 1.35, 1.8, normalized="tchau"),
    ]

    plan = build_cut_plan(words, ["né"], 3.0, 0.05, 0.08, 0.6)

    assert len(plan.occurrences) == 1
    assert len(plan.cuts) == 1
    assert plan.occurrences[0].normalized_term == "né"
    assert plan.occurrences[0].token_indexes == (1,)
    assert plan.cuts[0].start == 0.95
    assert plan.cuts[0].end == 1.28
    assert plan.keeps == (
        KeepInterval(start=0.0, end=0.95),
        KeepInterval(start=1.28, end=3.0),
    )


def test_build_cut_plan_ignores_target_when_margin_eats_core() -> None:
    words = [
        _word("antes", 0.0, 1.05, normalized="antes"),
        _word("né", 1.0, 1.1, normalized="né"),
        _word("depois", 1.08, 2.0, normalized="depois"),
    ]

    plan = build_cut_plan(words, ["né"], 3.0, 0.2, 0.2, 0.6)

    assert not plan.occurrences
    assert len(plan.ignored) == 1
    assert plan.ignored[0].reason == REASON_OVERLAP_PROTECTED


def test_merge_candidates_merges_nearby_occurrences_without_protected_overlap() -> None:
    occurrence_a = type("Occ", (), {
        "candidate_start": 1.0,
        "candidate_end": 1.2,
    })
    occurrence_b = type("Occ", (), {
        "candidate_start": 1.25,
        "candidate_end": 1.4,
    })

    cuts = _merge_candidates([occurrence_a, occurrence_b], [], 5.0)

    assert cuts == [
        CutInterval(id=1, start=1.0, end=1.4, occurrence_indexes=(0, 1))
    ]


def test_build_keeps_returns_remaining_segments() -> None:
    keeps = _build_keeps(
        [
            CutInterval(id=1, start=1.0, end=1.5, occurrence_indexes=(0,)),
            CutInterval(id=2, start=3.0, end=3.2, occurrence_indexes=(1,)),
        ],
        4.0,
    )
    assert keeps == [
        KeepInterval(start=0.0, end=1.0),
        KeepInterval(start=1.5, end=3.0),
        KeepInterval(start=3.2, end=4.0),
    ]


def test_build_filtergraph_single_keep_snapshot() -> None:
    graph = _build_filtergraph(
        0,
        1,
        [KeepInterval(start=0.5, end=2.0)],
        3.0,
    )
    assert graph == (
        "[0:0]setpts=PTS-STARTPTS,trim=start=0.500000:end=2.000000,"
        "setpts=PTS-STARTPTS,pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p[vout];\n"
        "[0:1]aresample=async=1:first_pts=0,apad,atrim=start=0:end=3.000000,"
        "asetpts=PTS-STARTPTS,atrim=start=0.500000:end=2.000000,asetpts=PTS-STARTPTS,"
        "afade=t=in:st=0:d=0.012000,afade=t=out:st=1.488000:d=0.012000[aout]"
    )


def test_compute_output_paths_uses_expected_suffixes() -> None:
    video, report, transcript = compute_output_paths(
        Path("entrada.mp4"),
        Path("saida"),
    )
    assert video == Path("saida") / "entrada_limpo.mp4"
    assert report == Path("saida") / "entrada_limpo_relatorio.json"
    assert transcript == Path("saida") / "entrada_limpo_transcricao.txt"


def test_build_cut_plan_treats_elongated_fillers_as_matches() -> None:
    words = [
        _word("nééé", 1.0, 1.25, normalized="nééé"),
        _word("tchau", 1.5, 2.0, normalized="tchau"),
    ]

    plan = build_cut_plan(words, ["né"], 3.0, 0.05, 0.08, 0.6)

    assert len(plan.occurrences) == 1
    assert plan.occurrences[0].configured_term == "né"


def test_build_cut_plan_keeps_lexical_tipo_without_pause_context() -> None:
    words = [
        _word("um", 0.0, 0.2, normalized="um"),
        _word("tipo", 0.21, 0.5, normalized="tipo"),
        _word("de", 0.51, 0.7, normalized="de"),
    ]

    plan = build_cut_plan(words, ["tipo"], 2.0, 0.05, 0.08, 0.6)

    assert not plan.occurrences
    assert len(plan.ignored) == 1
    assert plan.ignored[0].reason == "contexto_nao_isolado"


def test_build_cut_plan_refines_boundaries_with_silence_profile() -> None:
    words = [
        _word("né", 1.0, 1.2, normalized="né"),
        _word("fala", 1.5, 2.0, normalized="fala"),
    ]
    profile = AudioProfile(
        silence_spans=(
            SilenceSpan(0.82, 0.98),
            SilenceSpan(1.21, 1.36),
        ),
        noise_floor_db=-55.0,
    )

    plan = build_cut_plan(words, ["né"], 3.0, 0.2, 0.2, 0.6, audio_profile=profile)

    assert len(plan.occurrences) == 1
    assert plan.occurrences[0].candidate_start == 0.86
    assert plan.occurrences[0].candidate_end == 1.3


def test_build_cut_plan_matches_phrase_term() -> None:
    words = [
        _word("isso", 0.0, 0.2, normalized="isso"),
        _word("tipo", 0.5, 0.7, normalized="tipo"),
        _word("assim", 0.75, 1.0, normalized="assim"),
        _word("mesmo", 1.4, 1.8, normalized="mesmo"),
    ]

    plan = build_cut_plan(words, ["tipo assim"], 3.0, 0.05, 0.08, 0.6)

    assert len(plan.occurrences) == 1
    assert plan.occurrences[0].configured_term == "tipo assim"
    assert plan.occurrences[0].recognized_text == "tipo assim"
    assert plan.occurrences[0].token_indexes == (1, 2)


def test_build_cut_plan_marks_incomplete_phrase_as_ignored() -> None:
    words = [
        _word("tipo", 0.5, 0.7, normalized="tipo"),
        _word("coisa", 0.75, 1.0, normalized="coisa"),
    ]

    plan = build_cut_plan(words, ["tipo assim"], 2.0, 0.05, 0.08, 0.6)

    assert not plan.occurrences
    assert len(plan.ignored) == 1
    assert plan.ignored[0].reason == "frase_incompleta"


def test_video_encoder_args_supports_cpu_and_nvenc() -> None:
    assert _video_encoder_args("libx264") == [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
    ]
    assert _video_encoder_args("h264_nvenc") == [
        "-c:v",
        "h264_nvenc",
        "-preset",
        "p5",
        "-rc",
        "vbr",
        "-cq",
        "23",
        "-b:v",
        "0",
    ]
