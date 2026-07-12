"""Planejamento de cortes e renderização FFmpeg do PutzCleaner.

Duas responsabilidades, ambas de domínio (sem importar ``gui``):

1. Lógica pura de cortes (``build_cut_plan`` e helpers): a partir de
   ``WordToken`` e da lista de termos, decide o que remover com proteção
   temporal das palavras vizinhas. Testável sem FFmpeg.
2. Toolchain FFmpeg/ffprobe: descoberta, validação, probe, extração do WAV
   canônico, construção do filtergraph, renderização com progresso e
   verificação final.

Consulte as seções 8, 11.2, 14, 15, 16, 17 e 18 do plano.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .detection import (
    compile_term_specs,
    detect_match,
    lexical_context_reason,
)
from .audio_analysis import AudioProfile, refine_cut_bounds
from .transcriber import (
    EPSILON,
    MAX_SEGMENT_NO_SPEECH,
    MAX_WORD_DURATION_SEC,
    MIN_SEGMENT_AVG_LOGPROB,
    MIN_WORD_DURATION_SEC,
    MIN_WORD_PROBABILITY,
    TIMESTAMP_TOLERANCE_SEC,
    WordToken,
    _sanitize_float,
)

# ---------------------------------------------------------------------------
# Constantes de corte (seção 8)
# ---------------------------------------------------------------------------

MERGE_GAP_SEC = 0.12
MAX_MARGIN_SEC = 2.00
MAX_KEEPS_PER_GRAPH = 100
DEFAULT_AUDIO_FADE_SEC = 0.012

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_SUBPROCESS_TIMEOUT_SHORT = 10
_AV_OFFSET_TOLERANCE_SEC = 0.05
_DURATION_MATCH_TOLERANCE_SEC = 0.05

# Razões canônicas de ocorrência ignorada (seção 15).
REASON_PROB_MISSING = "probabilidade_ausente"
REASON_LOW_CONFIDENCE = "baixa_confianca"
REASON_TS_MISSING = "timestamp_ausente"
REASON_TS_NOT_FINITE = "timestamp_nao_finito"
REASON_TS_OUT_OF_BOUNDS = "timestamp_fora_do_video"
REASON_DURATION_INVALID = "duracao_invalida"
REASON_SEGMENT_UNSAFE = "segmento_inseguro"
REASON_OVERLAP_PROTECTED = "sobreposicao_com_palavra_protegida"
REASON_MARGIN_ATE_TARGET = "margem_eliminou_o_alvo"


class CutterError(RuntimeError):
    """Erro de domínio do cutter, com mensagem amigável ao usuário."""


class UnsafeCutPlanError(CutterError):
    """Levantado quando o plano de cortes ficaria inseguro (fala protegida)."""


class RenderCancelled(RuntimeError):
    """Sinaliza cancelamento durante a renderização."""


# ---------------------------------------------------------------------------
# Modelo de dados (seção 11.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Toolchain:
    ffmpeg: Path
    ffprobe: Path
    ffmpeg_version: str
    ffprobe_version: str
    filter_file_option: str


@dataclass(frozen=True)
class MediaStream:
    global_index: int
    codec_type: str
    codec_name: str
    start_time: float | None
    duration: float | None
    attached_picture: bool


@dataclass(frozen=True)
class MediaInfo:
    timeline_duration: float
    format_duration: float | None
    format_start_time: float | None
    video_stream: MediaStream
    audio_stream: MediaStream
    width: int
    height: int
    fps: float | None


@dataclass(frozen=True)
class IgnoredOccurrence:
    text: str
    normalized: str
    start: float | None
    end: float | None
    probability: float | None
    reason: str


@dataclass(frozen=True)
class CutOccurrence:
    configured_term: str
    recognized_text: str
    normalized_term: str
    word_start: float
    word_end: float
    probability: float
    candidate_start: float
    candidate_end: float


@dataclass(frozen=True)
class CutInterval:
    id: int
    start: float
    end: float
    occurrence_indexes: tuple[int, ...]


@dataclass(frozen=True)
class KeepInterval:
    start: float
    end: float


@dataclass(frozen=True)
class CutPlan:
    occurrences: tuple[CutOccurrence, ...]
    ignored: tuple[IgnoredOccurrence, ...]
    cuts: tuple[CutInterval, ...]
    keeps: tuple[KeepInterval, ...]
    expected_output_duration: float


@dataclass(frozen=True)
class RenderResult:
    staged_video: Path
    actual_duration: float
    video_codec: str
    audio_codec: str


# ===========================================================================
# PARTE 1 — LÓGICA PURA DE CORTES (seções 15 e 16)
# ===========================================================================


@dataclass(frozen=True)
class _ValidToken:
    """Token com intervalo temporal já validado/clampado."""

    index: int
    start: float
    end: float
    token: WordToken


def _clamp_interval(
    start: float | None,
    end: float | None,
    timeline_duration: float,
) -> tuple[float, float] | str:
    """Retorna (start, end) clampado ou uma razão canônica de erro.

    Aplica clamp externo de até ``TIMESTAMP_TOLERANCE_SEC`` (20 ms).
    """

    if start is None or end is None:
        return REASON_TS_MISSING

    tol = TIMESTAMP_TOLERANCE_SEC

    # Clamp do início dentro de [-tol, 0] -> 0.
    if -tol <= start < 0:
        start = 0.0
    # Clamp do fim dentro de [duração, duração + tol] -> duração.
    if timeline_duration < end <= timeline_duration + tol:
        end = timeline_duration

    if start < 0 or start > timeline_duration:
        return REASON_TS_OUT_OF_BOUNDS
    if end < 0 or end > timeline_duration:
        return REASON_TS_OUT_OF_BOUNDS
    if end <= start:
        return REASON_DURATION_INVALID

    return (start, end)


def _confidence_reason(
    token: WordToken, min_probability: float = MIN_WORD_PROBABILITY
) -> str | None:
    """Aplica o critério de confiança (seção 15). None significa aprovado.

    Não avalia sobreposição com palavra protegida (item 12) nem margens;
    isso é tratado depois em ``build_cut_plan``.
    """

    if token.probability is None:
        return REASON_PROB_MISSING
    if token.start is None or token.end is None:
        return REASON_TS_MISSING
    if token.probability < min_probability:
        return REASON_LOW_CONFIDENCE
    if token.end <= token.start:
        return REASON_DURATION_INVALID

    duration = token.end - token.start
    if duration < MIN_WORD_DURATION_SEC or duration > MAX_WORD_DURATION_SEC:
        return REASON_DURATION_INVALID

    if token.segment_no_speech_prob is None or token.segment_avg_logprob is None:
        return REASON_SEGMENT_UNSAFE
    if token.segment_no_speech_prob > MAX_SEGMENT_NO_SPEECH:
        return REASON_SEGMENT_UNSAFE
    if token.segment_avg_logprob < MIN_SEGMENT_AVG_LOGPROB:
        return REASON_SEGMENT_UNSAFE

    return None


def _intervals_overlap(
    a_start: float, a_end: float, b_start: float, b_end: float
) -> float:
    """Comprimento da sobreposição de [a) e [b), zero se não houver."""

    lo = max(a_start, b_start)
    hi = min(a_end, b_end)
    return max(0.0, hi - lo)


def build_cut_plan(
    words: Sequence[WordToken],
    configured_terms: Sequence[str],
    timeline_duration: float,
    margin_before: float,
    margin_after: float,
    min_probability: float = MIN_WORD_PROBABILITY,
    audio_profile: AudioProfile | None = None,
) -> CutPlan:
    """Constrói o plano de cortes seguro (seção 16)."""

    # --- 16.1 Preparação / validação de entrada ---
    td = _sanitize_float(timeline_duration)
    if td is None or td <= 0:
        raise CutterError("Duração da timeline inválida.")
    mb = _sanitize_float(margin_before)
    ma = _sanitize_float(margin_after)
    if mb is None or ma is None or not (0.0 <= mb <= MAX_MARGIN_SEC) or not (
        0.0 <= ma <= MAX_MARGIN_SEC
    ):
        raise CutterError("Margens inválidas; use valores entre 0 e 2 segundos.")
    minp = _sanitize_float(min_probability)
    if minp is None or not (0.0 <= minp <= 1.0):
        raise CutterError("Limiar de confiança inválido; use um valor entre 0 e 1.")

    term_specs = compile_term_specs(configured_terms)
    if not term_specs:
        raise CutterError("Nenhum termo válido para remover.")

    ignored: list[IgnoredOccurrence] = []

    # Visão sanitizada: separa tokens temporalmente válidos e registra alvos
    # inválidos como ignorados.
    valid_tokens: list[_ValidToken] = []
    for idx, token in enumerate(words):
        clamped = _clamp_interval(token.start, token.end, td)
        if isinstance(clamped, str):
            if detect_match(token, term_specs) is not None:
                ignored.append(
                    IgnoredOccurrence(
                        text=token.text,
                        normalized=token.normalized,
                        start=token.start,
                        end=token.end,
                        probability=token.probability,
                        reason=clamped,
                    )
                )
            continue
        cstart, cend = clamped
        valid_tokens.append(_ValidToken(idx, cstart, cend, token))

    # Ordenar tokens válidos por (start, end, segment_id) sem arredondar.
    valid_tokens.sort(key=lambda vt: (vt.start, vt.end, vt.token.segment_id))

    # --- Classificar em alvos-candidatos e palavras protegidas ---
    # Protegidas: não-alvos válidos + alvos que falham no critério de confiança.
    candidate_targets: list[tuple[_ValidToken, str, str]] = []
    protected: list[tuple[float, float]] = []

    for index, vt in enumerate(valid_tokens):
        match = detect_match(vt.token, term_specs)
        if match is None:
            protected.append((vt.start, vt.end))
            continue
        context_reason = lexical_context_reason(
            index=index,
            valid_tokens=valid_tokens,
            is_lexical=match.normalized_term in {"tipo", "assim"},
        )
        if context_reason is not None:
            protected.append((vt.start, vt.end))
            ignored.append(
                IgnoredOccurrence(
                    text=vt.token.text,
                    normalized=match.normalized_term,
                    start=vt.start,
                    end=vt.end,
                    probability=vt.token.probability,
                    reason=context_reason,
                )
            )
            continue
        reason = _confidence_reason(vt.token, minp)
        if reason is None:
            candidate_targets.append((vt, match.configured_term, match.normalized_term))
        else:
            protected.append((vt.start, vt.end))
            ignored.append(
                IgnoredOccurrence(
                    text=vt.token.text,
                    normalized=match.normalized_term,
                    start=vt.start,
                    end=vt.end,
                    probability=vt.token.probability,
                    reason=reason,
                )
            )

    protected.sort()

    # --- 16.2 Avaliação dos alvos ---
    occurrences: list[CutOccurrence] = []

    for vt, configured_term, normalized_term in candidate_targets:
        word_start, word_end = vt.start, vt.end

        # Item 12: núcleo não pode sobrepor palavra protegida.
        core_overlaps = any(
            _intervals_overlap(word_start, word_end, ps, pe) > EPSILON
            for ps, pe in protected
        )
        if core_overlaps:
            ignored.append(_ignored_from(vt, REASON_OVERLAP_PROTECTED))
            continue

        candidate_start = max(0.0, word_start - mb)
        candidate_end = min(td, word_end + ma)

        # Proteger vizinha anterior: maior end <= word_start + EPSILON.
        prev_end = None
        for ps, pe in protected:
            if pe <= word_start + EPSILON:
                if prev_end is None or pe > prev_end:
                    prev_end = pe
            # protected está ordenado por start; não dá para parar cedo pelo end.
        if prev_end is not None:
            candidate_start = max(candidate_start, prev_end)

        # Proteger vizinha posterior: menor start >= word_end - EPSILON.
        next_start = None
        for ps, pe in protected:
            if ps >= word_end - EPSILON:
                if next_start is None or ps < next_start:
                    next_start = ps
        if next_start is not None:
            candidate_end = min(candidate_end, next_start)

        candidate_start, candidate_end = refine_cut_bounds(
            candidate_start=candidate_start,
            candidate_end=candidate_end,
            word_start=word_start,
            word_end=word_end,
            audio_profile=audio_profile,
        )

        # Item 7: a proteção não pode invadir o núcleo do alvo.
        if (
            candidate_start > word_start + EPSILON
            or candidate_end < word_end - EPSILON
            or candidate_end <= candidate_start + EPSILON
        ):
            ignored.append(_ignored_from(vt, REASON_MARGIN_ATE_TARGET))
            continue

        # Item 8: revalidar que o candidato inteiro não intersecta protegida.
        if any(
            _intervals_overlap(candidate_start, candidate_end, ps, pe) > EPSILON
            for ps, pe in protected
        ):
            ignored.append(_ignored_from(vt, REASON_OVERLAP_PROTECTED))
            continue

        occurrences.append(
            CutOccurrence(
                configured_term=configured_term,
                recognized_text=vt.token.text,
                normalized_term=normalized_term,
                word_start=word_start,
                word_end=word_end,
                probability=float(vt.token.probability),
                candidate_start=candidate_start,
                candidate_end=candidate_end,
            )
        )

    # --- 16.3 União dos candidatos ---
    cuts = _merge_candidates(occurrences, protected, td)

    # --- 16.4 Complemento preservado ---
    keeps = _build_keeps(cuts, td)
    if not keeps:
        raise CutterError("Os cortes calculados removeriam todo o vídeo.")

    expected_output_duration = sum(k.end - k.start for k in keeps)

    return CutPlan(
        occurrences=tuple(occurrences),
        ignored=tuple(ignored),
        cuts=tuple(cuts),
        keeps=tuple(keeps),
        expected_output_duration=expected_output_duration,
    )


def _ignored_from(vt: _ValidToken, reason: str) -> IgnoredOccurrence:
    return IgnoredOccurrence(
        text=vt.token.text,
        normalized=vt.token.normalized,
        start=vt.start,
        end=vt.end,
        probability=vt.token.probability,
        reason=reason,
    )


def _merge_candidates(
    occurrences: list[CutOccurrence],
    protected: list[tuple[float, float]],
    timeline_duration: float,
) -> list[CutInterval]:
    if not occurrences:
        return []

    order = sorted(
        range(len(occurrences)),
        key=lambda i: (occurrences[i].candidate_start, occurrences[i].candidate_end),
    )

    def protected_intersects(lo: float, hi: float) -> bool:
        return any(
            _intervals_overlap(lo, hi, ps, pe) > EPSILON for ps, pe in protected
        )

    merged: list[tuple[float, float, list[int]]] = []
    first = order[0]
    cur_start = occurrences[first].candidate_start
    cur_end = occurrences[first].candidate_end
    cur_idx = [first]

    for i in order[1:]:
        occ = occurrences[i]
        union_start = min(cur_start, occ.candidate_start)
        union_end = max(cur_end, occ.candidate_end)
        near = occ.candidate_start <= cur_end + MERGE_GAP_SEC
        safe = not protected_intersects(union_start, union_end)

        if near and safe:
            cur_start, cur_end = union_start, union_end
            cur_idx.append(i)
        else:
            if occ.candidate_start < cur_end - EPSILON:
                raise UnsafeCutPlanError(
                    "Candidatos sobrepostos entraram em conflito com fala protegida."
                )
            merged.append((cur_start, cur_end, cur_idx))
            cur_start, cur_end, cur_idx = (
                occ.candidate_start,
                occ.candidate_end,
                [i],
            )

    merged.append((cur_start, cur_end, cur_idx))

    cuts: list[CutInterval] = []
    next_id = 1
    for start, end, idxs in merged:
        start = max(0.0, start)
        end = min(timeline_duration, end)
        if end - start <= EPSILON:
            continue
        cuts.append(
            CutInterval(
                id=next_id,
                start=start,
                end=end,
                occurrence_indexes=tuple(sorted(idxs)),
            )
        )
        next_id += 1

    return cuts


def _build_keeps(
    cuts: list[CutInterval], timeline_duration: float
) -> list[KeepInterval]:
    keeps: list[KeepInterval] = []
    cursor = 0.0
    for cut in cuts:
        if cut.start > cursor + EPSILON:
            keeps.append(KeepInterval(cursor, cut.start))
        cursor = max(cursor, cut.end)
    if cursor < timeline_duration - EPSILON:
        keeps.append(KeepInterval(cursor, timeline_duration))
    return keeps


# ===========================================================================
# PARTE 2 — TOOLCHAIN FFMPEG / FFPROBE (seções 14, 17, 18)
# ===========================================================================


def _startupinfo():
    if os.name != "nt":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return si


def _run_short(args: list[str], timeout: int = _SUBPROCESS_TIMEOUT_SHORT):
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
        startupinfo=_startupinfo(),
        shell=False,
    )


# --- 14.1/14.2 Descoberta e validação do toolchain ---


def _candidate_dirs(project_root: Path) -> list[Path]:
    dirs: list[Path] = []
    local = project_root / "tools" / "ffmpeg" / "bin"
    if local.is_dir():
        dirs.append(local)

    import shutil

    for name in ("ffmpeg", "ffprobe"):
        found = shutil.which(name)
        if found:
            parent = Path(found).resolve().parent
            if parent not in dirs:
                dirs.append(parent)
    return dirs


def resolve_toolchain(project_root: Path) -> Toolchain:
    """Descobre e valida um par ffmpeg/ffprobe do mesmo diretório (seção 14)."""

    project_root = Path(project_root)
    errors: list[str] = []

    for directory in _candidate_dirs(project_root):
        ffmpeg = directory / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        ffprobe = directory / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if not ffmpeg.is_file() or not ffprobe.is_file():
            continue
        try:
            toolchain = _validate_toolchain(ffmpeg, ffprobe)
        except CutterError as exc:
            errors.append(f"{directory}: {exc}")
            continue
        return toolchain

    raise CutterError(
        "FFmpeg/ffprobe não foi encontrado ou não possui suporte a H.264/AAC.\n"
        "Execute novamente o arquivo setup.bat. Nenhum arquivo original foi alterado."
    )


def _validate_toolchain(ffmpeg: Path, ffprobe: Path) -> Toolchain:
    ver = _run_short([str(ffmpeg), "-version"])
    if ver.returncode != 0:
        raise CutterError("ffmpeg -version falhou.")
    ffmpeg_version = ver.stdout.splitlines()[0].strip() if ver.stdout else ""

    pver = _run_short([str(ffprobe), "-version"])
    if pver.returncode != 0:
        raise CutterError("ffprobe -version falhou.")
    ffprobe_version = pver.stdout.splitlines()[0].strip() if pver.stdout else ""

    enc = _run_short([str(ffmpeg), "-hide_banner", "-encoders"])
    if enc.returncode != 0:
        raise CutterError("Não foi possível listar encoders do ffmpeg.")
    encoders_text = enc.stdout or ""
    if not _has_encoder(encoders_text, "libx264"):
        raise CutterError("Encoder libx264 ausente.")
    if not _has_encoder(encoders_text, "aac"):
        raise CutterError("Encoder aac ausente.")

    filter_option = _detect_filter_file_option(ffmpeg)

    return Toolchain(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        ffmpeg_version=ffmpeg_version,
        ffprobe_version=ffprobe_version,
        filter_file_option=filter_option,
    )


def _has_encoder(encoders_text: str, name: str) -> bool:
    for line in encoders_text.splitlines():
        # Linhas de encoder têm o formato " V..... libx264   descrição".
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


def _detect_filter_file_option(ffmpeg: Path) -> str:
    """Testa funcionalmente qual opção carrega filtergraph por arquivo.

    Cria um filtergraph mínimo e executa um comando lavfi de 0,1 s.
    """

    import tempfile

    graph = (
        "color=c=black:s=32x32:d=0.1[v];"
        "anullsrc=r=16000:cl=mono,atrim=end=0.1[a]"
    )

    for option in ("-/filter_complex", "-filter_complex_script"):
        tmpdir = tempfile.mkdtemp(prefix=".putzcleaner-filtertest-")
        graph_file = Path(tmpdir) / "graph.txt"
        try:
            graph_file.write_text(graph, encoding="utf-8")
            args = [
                str(ffmpeg),
                "-hide_banner",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=32x32:d=0.1",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=16000:cl=mono",
                option,
                str(graph_file),
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-t",
                "0.1",
                "-f",
                "null",
                "-",
            ]
            result = _run_short(args, timeout=_SUBPROCESS_TIMEOUT_SHORT)
            if result.returncode == 0:
                return option
        except Exception:  # noqa: BLE001
            pass
        finally:
            _safe_rmtree(graph_file.parent)

    raise CutterError(
        "Nenhuma forma de carregar filtergraph por arquivo funcionou."
    )


def _safe_rmtree(path: Path) -> None:
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


# --- 14.3 Probe do vídeo ---


def probe_media(toolchain: Toolchain, input_path: Path) -> MediaInfo:
    args = [
        str(toolchain.ffprobe),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(input_path),
    ]
    result = _run_short(args, timeout=30)
    if result.returncode != 0:
        raise CutterError(
            "Não foi possível inspecionar o vídeo. O arquivo pode estar corrompido."
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CutterError("A inspeção do vídeo retornou dados inválidos.") from exc

    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video_stream = None
    audio_stream = None
    for stream in streams:
        codec_type = stream.get("codec_type")
        disposition = stream.get("disposition", {}) or {}
        attached = bool(disposition.get("attached_pic", 0))
        if codec_type == "video" and video_stream is None and not attached:
            video_stream = _build_stream(stream, attached)
        elif codec_type == "audio" and audio_stream is None:
            audio_stream = _build_stream(stream, attached)

    if video_stream is None:
        raise CutterError("O arquivo não possui faixa de vídeo utilizável.")
    if audio_stream is None:
        raise CutterError("O arquivo não possui faixa de áudio; a transcrição exige áudio.")

    # Dimensões e fps do stream de vídeo.
    raw_video = _find_stream_dict(streams, video_stream.global_index)
    width = int(raw_video.get("width", 0) or 0)
    height = int(raw_video.get("height", 0) or 0)
    if width <= 0 or height <= 0:
        raise CutterError("O vídeo não possui dimensões válidas.")
    fps = _parse_fraction(raw_video.get("avg_frame_rate"))

    format_duration = _sanitize_float(fmt.get("duration"))
    format_start_time = _sanitize_float(fmt.get("start_time"))

    # Duração canônica: vídeo, senão format.
    timeline_duration = video_stream.duration
    if timeline_duration is None or timeline_duration <= 0:
        timeline_duration = format_duration
    if timeline_duration is None or timeline_duration <= 0:
        raise CutterError("Não foi possível determinar a duração do vídeo.")

    # Comparar offsets A/V.
    v_start = video_stream.start_time
    a_start = audio_stream.start_time
    if v_start is None:
        v_start = format_start_time
    if a_start is None:
        a_start = format_start_time
    if v_start is None and a_start is None:
        v_start = a_start = 0.0
    elif v_start is None:
        v_start = a_start
    elif a_start is None:
        a_start = v_start

    if abs(v_start - a_start) > _AV_OFFSET_TOLERANCE_SEC:
        raise CutterError(
            "O vídeo possui deslocamento incomum entre áudio e imagem e não pode "
            "ser processado com segurança nesta versão."
        )

    return MediaInfo(
        timeline_duration=float(timeline_duration),
        format_duration=format_duration,
        format_start_time=format_start_time,
        video_stream=video_stream,
        audio_stream=audio_stream,
        width=width,
        height=height,
        fps=fps,
    )


def _build_stream(stream: dict, attached: bool) -> MediaStream:
    return MediaStream(
        global_index=int(stream.get("index", 0) or 0),
        codec_type=str(stream.get("codec_type", "")),
        codec_name=str(stream.get("codec_name", "")),
        start_time=_sanitize_float(stream.get("start_time")),
        duration=_sanitize_float(stream.get("duration")),
        attached_picture=attached,
    )


def _find_stream_dict(streams: list[dict], global_index: int) -> dict:
    for stream in streams:
        if int(stream.get("index", -1)) == global_index:
            return stream
    return {}


def _parse_fraction(value: object) -> float | None:
    if not value or not isinstance(value, str) or "/" not in value:
        return None
    num_str, den_str = value.split("/", 1)
    num = _sanitize_float(num_str)
    den = _sanitize_float(den_str)
    if num is None or den is None or den == 0:
        return None
    result = num / den
    if result <= 0:
        return None
    return result


# --- 14.4 Extração do WAV canônico ---


def extract_canonical_audio(
    toolchain: Toolchain,
    input_path: Path,
    media_info: MediaInfo,
    dest_wav: Path,
    cancel_event: threading.Event,
    log_callback: Callable[[str], None],
) -> None:
    if cancel_event.is_set():
        raise RenderCancelled()

    dur = media_info.timeline_duration
    audio_index = media_info.audio_stream.global_index
    af = (
        f"aresample=16000:async=1:first_pts=0,apad,"
        f"atrim=start=0:end={dur:.6f},asetpts=PTS-STARTPTS"
    )
    args = [
        str(toolchain.ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-n",
        "-i",
        str(input_path),
        "-map",
        f"0:{audio_index}",
        "-vn",
        "-af",
        af,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-nostats",
        str(dest_wav),
    ]
    log_callback("Extraindo áudio canônico para transcrição.")
    result = _run_short(args, timeout=max(60, int(dur) + 60))
    if result.returncode != 0:
        raise CutterError(
            "Falha ao extrair o áudio do vídeo. O arquivo pode estar corrompido."
        )

    # Validar o WAV com ffprobe.
    probe = _run_short(
        [
            str(toolchain.ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            str(dest_wav),
        ],
        timeout=15,
    )
    if probe.returncode != 0:
        raise CutterError("O áudio extraído não pôde ser validado.")
    try:
        wav_dur = _sanitize_float(json.loads(probe.stdout).get("format", {}).get("duration"))
    except json.JSONDecodeError:
        wav_dur = None
    if wav_dur is None or abs(wav_dur - dur) > _DURATION_MATCH_TOLERANCE_SEC:
        raise CutterError(
            "A duração do áudio extraído diverge da timeline do vídeo."
        )


# --- 17 Renderização ---


def _fmt(value: float) -> str:
    return f"{value:.6f}"


def _build_filtergraph(
    video_index: int,
    audio_index: int,
    keeps: Sequence[KeepInterval],
    timeline_duration: float,
    audio_fade_sec: float = DEFAULT_AUDIO_FADE_SEC,
) -> str:
    """Constrói o filtergraph para 1..MAX_KEEPS_PER_GRAPH keeps (seção 17.2)."""

    k = len(keeps)
    if k == 0:
        raise CutterError("Não há segmentos preservados para renderizar.")

    audio_norm = (
        f"aresample=async=1:first_pts=0,apad,"
        f"atrim=start=0:end={_fmt(timeline_duration)},asetpts=PTS-STARTPTS"
    )

    if k == 1:
        keep = keeps[0]
        s, e = _fmt(keep.start), _fmt(keep.end)
        audio_filters = _audio_segment_filters(keep, audio_fade_sec)
        parts = [
            f"[0:{video_index}]setpts=PTS-STARTPTS,trim=start={s}:end={e},"
            f"setpts=PTS-STARTPTS,pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p[vout]",
            f"[0:{audio_index}]{audio_norm},atrim=start={s}:end={e},"
            f"asetpts=PTS-STARTPTS,{audio_filters}[aout]",
        ]
        return ";\n".join(parts)

    parts: list[str] = []
    vsrc = "".join(f"[vsrc{i}]" for i in range(k))
    asrc = "".join(f"[asrc{i}]" for i in range(k))
    parts.append(f"[0:{video_index}]setpts=PTS-STARTPTS,split=outputs={k}{vsrc}")
    parts.append(f"[0:{audio_index}]{audio_norm},asplit=outputs={k}{asrc}")

    for i, keep in enumerate(keeps):
        s, e = _fmt(keep.start), _fmt(keep.end)
        audio_filters = _audio_segment_filters(keep, audio_fade_sec)
        parts.append(
            f"[vsrc{i}]trim=start={s}:end={e},setpts=PTS-STARTPTS[v{i}]"
        )
        parts.append(
            f"[asrc{i}]atrim=start={s}:end={e},asetpts=PTS-STARTPTS,{audio_filters}[a{i}]"
        )

    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(k))
    parts.append(f"{concat_inputs}concat=n={k}:v=1:a=1[vcat][acat]")
    parts.append("[vcat]pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p[vout]")
    parts.append("[acat]anull[aout]")

    return ";\n".join(parts)


def _audio_segment_filters(keep: KeepInterval, audio_fade_sec: float) -> str:
    duration = keep.end - keep.start
    fade = min(audio_fade_sec, max(0.0, duration / 2.0 - EPSILON))
    if fade <= 0:
        return "anull"
    return (
        f"afade=t=in:st=0:d={_fmt(fade)},"
        f"afade=t=out:st={_fmt(duration - fade)}:d={_fmt(fade)}"
    )


def render_video(
    toolchain: Toolchain,
    media_info: MediaInfo,
    input_path: Path,
    plan: CutPlan,
    staged_video: Path,
    work_dir: Path,
    cancel_event: threading.Event,
    log_callback: Callable[[str], None],
    progress_callback: Callable[[float], None],
) -> RenderResult:
    """Renderiza o MP4 final (rota única ou em lotes) e o verifica."""

    keeps = plan.keeps
    if len(keeps) <= MAX_KEEPS_PER_GRAPH:
        _render_single_graph(
            toolchain,
            media_info,
            input_path,
            keeps,
            staged_video,
            work_dir,
            plan.expected_output_duration,
            cancel_event,
            log_callback,
            progress_callback,
        )
    else:
        _render_batches(
            toolchain,
            media_info,
            input_path,
            keeps,
            staged_video,
            work_dir,
            plan.expected_output_duration,
            cancel_event,
            log_callback,
            progress_callback,
        )

    return verify_output(toolchain, staged_video, plan.expected_output_duration, media_info)


def _render_single_graph(
    toolchain,
    media_info,
    input_path,
    keeps,
    staged_video,
    work_dir,
    expected_duration,
    cancel_event,
    log_callback,
    progress_callback,
):
    graph = _build_filtergraph(
        media_info.video_stream.global_index,
        media_info.audio_stream.global_index,
        keeps,
        media_info.timeline_duration,
    )
    graph_file = Path(work_dir) / f"filtergraph-{uuid.uuid4().hex}.txt"
    graph_file.write_text(graph, encoding="utf-8")

    args = [
        str(toolchain.ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-n",
        "-i",
        str(input_path),
        toolchain.filter_file_option,
        str(graph_file),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-abort_on",
        "empty_output",
        "-progress",
        "pipe:1",
        "-nostats",
        str(staged_video),
    ]

    log_callback(f"Renderizando {len(keeps)} segmento(s) preservado(s).")
    _run_ffmpeg_with_progress(
        args,
        expected_duration,
        0.0,
        1.0,
        cancel_event,
        progress_callback,
        cwd=None,
    )


# --- 17.3 Rota em lotes ---


def _render_batches(
    toolchain,
    media_info,
    input_path,
    keeps,
    staged_video,
    work_dir,
    expected_duration,
    cancel_event,
    log_callback,
    progress_callback,
):
    batches: list[list[KeepInterval]] = [
        list(keeps[i : i + MAX_KEEPS_PER_GRAPH])
        for i in range(0, len(keeps), MAX_KEEPS_PER_GRAPH)
    ]
    batch_dir = Path(work_dir) / f"batches-{uuid.uuid4().hex}"
    batch_dir.mkdir(parents=True, exist_ok=False)

    log_callback(f"Renderizando em {len(batches)} lote(s) para vídeo longo.")

    batch_durations = [
        sum(k.end - k.start for k in batch) for batch in batches
    ]
    total_batch_duration = sum(batch_durations)
    # Reservar a parte final (10%) da faixa para a concatenação.
    render_span = 0.90
    done_duration = 0.0

    batch_files: list[str] = []
    for bi, batch in enumerate(batches):
        if cancel_event.is_set():
            raise RenderCancelled()
        batch_start = batch[0].start
        batch_end = batch[-1].end
        window = batch_end - batch_start
        rel_keeps = [
            KeepInterval(k.start - batch_start, k.end - batch_start) for k in batch
        ]
        graph = _build_filtergraph(
            media_info.video_stream.global_index,
            media_info.audio_stream.global_index,
            rel_keeps,
            window,
        )
        graph_file = batch_dir / f"graph_{bi:04d}.txt"
        graph_file.write_text(graph, encoding="utf-8")
        batch_out = batch_dir / f"batch_{bi:04d}.mkv"

        args = [
            str(toolchain.ffmpeg),
            "-hide_banner",
            "-nostdin",
            "-n",
            "-ss",
            _fmt(batch_start),
            "-t",
            _fmt(window),
            "-i",
            str(input_path),
            toolchain.filter_file_option,
            str(graph_file),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "flac",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-abort_on",
            "empty_output",
            "-progress",
            "pipe:1",
            "-nostats",
            str(batch_out),
        ]

        span = (
            (batch_durations[bi] / total_batch_duration) * render_span
            if total_batch_duration > 0
            else 0.0
        )
        base = (
            (done_duration / total_batch_duration) * render_span
            if total_batch_duration > 0
            else 0.0
        )
        _run_ffmpeg_with_progress(
            args,
            batch_durations[bi],
            base,
            span,
            cancel_event,
            progress_callback,
            cwd=None,
        )
        _verify_intermediate(toolchain, batch_out)
        done_duration += batch_durations[bi]
        batch_files.append(batch_out.name)

    # Manifesto de concatenação.
    manifest = batch_dir / "batches.txt"
    manifest.write_text(
        "".join(f"file '{name}'\n" for name in batch_files), encoding="ascii"
    )

    concat_args = [
        str(toolchain.ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-n",
        "-f",
        "concat",
        "-safe",
        "1",
        "-i",
        "batches.txt",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-progress",
        "pipe:1",
        "-nostats",
        str(staged_video),
    ]
    log_callback("Concatenando lotes no arquivo final.")
    _run_ffmpeg_with_progress(
        concat_args,
        expected_duration,
        render_span,
        1.0 - render_span,
        cancel_event,
        progress_callback,
        cwd=str(batch_dir),
    )
    # A limpeza do batch_dir é responsabilidade do dono do work_dir (finally).


def _verify_intermediate(toolchain: Toolchain, path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise CutterError("Um lote intermediário não foi gerado corretamente.")
    probe = _run_short(
        [
            str(toolchain.ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            str(path),
        ],
        timeout=15,
    )
    if probe.returncode != 0:
        raise CutterError("Um lote intermediário não pôde ser validado.")


# --- 17.4 Progresso do FFmpeg ---


def _run_ffmpeg_with_progress(
    args: list[str],
    expected_duration: float,
    progress_base: float,
    progress_span: float,
    cancel_event: threading.Event,
    progress_callback: Callable[[float], None],
    cwd: str | None,
) -> None:
    if cancel_event.is_set():
        raise RenderCancelled()

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
        startupinfo=_startupinfo(),
        cwd=cwd,
        shell=False,
        bufsize=1,
    )

    stderr_tail: list[str] = []

    def _drain_stderr():
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_tail.append(line.rstrip("\n"))
            if len(stderr_tail) > 100:
                del stderr_tail[0]

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    ended_ok = False
    last_progress = progress_base
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel_event.is_set():
                _terminate_process(proc)
                raise RenderCancelled()
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out_time_sec = _parse_progress_time(key, value)
            if out_time_sec is not None and expected_duration > 0:
                frac = max(0.0, min(1.0, out_time_sec / expected_duration))
                mapped = progress_base + frac * progress_span
                if mapped > last_progress:
                    last_progress = mapped
                    progress_callback(mapped)
            elif key == "progress" and value == "end":
                ended_ok = True
    finally:
        proc.wait()
        stderr_thread.join(timeout=2)

    if cancel_event.is_set():
        raise RenderCancelled()

    if proc.returncode != 0:
        tail = "\n".join(stderr_tail[-20:])
        raise CutterError(
            "A renderização com FFmpeg falhou.\n" + tail
        )


def _parse_progress_time(key: str, value: str) -> float | None:
    # out_time_us e o legado out_time_ms são ambos microssegundos.
    if key in ("out_time_us", "out_time_ms"):
        micro = _sanitize_float(value)
        if micro is None:
            return None
        return micro / 1_000_000.0
    if key == "out_time":
        return _parse_hhmmss(value)
    return None


def _parse_hhmmss(value: str) -> float | None:
    try:
        parts = value.split(":")
        if len(parts) != 3:
            return None
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    except (ValueError, IndexError):
        return None


# --- 17.5 Cancelamento do subprocesso ---


def _terminate_process(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


# --- 18.4 Verificação antes de publicar ---


def verify_output(
    toolchain: Toolchain,
    path: Path,
    expected_duration: float,
    media_info: MediaInfo,
) -> RenderResult:
    if not path.is_file() or path.stat().st_size == 0:
        raise CutterError("O vídeo final não foi gerado.")

    probe = _run_short(
        [
            str(toolchain.ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=30,
    )
    if probe.returncode != 0:
        raise CutterError("O vídeo final não pôde ser validado.")
    try:
        data = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise CutterError("A validação do vídeo final retornou dados inválidos.") from exc

    video_codec = ""
    audio_codec = ""
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not video_codec:
            video_codec = str(stream.get("codec_name", ""))
        elif stream.get("codec_type") == "audio" and not audio_codec:
            audio_codec = str(stream.get("codec_name", ""))

    if video_codec != "h264":
        raise CutterError(f"O vídeo final não é H.264 (obtido: {video_codec}).")
    if audio_codec != "aac":
        raise CutterError(f"O áudio final não é AAC (obtido: {audio_codec}).")

    actual = _sanitize_float(data.get("format", {}).get("duration"))
    if actual is None or actual <= 0:
        raise CutterError("O vídeo final não possui duração válida.")

    fps = media_info.fps or 30.0
    tolerance = max(0.5, 2.0 / fps)
    if abs(actual - expected_duration) > tolerance:
        raise CutterError(
            "A duração do vídeo final diverge do esperado "
            f"(esperado {expected_duration:.3f}s, obtido {actual:.3f}s)."
        )

    return RenderResult(
        staged_video=path,
        actual_duration=actual,
        video_codec=video_codec,
        audio_codec=audio_codec,
    )


# --- 18.1 Nomes de saída ---


def compute_output_paths(
    input_path: Path, output_dir: Path
) -> tuple[Path, Path, Path]:
    stem = input_path.stem
    video = output_dir / f"{stem}_limpo.mp4"
    report = output_dir / f"{stem}_limpo_relatorio.json"
    transcript = output_dir / f"{stem}_limpo_transcricao.txt"
    return video, report, transcript

