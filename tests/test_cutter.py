from __future__ import annotations

from pathlib import Path

import pytest

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
    # Estende para dentro do silêncio retendo micro-pausa (0.05 antes, 0.075
    # depois — metade do silêncio disponível, limitado a 0.10).
    assert plan.occurrences[0].candidate_start == pytest.approx(0.87)
    assert plan.occurrences[0].candidate_end == pytest.approx(1.285)


def test_build_cut_plan_accepts_margins_above_recommended_limit() -> None:
    words = [
        _word("olá", 0.0, 0.4, normalized="olá"),
        _word("né", 1.0, 1.2, normalized="né"),
        _word("tchau", 3.8, 4.2, normalized="tchau"),
    ]

    plan = build_cut_plan(words, ["né"], 6.0, 3.0, 3.0, 0.6)

    assert len(plan.occurrences) == 1
    assert plan.occurrences[0].candidate_start == 0.0
    assert plan.occurrences[0].candidate_end == 4.2


def test_build_cut_plan_large_before_margin_overrides_neighbor_protection() -> None:
    words = [
        _word("fala", 9.0, 9.5, normalized="fala"),
        _word("né", 20.0, 20.2, normalized="né"),
        _word("depois", 20.8, 21.2, normalized="depois"),
    ]

    plan = build_cut_plan(words, ["né"], 30.0, 15.0, 0.1, 0.6)

    assert len(plan.occurrences) == 1
    assert plan.occurrences[0].candidate_start == 5.0
    assert plan.occurrences[0].candidate_end == 20.3


def test_build_cut_plan_large_margin_is_not_shortened_by_silence_snap() -> None:
    words = [
        _word("fala", 9.0, 9.5, normalized="fala"),
        _word("né", 20.0, 20.2, normalized="né"),
    ]
    profile = AudioProfile(
        silence_spans=(
            SilenceSpan(19.6, 19.95),
            SilenceSpan(20.22, 20.5),
        ),
        noise_floor_db=-55.0,
    )

    plan = build_cut_plan(words, ["né"], 30.0, 15.0, 4.0, 0.6, audio_profile=profile)

    assert len(plan.occurrences) == 1
    assert plan.occurrences[0].candidate_start == 5.0
    assert plan.occurrences[0].candidate_end == 24.2


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


def test_build_cut_plan_matches_nasal_filler_a() -> None:
    # Regressão: o "ã" nasal estava sendo bloqueado pelo guarda de vogais.
    words = [
        _word("então", 0.0, 0.4, normalized="então"),
        _word("ã", 1.0, 1.15, normalized="ã", probability=0.7),
        _word("depois", 1.6, 2.0, normalized="depois"),
    ]

    plan = build_cut_plan(words, ["ã"], 3.0, 0.05, 0.08, 0.35)

    assert len(plan.occurrences) == 1
    assert plan.occurrences[0].normalized_term == "ã"


def test_build_cut_plan_protects_plain_vowel_glued_in_speech() -> None:
    # "é" copular ("isso é bom") nunca pode ser removido, mesmo listado.
    words = [
        _word("isso", 0.0, 0.3, normalized="isso"),
        _word("é", 0.31, 0.42, normalized="é", probability=0.95),
        _word("bom", 0.43, 0.8, normalized="bom"),
    ]

    plan = build_cut_plan(words, ["é"], 2.0, 0.05, 0.08, 0.2)

    assert not plan.occurrences
    assert len(plan.ignored) == 1
    assert plan.ignored[0].reason == "contexto_nao_isolado"


def test_build_cut_plan_removes_isolated_plain_vowel_hesitation() -> None:
    # "é..." como hesitação isolada por pausas largas pode ser removido.
    words = [
        _word("então", 0.0, 0.4, normalized="então"),
        _word("é", 0.8, 1.0, normalized="é", probability=0.6),
        _word("vamos", 1.4, 1.8, normalized="vamos"),
    ]

    plan = build_cut_plan(words, ["é"], 3.0, 0.05, 0.08, 0.35)

    assert len(plan.occurrences) == 1
    assert plan.occurrences[0].normalized_term == "é"


