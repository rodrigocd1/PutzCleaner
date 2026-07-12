"""Interface Tkinter e orquestração do PutzCleaner (seções 9, 18, 20).

A thread principal cuida da GUI; um worker (``threading.Thread``) executa o
fluxo de domínio e comunica-se por uma ``queue.Queue`` de eventos. O worker
nunca toca widgets, ``StringVar`` ou ``messagebox``.
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
import tkinter as tk
import uuid
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from . import cutter
from . import report as report_mod
from . import transcript as transcript_mod
from .transcription_cache import CacheKey, TranscriptionCache
from .audio_analysis import analyze_wav
from .cutter import (
    CutterError,
    RenderCancelled,
    build_cut_plan,
    compute_output_paths,
    extract_canonical_audio,
    probe_media,
    render_video,
    resolve_toolchain,
)
from .transcriber import (
    DEVICE_CHOICES,
    MODEL_MAP,
    TermValidationError,
    Transcriber,
    TranscriptionCancelled,
    TranscriptionError,
    validate_terms,
)

MAX_MARGIN_SEC = 2.0
_LOG_MAX_LINES = 1000

_CONFIDENCE_HELP_TEXT = (
    "Confiança mínima — como funciona\n\n"
    "Cada palavra reconhecida recebe uma confiança de 0 a 1 (o quanto o modelo "
    "tem certeza do que ouviu). O PutzCleaner só remove um vício de fala se a "
    "confiança dele for maior ou igual a este valor.\n\n"
    "Quanto MENOR o valor, mais palavras são removidas (inclusive as duvidosas). "
    "Quanto MAIOR, mais seguro contra cortes indevidos.\n\n"
    "Exemplos de valores:\n"
    "• 0,60  — Seguro (padrão). Corta pouco por engano, mas pode deixar passar "
    "\"né\"/\"hum\" ditos muito rápido.\n"
    "• 0,40  — Equilibrado. Remove também vícios reconhecidos com menos certeza. "
    "Boa escolha para a maioria das entrevistas.\n"
    "• 0,20  — Agressivo. Pega quase tudo, até o que o modelo mal reconheceu. "
    "Maior risco de cortar fala legítima.\n\n"
    "Como calibrar: se um vício aparece na transcrição (.txt) SEM a marca "
    "[removida], veja a confiança exata dele na seção \"ignorados\" do relatório "
    "(.json) e ajuste este valor para logo abaixo dela."
)

_MARGIN_BEFORE_HELP_TEXT = (
    "Margem antes — como funciona\n\n"
    "Define quanto tempo antes do vício de fala o corte pode avançar.\n\n"
    "Valores maiores podem soar mais naturais em algumas pausas, mas também "
    "aumentam o risco de aproximar demais o corte da fala útil.\n\n"
    f"O valor recomendado é até {MAX_MARGIN_SEC:.0f}s. Acima disso, o programa "
    "permite continuar, mas registra um aviso no log para revisão."
)

_MARGIN_AFTER_HELP_TEXT = (
    "Margem depois — como funciona\n\n"
    "Define quanto tempo depois do vício de fala o corte pode avançar.\n\n"
    "Valores maiores ajudam quando o vício emenda numa pausa, mas também podem "
    "engolir mais contexto do que o necessário.\n\n"
    f"O valor recomendado é até {MAX_MARGIN_SEC:.0f}s. Acima disso, o programa "
    "permite continuar, mas registra um aviso no log para revisão."
)

PRESET_DEFAULT = "Equilibrado"
PRESET_CUSTOM = "Personalizado"
PRESET_CONFIGS: dict[str, dict[str, str]] = {
    "Conservador": {
        "confidence": "0.60",
        "margin_before": "0.03",
        "margin_after": "0.05",
    },
    PRESET_DEFAULT: {
        "confidence": "0.40",
        "margin_before": "0.05",
        "margin_after": "0.08",
    },
    "Agressivo": {
        "confidence": "0.20",
        "margin_before": "0.08",
        "margin_after": "0.12",
    },
}


class Tooltip:
    """Dica flutuante simples exibida ao passar o mouse sobre um widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event: object = None) -> None:
        if self._tip is not None:
            return
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 6
        tip = tk.Toplevel(self._widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tip,
            text=self._text,
            justify="left",
            background="#ffffe0",
            foreground="#000000",
            relief="solid",
            borderwidth=1,
            wraplength=460,
            padx=8,
            pady=6,
        )
        label.pack()
        self._tip = tip

    def _hide(self, _event: object = None) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

DEFAULT_CONFIG: dict[str, Any] = {
    "palavras_removidas": [
        "né", "neh", "eee", "ééé", "ã", "hã", "hum", "tipo", "assim",
    ],
    "modelo_padrao": "small",
    "dispositivo": "auto",
    "margem_antes": 0.05,
    "margem_depois": 0.08,
    "limiar_confianca": 0.60,
    "pasta_saida": "",
    "modo_preset": PRESET_DEFAULT,
    "usar_encoder_gpu": False,
}


# ---------------------------------------------------------------------------
# ProcessingOptions (seção 11.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessingOptions:
    input_video: Path
    output_directory: Path
    terms: tuple[str, ...]
    model: str
    device: str
    margin_before: float
    margin_after: float
    min_probability: float
    preset_name: str = PRESET_DEFAULT
    use_gpu_encoder: bool = False
    analyze_only: bool = False


# ---------------------------------------------------------------------------
# Configuração (seção 9)
# ---------------------------------------------------------------------------


def _config_path(project_root: Path) -> Path:
    return project_root / "config.json"