def test_build_cut_plan_lexical_requires_high_confidence() -> None:
    # Limiar por classe: "tipo" isolado com prob 0.5 falha (lexical exige
    # >= 0.55 mesmo com limiar base 0.2); com prob 0.8 passa.
    words_low = [
        _word("foi", 0.0, 0.3, normalized="foi"),
        _word("tipo", 0.7, 1.0, normalized="tipo", probability=0.5),
        _word("aí", 1.4, 1.7, normalized="aí"),
    ]
    plan_low = build_cut_plan(words_low, ["tipo"], 3.0, 0.05, 0.08, 0.2)
    assert not plan_low.occurrences
    assert plan_low.ignored[0].reason == REASON_LOW_CONFIDENCE

    words_high = [
        _word("foi", 0.0, 0.3, normalized="foi"),
        _word("tipo", 0.7, 1.0, normalized="tipo", probability=0.8),
        _word("aí", 1.4, 1.7, normalized="aí"),
    ]
    plan_high = build_cut_plan(words_high, ["tipo"], 3.0, 0.05, 0.08, 0.2)
    assert len(plan_high.occurrences) == 1
    assert plan_high.occurrences[0].word_class == "lexical"


def test_build_cut_plan_rejects_glued_low_confidence_filler() -> None:
    # "né" com prob 0.25 colado na fala dos dois lados costuma ser pedaço
    # mal reconhecido de palavra real.
    words = [
        _word("você", 0.0, 0.3, normalized="você"),
        _word("né", 0.31, 0.45, normalized="né", probability=0.25),
        _word("sabe", 0.46, 0.8, normalized="sabe"),
    ]

    plan = build_cut_plan(words, ["né"], 2.0, 0.02, 0.02, 0.2)

    assert not plan.occurrences
    assert plan.ignored[0].reason == "baixa_confianca_sem_pausa"


def test_build_cut_plan_rejects_implausible_filler_duration() -> None:
    words = [
        _word("antes", 0.0, 0.4, normalized="antes"),
        _word("hum", 1.0, 2.8, normalized="hum", probability=0.9),
        _word("depois", 3.2, 3.6, normalized="depois"),
    ]

    plan = build_cut_plan(words, ["hum"], 5.0, 0.05, 0.08, 0.35)

    assert not plan.occurrences
    assert plan.ignored[0].reason == "duracao_implausivel"


def test_build_cut_plan_drops_cuts_below_minimum_duration() -> None:
    words = [
        _word("antes", 0.5, 0.9, normalized="antes"),
        _word("né", 1.0, 1.05, normalized="né", probability=0.9),
        _word("depois", 1.2, 1.6, normalized="depois"),
    ]

    plan = build_cut_plan(words, ["né"], 3.0, 0.0, 0.0, 0.6)

    assert not plan.occurrences
    assert not plan.cuts
    assert plan.ignored[0].reason == "corte_muito_curto"
    assert plan.keeps == (KeepInterval(0.0, 3.0),)


def test_build_cut_plan_merges_cuts_separated_only_by_silence() -> None:
    words = [
        _word("né", 1.0, 1.2, normalized="né"),
        _word("né", 1.55, 1.7, normalized="né"),
        _word("tchau", 2.2, 2.6, normalized="tchau"),
    ]
    profile = AudioProfile(
        silence_spans=(SilenceSpan(1.2, 1.6),),
        noise_floor_db=-55.0,
    )

    plan_sem_perfil = build_cut_plan(words, ["né"], 4.0, 0.05, 0.08, 0.6)
    plan_com_perfil = build_cut_plan(
        words, ["né"], 4.0, 0.05, 0.08, 0.6, audio_profile=profile
    )

    assert len(plan_sem_perfil.cuts) == 2
    assert len(plan_com_perfil.cuts) == 1
    assert plan_com_perfil.cuts[0].occurrence_indexes == (0, 1)


def test_build_cut_plan_phrase_with_lexical_part_requires_isolation() -> None:
    # "tipo assim" emendado em fala legítima ("é tipo assim que funciona")
    # não pode ser removido.
    words = [
        _word("é", 0.0, 0.1, normalized="é"),
        _word("tipo", 0.12, 0.3, normalized="tipo"),
        _word("assim", 0.32, 0.5, normalized="assim"),
        _word("que", 0.52, 0.7, normalized="que"),
    ]

    plan = build_cut_plan(words, ["tipo assim"], 2.0, 0.05, 0.08, 0.6)

    assert not plan.occurrences
    assert plan.ignored[0].reason == "contexto_nao_isolado"


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