def load_config(project_root: Path) -> tuple[dict[str, Any], list[str], bool]:
    """Carrega config.json aplicando padrões. Retorna (config, avisos, corrompido)."""

    import json

    path = _config_path(project_root)
    warnings: list[str] = []
    corrupt = False
    config = dict(DEFAULT_CONFIG)

    if not path.is_file():
        warnings.append("config.json não encontrado; usando padrões.")
        return config, warnings, corrupt

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        warnings.append(f"config.json inválido ({exc}); usando padrões em memória.")
        return config, warnings, True

    if not isinstance(data, dict):
        warnings.append("config.json não é um objeto; usando padrões.")
        return config, warnings, True

    # palavras_removidas
    palavras = data.get("palavras_removidas")
    if isinstance(palavras, list) and palavras:
        try:
            config["palavras_removidas"] = list(validate_terms(str(p) for p in palavras))
        except TermValidationError as exc:
            warnings.append(f"palavras_removidas inválida ({exc}); usando padrão.")
    else:
        warnings.append("palavras_removidas ausente/vazia; usando padrão.")

    # modelo_padrao
    modelo = data.get("modelo_padrao")
    if modelo in MODEL_MAP:
        config["modelo_padrao"] = modelo
    else:
        warnings.append("modelo_padrao inválido; usando 'small'.")

    # dispositivo
    dispositivo = data.get("dispositivo")
    if dispositivo in DEVICE_CHOICES:
        config["dispositivo"] = dispositivo
    else:
        warnings.append("dispositivo inválido; usando 'auto'.")

    # margens
    for chave, padrao in (("margem_antes", 0.05), ("margem_depois", 0.08)):
        valor = data.get(chave)
        try:
            fvalor = float(valor)
            if fvalor < 0.0:
                raise ValueError
            config[chave] = fvalor
        except (TypeError, ValueError):
            warnings.append(f"{chave} inválida; usando {padrao}.")

    # limiar de confiança
    limiar = data.get("limiar_confianca")
    try:
        flimiar = float(limiar)
        if not (0.0 <= flimiar <= 1.0):
            raise ValueError
        config["limiar_confianca"] = flimiar
    except (TypeError, ValueError):
        warnings.append("limiar_confianca inválido; usando 0.60.")

    preset = data.get("modo_preset")
    if isinstance(preset, str) and preset in {*PRESET_CONFIGS.keys(), PRESET_CUSTOM}:
        config["modo_preset"] = preset
    else:
        config["modo_preset"] = PRESET_DEFAULT

    config["usar_encoder_gpu"] = bool(data.get("usar_encoder_gpu", False))

    # pasta_saida
    pasta = data.get("pasta_saida", "")
    if isinstance(pasta, str):
        config["pasta_saida"] = pasta
    else:
        warnings.append("pasta_saida inválida; usando pasta do vídeo.")

    return config, warnings, corrupt


def save_config(project_root: Path, config: dict[str, Any]) -> None:
    """Escreve config.json de forma atômica (seção 9, regras de escrita)."""

    import json

    payload = {
        "palavras_removidas": list(config["palavras_removidas"]),
        "modelo_padrao": config["modelo_padrao"],
        "dispositivo": config.get("dispositivo", "auto"),
        "margem_antes": float(config["margem_antes"]),
        "margem_depois": float(config["margem_depois"]),
        "limiar_confianca": float(config.get("limiar_confianca", 0.60)),
        "modo_preset": config.get("modo_preset", PRESET_DEFAULT),
        "usar_encoder_gpu": bool(config.get("usar_encoder_gpu", False)),
        "pasta_saida": config.get("pasta_saida", ""),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)

    path = _config_path(project_root)
    tmp = path.parent / f".config-{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Helpers de validação instantânea
# ---------------------------------------------------------------------------


def parse_decimal(text: str) -> float:
    """Converte '0.05' ou '0,05' em float, rejeitando texto misto/NaN/inf."""

    import math

    text = text.strip()
    if text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    value = float(text)  # ValueError se inválido
    if not math.isfinite(value):
        raise ValueError("valor não finito")
    return value


# ---------------------------------------------------------------------------
# Worker (thread de domínio) — seção 7 e 20.4
# ---------------------------------------------------------------------------


def _faster_whisper_version() -> str:
    try:
        from importlib.metadata import version

        return version("faster-whisper")
    except Exception:  # noqa: BLE001
        return "desconhecida"


def run_worker(
    options: ProcessingOptions,
    project_root: Path,
    transcriber: Transcriber,
    cancel_event: threading.Event,
    emit: Callable[..., None],
) -> None:
    """Executa o fluxo completo de domínio e emite eventos estruturados."""

    work_dir: str | None = None
    staged_video: Path | None = None
    staged_report: Path | None = None
    staged_transcript: Path | None = None
    published_video = False
    published_report = False
    published_transcript = False
    cache = TranscriptionCache(project_root / "models" / ".transcription_cache")
    cache_hit = False

    def log(msg: str) -> None:
        emit("log", msg)

    try:
        # ---- VALIDATING (0-5%) ----
        emit("status", "Validando entrada e ferramentas...")
        emit("progress_mode", "determinate")
        emit("progress", 1.0)

        input_path = options.input_video
        if not input_path.is_file():
            raise CutterError("O vídeo selecionado não existe.")
        if input_path.stat().st_size == 0:
            raise CutterError("O vídeo selecionado está vazio.")

        output_dir = options.output_directory
        if not output_dir.is_dir():
            raise CutterError("A pasta de saída não existe.")

        # Teste de escrita na pasta de saída.
        test_file = output_dir / f".putzcleaner-write-{uuid.uuid4().hex}.tmp"
        try:
            test_file.write_text("ok", encoding="ascii")
            test_file.unlink()
        except OSError as exc:
            raise CutterError("Sem permissão de escrita na pasta de saída.") from exc

        final_video, final_report, final_transcript = compute_output_paths(
            input_path, output_dir
        )

        # Entrada nunca pode ser igual à saída (case-insensitive no Windows).
        if os.path.normcase(str(input_path.resolve())) == os.path.normcase(
            str(final_video.resolve())
        ):
            raise CutterError("O vídeo de saída não pode ser igual ao de entrada.")

        if options.analyze_only:
            _check_collision(final_report, final_transcript)
        else:
            _check_collision(final_video, final_report, final_transcript)

        emit("progress", 3.0)
        toolchain = resolve_toolchain(project_root)
        log(f"FFmpeg: {toolchain.ffmpeg_version}")
        log(
            "NVENC disponível." if toolchain.nvenc_available
            else "NVENC indisponível; render em CPU/libx264."
        )

        media_info = probe_media(toolchain, input_path)
        log(
            f"Vídeo {media_info.width}x{media_info.height}, "
            f"duração {media_info.timeline_duration:.2f}s."
        )
        emit("progress", 5.0)

        if cancel_event.is_set():
            raise RenderCancelled()

        # ---- work dir e staging ----
        work_dir = tempfile.mkdtemp(prefix=".putzcleaner-", dir=str(output_dir))
        wav_path = Path(work_dir) / "audio_canonico.wav"
        staged_video = output_dir / f".putzcleaner-{uuid.uuid4().hex}.mp4"
        staged_report = output_dir / f".putzcleaner-{uuid.uuid4().hex}.json"
        staged_transcript = output_dir / f".putzcleaner-{uuid.uuid4().hex}.txt"

        # ---- EXTRACTING_AUDIO (5-10%) ----
        emit("status", "Extraindo áudio canônico...")
        extract_canonical_audio(
            toolchain, input_path, media_info, wav_path, cancel_event, log
        )
        emit("progress", 10.0)

        if cancel_event.is_set():
            raise RenderCancelled()

        # ---- LOADING_MODEL / TRANSCRIBING (10-60%) ----
        emit("status", f"Transcrevendo com o modelo {options.model}...")
        emit("progress_mode", "indeterminate")

        def transcribe_progress(frac: float) -> None:
            # Transcrição ocupa a faixa 18-60%.
            emit("progress_mode", "determinate")
            emit("progress", 18.0 + max(0.0, min(1.0, frac)) * 42.0)

        cache_key = CacheKey.build(
            input_path=input_path,
            model_requested=options.model,
            timeline_duration=media_info.timeline_duration,
        )
        result = cache.load(cache_key)
        if result is None:
            log("Cache de transcricao: miss.")
            result = transcriber.transcribe(
                wav_path,
                media_info.timeline_duration,
                options.model,
                options.device,
                cancel_event,
                log,
                transcribe_progress,
            )
            cache.save(cache_key, result)
        else:
            cache_hit = True
            log("Cache de transcricao: hit.")
            emit("progress_mode", "determinate")
            emit("progress", 60.0)
        emit("progress_mode", "determinate")
        emit("progress", 60.0)

        if cancel_event.is_set():
            raise RenderCancelled()

        # ---- PLANNING (60-65%) ----
        emit("status", "Calculando cortes...")
        audio_profile = None
        try:
            audio_profile = analyze_wav(wav_path)
            log(
                f"Analise de audio: {len(audio_profile.silence_spans)} trecho(s) de silencio detectados."
            )
        except Exception as exc:  # noqa: BLE001
            log(f"Analise de audio indisponivel; usando margens fixas. Detalhe: {exc}")
        plan = build_cut_plan(
            result.words,
            options.terms,
            media_info.timeline_duration,
            options.margin_before,
            options.margin_after,
            options.min_probability,
            audio_profile=audio_profile,
        )
        log(
            f"{len(plan.occurrences)} ocorrência(s) aceita(s), "
            f"{len(plan.cuts)} corte(s), {len(plan.ignored)} ignorada(s)."
        )
        emit("progress", 65.0)

        if cancel_event.is_set():
            raise RenderCancelled()

        if options.analyze_only:
            emit("status", "Analise concluida. Gerando relatorio...")
            render_result = cutter.RenderResult(
                staged_video=final_video,
                actual_duration=plan.expected_output_duration,
                video_codec="analise",
                audio_codec="analise",
                encoder_used="analise",
                render_mode="analise",
            )
            emit("progress", 97.0)
        else:
            # ---- RENDERING / VERIFYING (65-97%) ----
            emit("status", "Renderizando o vídeo limpo...")

            def render_progress(frac: float) -> None:
                emit("progress", 65.0 + max(0.0, min(1.0, frac)) * 32.0)

            render_result = render_video(
                toolchain,
                media_info,
                input_path,
                plan,
                staged_video,
                Path(work_dir),
                options.use_gpu_encoder,
                cancel_event,
                log,
                render_progress,
            )
            emit("progress", 97.0)

        # ---- REPORTING (97-99%) ----
        emit("status", "Gerando relatório...")
        payload = report_mod.build_report(
            input_path=input_path,
            output_path=final_video,
            media_info=media_info,
            plan=plan,
            render=render_result,
            configured_terms=options.terms,
            model_requested=result.model_requested,
            model_resolved=result.model_resolved,
            device_requested=result.device_requested,
            device_used=result.device_used,
            margin_before=options.margin_before,
            margin_after=options.margin_after,
            min_probability=options.min_probability,
            preset_name=options.preset_name,
            analyze_only=options.analyze_only,
            silence_detection_used=audio_profile is not None,
            cache_hit=cache_hit,
            faster_whisper_version=_faster_whisper_version(),
            ffmpeg_version=toolchain.ffmpeg_version,
        )
        report_mod.write_report_staged(staged_report, payload)

        # Transcrição legível com tempos e marcação [removida].
        transcript_text = transcript_mod.build_transcript(
            result.words,
            plan.occurrences,
            input_name=str(input_path),
            model_label=f"{result.model_requested} ({result.model_resolved})",
            device_label=result.device_used,
        )
        transcript_mod.write_transcript_staged(staged_transcript, transcript_text)
        emit("progress", 99.0)

        # ---- Publicação sem sobrescrita (seção 18.5) ----
        # Ordem: vídeo, transcrição e por fim o relatório (marcador de commit).
        published_paths: list[Path] = []
        if not options.analyze_only:
            _check_collision(final_video, final_report, final_transcript)
            _publish_no_overwrite(staged_video, final_video)
            published_video = True
            staged_video = None
            published_paths.append(final_video)
        else:
            _check_collision(final_report, final_transcript)
        try:
            _publish_no_overwrite(staged_transcript, final_transcript)
            published_transcript = True
            staged_transcript = None
            published_paths.append(final_transcript)

            _publish_no_overwrite(staged_report, final_report)
            published_report = True
            staged_report = None
        except OSError as exc:
            # Rollback: remover somente os arquivos que esta execução publicou.
            orphans: list[str] = []
            for path in published_paths:
                try:
                    path.unlink()
                except OSError:
                    orphans.append(str(path))
            if orphans:
                emit(
                    "error",
                    "Falha ao publicar a saída e alguns arquivos ficaram órfãos.",
                    f"Órfãos: {', '.join(orphans)}. Detalhe: {exc}",
                )
                return
            raise CutterError("Falha ao publicar a saída.") from exc

        emit("progress", 100.0)
        emit("status", "Concluído.")
        if options.analyze_only:
            emit(
                "analysis_success",
                str(final_report),
                str(final_transcript),
                len(plan.occurrences),
                len(plan.cuts),
                round(sum(cut.end - cut.start for cut in plan.cuts), 3),
            )
        else:
            emit(
                "success",
                str(final_video),
                str(final_report),
                len(plan.occurrences),
                len(plan.cuts),
            )
        log(f"Transcrição: {final_transcript}")

    except (TranscriptionCancelled, RenderCancelled):
        emit("status", "Cancelado.")
        emit("log", "Processamento cancelado pelo usuário.")
    except (CutterError, TranscriptionError, TermValidationError) as exc:
        emit("error", str(exc), f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        emit(
            "error",
            "Ocorreu um erro inesperado durante o processamento.",
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        # Limpeza de staged não publicados e do work dir.
        for staged, published in (
            (staged_video, published_video),
            (staged_report, published_report),
            (staged_transcript, published_transcript),
        ):
            if staged is not None and not published:
                try:
                    if staged.exists():
                        staged.unlink()
                except OSError as exc:
                    emit("log", f"Falha ao limpar temporário {staged}: {exc}")
        if work_dir is not None:
            import shutil

            try:
                shutil.rmtree(work_dir, ignore_errors=False)
            except OSError as exc:
                emit("log", f"Falha ao remover diretório temporário {work_dir}: {exc}")
        emit("done")


def _check_collision(*final_paths: Path) -> None:
    if any(path.exists() for path in final_paths):
        raise CutterError(
            "Já existe um arquivo de saída com esse nome. Nada foi sobrescrito.\n"
            "Renomeie/mova o arquivo existente ou escolha outra pasta de saída."
        )


def _publish_no_overwrite(staged: Path, final: Path) -> None:
    """Publica com os.rename, que falha se o destino já existir (Windows)."""

    if final.exists():
        raise CutterError(
            "Já existe um arquivo de saída com esse nome. Nada foi sobrescrito."
        )
    os.rename(staged, final)


# ---------------------------------------------------------------------------
# Aplicação GUI (seção 20)
# ---------------------------------------------------------------------------


class PutzCleanerApp:
    def __init__(self, root: tk.Tk, project_root: Path) -> None:
        self.root = root
        self.project_root = project_root
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_event: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.transcriber = Transcriber(project_root / "models")
        self._closing_after_cancel = False
        self._applying_preset = False
        self._last_video_path: str | None = None
        self._last_report_path: str | None = None
        self._last_output_dir: str | None = None

        self.config, warnings, self.config_corrupt = load_config(project_root)
        self._configure_styles()

        root.title("PutzCleaner")
        root.geometry("900x720")
        root.minsize(760, 600)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._apply_config_to_widgets()

        for warning in warnings:
            self._append_log(f"Aviso: {warning}")
        if self.config_corrupt:
            self._append_log(
                "config.json estava inválido; padrões carregados em memória."
            )

        self.root.after(100, self._drain_events)

    # ---- Construção da tela (seção 20.2) ----

    def _build_ui(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)

        frame = ttk.Frame(root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        r = 0
        header = ttk.Frame(frame, style="Panel.TFrame", padding=16)
        header.grid(row=r, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        title = ttk.Label(header, text="PutzCleaner", style="HeroTitle.TLabel")
        title.grid(row=0, column=0, sticky="w")
        self.status_badge = ttk.Label(header, text="Pronto", style="StatusReady.TLabel")
        self.status_badge.grid(row=0, column=1, sticky="e")
        r += 1
        subtitle = ttk.Label(
            header, text="Removedor automático de vícios de fala para entrevistas", style="Subtitle.TLabel"
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(6, 0))
        r += 1

        # Linha do vídeo
        video_frame = ttk.Frame(frame, style="Panel.TFrame", padding=12)
        video_frame.grid(row=r, column=0, sticky="ew", pady=4)
        video_frame.columnconfigure(1, weight=1)
        ttk.Button(
            video_frame, text="Selecionar vídeo", command=self._choose_video, style="Primary.TButton"
        ).grid(row=0, column=0, padx=(0, 8))
        self.video_var = tk.StringVar()
        ttk.Entry(video_frame, textvariable=self.video_var, state="readonly").grid(
            row=0, column=1, sticky="ew"
        )
        r += 1

        # Linha de saída
        out_frame = ttk.Frame(frame, style="Panel.TFrame", padding=12)
        out_frame.grid(row=r, column=0, sticky="ew", pady=4)
        out_frame.columnconfigure(0, weight=1)
        self.output_var = tk.StringVar(value="Mesma pasta do vídeo")
        ttk.Entry(out_frame, textvariable=self.output_var, state="readonly").grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(
            out_frame, text="Escolher pasta", command=self._choose_output
        ).grid(row=0, column=1)
        r += 1

        # LabelFrame da lista
        list_frame = ttk.LabelFrame(frame, text="Palavras/sons a remover", padding=12)
        list_frame.grid(row=r, column=0, sticky="nsew", pady=6)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)
        frame.rowconfigure(r, weight=1)
        ttk.Label(list_frame, text="Uma palavra ou som por linha").grid(
            row=0, column=0, sticky="w"
        )
        text_wrap = ttk.Frame(list_frame)
        text_wrap.grid(row=1, column=0, sticky="nsew")
        text_wrap.columnconfigure(0, weight=1)
        text_wrap.rowconfigure(0, weight=1)
        self.terms_text = tk.Text(text_wrap, height=8, wrap="none")
        self.terms_text.grid(row=0, column=0, sticky="nsew")
        term_scroll = ttk.Scrollbar(
            text_wrap, orient="vertical", command=self.terms_text.yview
        )
        term_scroll.grid(row=0, column=1, sticky="ns")
        self.terms_text.configure(yscrollcommand=term_scroll.set)
        r += 1

        preset_frame = ttk.Frame(frame, style="Panel.TFrame", padding=12)
        preset_frame.grid(row=r, column=0, sticky="ew", pady=6)
        ttk.Label(preset_frame, text="Modo:").grid(row=0, column=0, padx=(0, 4))
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(
            preset_frame,
            textvariable=self.preset_var,
            values=[*PRESET_CONFIGS.keys(), PRESET_CUSTOM],
            state="readonly",
            width=16,
        )
        self.preset_combo.grid(row=0, column=1, padx=(0, 12))
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        self.advanced_button = ttk.Button(
            preset_frame,
            text="Mostrar opções avançadas",
            command=self._toggle_advanced,
            style="Secondary.TButton",
        )
        self.advanced_button.grid(row=0, column=2, sticky="w")
        r += 1

        # Opções avançadas
        self.advanced_visible = False
        self.advanced_frame = ttk.Frame(frame, style="Panel.TFrame", padding=12)
        opts = self.advanced_frame
        opts.grid(row=r, column=0, sticky="ew", pady=6)
        ttk.Label(opts, text="Modelo:").grid(row=0, column=0, padx=(0, 4))
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(
            opts,
            textvariable=self.model_var,
            values=["small", "medium", "large"],
            state="readonly",
            width=10,
        )
        self.model_combo.grid(row=0, column=1, padx=(0, 16))
        ttk.Label(opts, text="Processar em:").grid(row=0, column=2, padx=(0, 4))
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            opts,
            textvariable=self.device_var,
            values=list(DEVICE_CHOICES),
            state="readonly",
            width=8,
        )
        self.device_combo.grid(row=0, column=3, padx=(0, 16))
        ttk.Label(opts, text="Margem antes (s):").grid(row=0, column=4, padx=(0, 4))
        margin_before_info = ttk.Label(
            opts, text="ⓘ", foreground="#1a6fd4", cursor="hand2"
        )
        margin_before_info.grid(row=0, column=5, padx=(0, 4))
        margin_before_info.bind(
            "<Button-1>", lambda _e: messagebox.showinfo("Margem antes", _MARGIN_BEFORE_HELP_TEXT)
        )
        Tooltip(margin_before_info, _MARGIN_BEFORE_HELP_TEXT)
        self.margin_before_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.margin_before_var, width=8).grid(
            row=0, column=6, padx=(0, 16)
        )
        ttk.Label(opts, text="Margem depois (s):").grid(row=0, column=7, padx=(0, 4))
        margin_after_info = ttk.Label(
            opts, text="ⓘ", foreground="#1a6fd4", cursor="hand2"
        )
        margin_after_info.grid(row=0, column=8, padx=(0, 4))
        margin_after_info.bind(
            "<Button-1>", lambda _e: messagebox.showinfo("Margem depois", _MARGIN_AFTER_HELP_TEXT)
        )
        Tooltip(margin_after_info, _MARGIN_AFTER_HELP_TEXT)
        self.margin_after_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.margin_after_var, width=8).grid(
            row=0, column=9
        )
        self.gpu_encoder_var = tk.BooleanVar(value=False)
        self.gpu_encoder_check = ttk.Checkbutton(
            opts,
            text="Usar encoder GPU (NVENC)",
            variable=self.gpu_encoder_var,
        )
        self.gpu_encoder_check.grid(row=1, column=0, columnspan=5, sticky="w", pady=(10, 0))
        r += 1

        opts2 = ttk.Frame(self.advanced_frame)
        opts2.grid(row=r, column=0, sticky="ew", pady=(0, 2))
        ttk.Label(opts2, text="Confiança mínima (0-1):").grid(
            row=0, column=0, padx=(0, 4)
        )
        self.confidence_var = tk.StringVar()
        ttk.Entry(opts2, textvariable=self.confidence_var, width=8).grid(
            row=0, column=1, padx=(0, 4)
        )
        # Ícone de informação: passar o mouse mostra a dica; clicar abre a ajuda.
        info_icon = ttk.Label(
            opts2, text="ⓘ", foreground="#1a6fd4", cursor="hand2"
        )
        info_icon.grid(row=0, column=2)
        info_icon.bind("<Button-1>", lambda _e: self._show_confidence_help())
        Tooltip(info_icon, _CONFIDENCE_HELP_TEXT)
        r += 1

        # Dica sobre o dispositivo (GPU exige NVIDIA + CUDA/cuDNN).
        device_hint = ttk.Label(
            self.advanced_frame,
            text=(
                "Dispositivo: auto usa GPU NVIDIA se disponível (senão CPU). "
                "cpu usa todos os núcleos. cuda força a GPU."
            ),
            foreground="gray",
        )
        device_hint.grid(row=r, column=0, sticky="w")
        r += 1
        confidence_hint = ttk.Label(
            self.advanced_frame,
            text=(
                "Confiança mínima: menor (ex.: 0,4) remove mais vícios de fala "
                "duvidosos; maior (ex.: 0,6) é mais seguro. Clique no ⓘ para detalhes."
            ),
            foreground="gray",
        )
        confidence_hint.grid(row=r, column=0, sticky="w")
        r += 1

        # Botão principal
        self.process_button = ttk.Button(
            frame, text="Processar vídeo", command=self._on_process
        )
        self.advanced_frame.grid_remove()

        action_frame = ttk.Frame(frame)
        action_frame.grid(row=r, column=0, pady=6, sticky="w")
        self.process_button = ttk.Button(
            action_frame, text="Processar vídeo", command=self._on_process
        )
        self.process_button.grid(row=0, column=0, padx=(0, 8))
        self.analyze_button = ttk.Button(
            action_frame, text="Analisar sem renderizar", command=self._on_analyze, style="Secondary.TButton"
        )
        self.analyze_button.grid(row=0, column=1)
        self.open_output_button = ttk.Button(
            action_frame,
            text="Abrir saída",
            command=self._open_output_dir,
            style="Secondary.TButton",
            state="disabled",
        )
        self.open_output_button.grid(row=0, column=2, padx=(8, 0))
        self.open_report_button = ttk.Button(
            action_frame,
            text="Abrir relatório",
            command=self._open_report,
            style="Secondary.TButton",
            state="disabled",
        )
        self.open_report_button.grid(row=0, column=3, padx=(8, 0))
        r += 1

        # Status
        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(frame, textvariable=self.status_var).grid(
            row=r, column=0, sticky="w"
        )
        r += 1

        # Progresso
        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=100.0)
        self.progress.grid(row=r, column=0, sticky="ew", pady=4)
        r += 1

        # Logs
        log_frame = ttk.LabelFrame(frame, text="Logs", padding=8)
        log_frame.grid(row=r, column=0, sticky="nsew", pady=6)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        frame.rowconfigure(r, weight=1)
        self.log_text = tk.Text(log_frame, height=8, state="disabled", wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def _apply_config_to_widgets(self) -> None:
        self.terms_text.delete("1.0", "end")
        self.terms_text.insert("1.0", "\n".join(self.config["palavras_removidas"]))
        self.preset_var.set(self.config.get("modo_preset", PRESET_DEFAULT))
        self.model_var.set(self.config["modelo_padrao"])
        self.device_var.set(self.config.get("dispositivo", "auto"))
        self.margin_before_var.set(str(self.config["margem_antes"]))
        self.margin_after_var.set(str(self.config["margem_depois"]))
        self.confidence_var.set(str(self.config.get("limiar_confianca", 0.60)))
        self.gpu_encoder_var.set(bool(self.config.get("usar_encoder_gpu", False)))
        pasta = self.config.get("pasta_saida", "")
        if pasta:
            self.output_var.set(pasta)
        else:
            self.output_var.set("Mesma pasta do vídeo")
        self._apply_preset_if_needed()
        self.margin_before_var.trace_add("write", self._mark_custom_preset)
        self.margin_after_var.trace_add("write", self._mark_custom_preset)
        self.confidence_var.trace_add("write", self._mark_custom_preset)

    # ---- Seleção de arquivos ----

    def _choose_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecionar vídeo",
            filetypes=[("Arquivos MP4", "*.mp4"), ("Todos os arquivos", "*.*")],
        )
        if path:
            self.video_var.set(path)

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(title="Escolher pasta de saída")
        if path:
            self.output_var.set(os.path.normpath(os.path.abspath(path)))

    def _show_confidence_help(self) -> None:
        messagebox.showinfo("Confiança mínima", _CONFIDENCE_HELP_TEXT)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(background="#eef3f8")
        style.configure("TFrame", background="#eef3f8")
        style.configure("Panel.TFrame", background="#ffffff", relief="flat")
        style.configure("TLabelframe", background="#ffffff")
        style.configure("TLabelframe.Label", background="#ffffff", font=("Segoe UI Semibold", 10))
        style.configure("TLabel", background="#eef3f8", font=("Segoe UI", 10))
        style.configure("Subtitle.TLabel", background="#ffffff", foreground="#4b5b6b", font=("Segoe UI", 10))
        style.configure("HeroTitle.TLabel", background="#ffffff", foreground="#142235", font=("Segoe UI Semibold", 20))
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10))
        style.configure("Secondary.TButton", font=("Segoe UI", 10))
        style.configure("StatusReady.TLabel", background="#dff4e8", foreground="#1d6b43", padding=(10, 4))
        style.configure("StatusBusy.TLabel", background="#e7f0ff", foreground="#2457a6", padding=(10, 4))
        style.configure("StatusError.TLabel", background="#fde8e8", foreground="#a63333", padding=(10, 4))

    def _toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.grid()
            self.advanced_button.configure(text="Ocultar opções avançadas")
        else:
            self.advanced_frame.grid_remove()
            self.advanced_button.configure(text="Mostrar opções avançadas")

    def _on_preset_selected(self, _event: object = None) -> None:
        self._apply_preset_if_needed()

    def _apply_preset_if_needed(self) -> None:
        preset = self.preset_var.get().strip() or PRESET_DEFAULT
        config = PRESET_CONFIGS.get(preset)
        if config is None:
            return
        self._applying_preset = True
        self.confidence_var.set(config["confidence"])
        self.margin_before_var.set(config["margin_before"])
        self.margin_after_var.set(config["margin_after"])
        self._applying_preset = False

    def _mark_custom_preset(self, *_args: object) -> None:
        if self._applying_preset:
            return
        current = self.preset_var.get().strip()
        if current == PRESET_CUSTOM:
            return
        config = PRESET_CONFIGS.get(current)
        if config is None:
            self.preset_var.set(PRESET_CUSTOM)
            return
        if (
            self.confidence_var.get().strip() != config["confidence"]
            or self.margin_before_var.get().strip() != config["margin_before"]
            or self.margin_after_var.get().strip() != config["margin_after"]
        ):
            self.preset_var.set(PRESET_CUSTOM)

    # ---- Processamento ----

    def _on_process(self) -> None:
        self._start_processing(analyze_only=False)

    def _on_analyze(self) -> None:
        self._start_processing(analyze_only=True)

    def _start_processing(self, analyze_only: bool) -> None:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("PutzCleaner", "Já existe um processamento em andamento.")
            return

        try:
            options = self._collect_options()
            options = ProcessingOptions(**{**options.__dict__, "analyze_only": analyze_only})
        except (ValueError, TermValidationError, CutterError) as exc:
            messagebox.showerror("Entrada inválida", str(exc))
            return

        # Persistir configuração antes de iniciar.
        self.config["palavras_removidas"] = list(options.terms)
        self.config["modelo_padrao"] = options.model
        self.config["dispositivo"] = options.device
        self.config["margem_antes"] = options.margin_before
        self.config["margem_depois"] = options.margin_after
        self.config["limiar_confianca"] = options.min_probability
        self.config["modo_preset"] = self.preset_var.get().strip() or PRESET_DEFAULT
        self.config["usar_encoder_gpu"] = options.use_gpu_encoder
        self.config["pasta_saida"] = (
            "" if self.output_var.get() == "Mesma pasta do vídeo"
            else str(options.output_directory)
        )
        self._warn_if_large_margins(options)
        try:
            if self.config_corrupt:
                if not messagebox.askyesno(
                    "config.json inválido",
                    "O config.json estava inválido. Deseja sobrescrevê-lo com a "
                    "configuração atual?",
                ):
                    self._append_log("Configuração não foi salva (usuário recusou).")
                else:
                    save_config(self.project_root, self.config)
                    self.config_corrupt = False
            else:
                save_config(self.project_root, self.config)
        except OSError as exc:
            self._append_log(f"Aviso: não foi possível salvar config.json: {exc}")

        self._set_controls_enabled(False)
        self.progress.configure(mode="determinate", value=0.0)
        self.cancel_event = threading.Event()
        emit = self._make_emitter()
        self.worker = threading.Thread(
            target=run_worker,
            args=(options, self.project_root, self.transcriber, self.cancel_event, emit),
            daemon=False,
        )
        self.worker.start()

    def _collect_options(self) -> ProcessingOptions:
        video = self.video_var.get().strip()
        if not video:
            raise ValueError("Selecione um vídeo primeiro.")
        input_path = Path(video)
        if input_path.suffix.lower() != ".mp4":
            raise ValueError("O arquivo selecionado precisa ter a extensão .mp4.")

        model = self.model_var.get().strip()
        if model not in MODEL_MAP:
            raise ValueError("Selecione um modelo válido (small, medium ou large).")

        device = self.device_var.get().strip()
        if device not in DEVICE_CHOICES:
            raise ValueError("Selecione um dispositivo válido (auto, cpu ou cuda).")

        raw_lines = self.terms_text.get("1.0", "end").splitlines()
        terms = validate_terms(raw_lines)

        try:
            margin_before = parse_decimal(self.margin_before_var.get())
            margin_after = parse_decimal(self.margin_after_var.get())
        except ValueError as exc:
            raise ValueError(
                "Margens inválidas. Use um número maior ou igual a 0 (ex.: 0,05)."
            ) from exc
        for margin in (margin_before, margin_after):
            if margin < 0.0:
                raise ValueError("As margens devem ser maiores ou iguais a 0.")

        try:
            min_probability = parse_decimal(self.confidence_var.get())
        except ValueError as exc:
            raise ValueError(
                "Confiança mínima inválida. Use um número entre 0 e 1 (ex.: 0,5)."
            ) from exc
        if not (0.0 <= min_probability <= 1.0):
            raise ValueError("A confiança mínima deve estar entre 0 e 1.")

        output_text = self.output_var.get().strip()
        if not output_text or output_text == "Mesma pasta do vídeo":
            output_directory = input_path.parent
        else:
            output_directory = Path(output_text)

        return ProcessingOptions(
            input_video=input_path,
            output_directory=output_directory,
            terms=terms,
            model=model,
            device=device,
            margin_before=margin_before,
            margin_after=margin_after,
            min_probability=min_probability,
            preset_name=self.preset_var.get().strip() or PRESET_DEFAULT,
            use_gpu_encoder=bool(self.gpu_encoder_var.get()),
        )

    def _warn_if_large_margins(self, options: ProcessingOptions) -> None:
        if options.margin_before > MAX_MARGIN_SEC:
            self._append_log(
                f"Aviso: margem antes acima do recomendado ({options.margin_before:.2f}s > {MAX_MARGIN_SEC:.2f}s)."
            )
        if options.margin_after > MAX_MARGIN_SEC:
            self._append_log(
                f"Aviso: margem depois acima do recomendado ({options.margin_after:.2f}s > {MAX_MARGIN_SEC:.2f}s)."
            )

    # ---- Fila de eventos ----

    def _make_emitter(self) -> Callable[..., None]:
        q = self.queue

        def emit(kind: str, *payload: Any) -> None:
            q.put((kind, payload))

        return emit

    def _drain_events(self) -> None:
        processed = 0
        try:
            while processed < 100:
                kind, payload = self.queue.get_nowait()
                self._handle_event(kind, payload)
                processed += 1
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _handle_event(self, kind: str, payload: tuple) -> None:
        if kind == "log":
            self._append_log(payload[0])
        elif kind == "status":
            self.status_var.set(payload[0])
        elif kind == "progress_mode":
            mode = payload[0]
            if mode == "indeterminate":
                self.progress.configure(mode="indeterminate")
                self.progress.start(60)
            else:
                self.progress.stop()
                self.progress.configure(mode="determinate")
        elif kind == "progress":
            self.progress.configure(value=float(payload[0]))
        elif kind == "success":
            video_path, report_path, occurrences, cuts = payload
            self._append_log(f"Vídeo: {video_path}")
            self._append_log(f"Relatório: {report_path}")
            self._last_video_path = video_path
            self._last_report_path = report_path
            self._last_output_dir = str(Path(video_path).parent)
            self.open_output_button.configure(state="normal")
            self.open_report_button.configure(state="normal")
            messagebox.showinfo(
                "PutzCleaner",
                "Processamento concluído.\n\n"
                f"Vídeo: {video_path}\n"
                f"Relatório: {report_path}",
            )
        elif kind == "analysis_success":
            report_path, transcript_path, occurrences, cuts, removed_seconds = payload
            self._append_log(f"Relatório da análise: {report_path}")
            self._append_log(f"Transcrição: {transcript_path}")
            self._last_video_path = None
            self._last_report_path = report_path
            self._last_output_dir = str(Path(report_path).parent)
            self.open_output_button.configure(state="normal")
            self.open_report_button.configure(state="normal")
            messagebox.showinfo(
                "PutzCleaner",
                "Análise concluída.\n\n"
                f"Ocorrências aceitas: {occurrences}\n"
                f"Cortes planejados: {cuts}\n"
                f"Tempo estimado removido: {removed_seconds}s\n"
                f"Relatório: {report_path}",
            )
        elif kind == "error":
            user_message = payload[0]
            technical = payload[1] if len(payload) > 1 else ""
            if technical:
                self._append_log(f"Erro: {technical}")
            self.status_badge.configure(text="Erro", style="StatusError.TLabel")
            messagebox.showerror("PutzCleaner", user_message)
        elif kind == "done":
            self._on_worker_done()

    def _on_worker_done(self) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self._set_controls_enabled(True)
        if self._closing_after_cancel:
            self.root.destroy()

    # ---- Utilidades de UI ----

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        # Limitar às últimas _LOG_MAX_LINES linhas.
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > _LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{line_count - _LOG_MAX_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.process_button.configure(state=state)
        self.analyze_button.configure(state=state)
        self.advanced_button.configure(state=state)
        self.terms_text.configure(state=state)
        self.model_combo.configure(state="readonly" if enabled else "disabled")
        self.device_combo.configure(state="readonly" if enabled else "disabled")
        self.preset_combo.configure(state="readonly" if enabled else "disabled")
        self.gpu_encoder_check.configure(state=state)
        self.status_badge.configure(
            text="Pronto" if enabled else "Processando",
            style="StatusReady.TLabel" if enabled else "StatusBusy.TLabel",
        )

    def _open_output_dir(self) -> None:
        if not self._last_output_dir:
            return
        self._open_path(Path(self._last_output_dir))

    def _open_report(self) -> None:
        if not self._last_report_path:
            return
        self._open_path(Path(self._last_report_path))

    def _open_path(self, path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                raise OSError("Abertura automática disponível apenas no Windows.")
        except OSError as exc:
            messagebox.showerror("PutzCleaner", f"Não foi possível abrir {path}.\n\n{exc}")

    # ---- Fechamento seguro (seção 20.6) ----

    def _on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno(
                "PutzCleaner",
                "Há um processamento em andamento. Deseja cancelar e sair?",
            ):
                return
            if self.cancel_event is not None:
                self.cancel_event.set()
            self._closing_after_cancel = True
            self.status_var.set("Cancelando com segurança...")
            self.process_button.configure(state="disabled")
        else:
            self.root.destroy()

