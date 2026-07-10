"""PutzCleaner — EXPORTAÇÃO DA INTERFACE (somente estrutura de UI).

============================================================================
BIBLIOTECA DE INTERFACE: Tkinter puro (biblioteca padrão do Python), usando
os widgets temáticos do submódulo tkinter.ttk. Nenhum framework externo
(sem PyQt, sem Kivy, sem web).
============================================================================

Este arquivo é uma versão isolada apenas com a ESTRUTURA da interface para
avaliação de UI/UX. Toda a lógica de backend (transcrição Whisper, corte de
vídeo com FFmpeg, geração de relatório, leitura/escrita de configuração,
worker em thread) foi substituída por `pass`/comentários. As VARIÁVEIS DE
ESTADO dos inputs (StringVar, Entry, Text, Combobox, etc.) foram mantidas
intactas, assim como o layout (grid) e os textos.

Pode ser executado diretamente para visualizar a janela:  python este_arquivo.py

------------------------------------------------------------------
MAPA DOS INPUTS E SEUS ESTADOS (o que o backend lê da interface)
------------------------------------------------------------------
- self.video_var            (StringVar)  -> caminho do MP4 de entrada (readonly)
- self.output_var           (StringVar)  -> pasta de saída (readonly; "Mesma pasta do vídeo")
- self.terms_text           (tk.Text)    -> lista de vícios, uma palavra/som por linha
- self.model_var            (StringVar)  -> Combobox: small | medium | large
- self.device_var           (StringVar)  -> Combobox: auto | cpu | cuda
- self.margin_before_var    (StringVar)  -> Entry: margem antes (s), 0..2, aceita vírgula
- self.margin_after_var     (StringVar)  -> Entry: margem depois (s), 0..2, aceita vírgula
- self.confidence_var       (StringVar)  -> Entry: confiança mínima 0..1 (+ ícone ⓘ de ajuda)
- self.status_var           (StringVar)  -> label de status (saída)
- self.progress             (Progressbar)-> barra 0..100 (saída)
- self.log_text             (tk.Text)    -> área de logs readonly (saída)
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

# --- Constantes de domínio usadas apenas para popular a UI ---
MODEL_CHOICES = ["small", "medium", "large"]
DEVICE_CHOICES = ("auto", "cpu", "cuda")
MAX_MARGIN_SEC = 2.0
_LOG_MAX_LINES = 1000

# Valores iniciais dos inputs (viriam de config.json no app real).
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
}

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


# ---------------------------------------------------------------------------
# Estrutura imutável com o snapshot dos inputs (o que a UI entrega ao backend)
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


# ---------------------------------------------------------------------------
# Tooltip: dica flutuante ao passar o mouse (usada no ícone de ajuda ⓘ)
# ---------------------------------------------------------------------------
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


def parse_decimal(text: str) -> float:
    """Converte '0.05' ou '0,05' em float (helper de leitura de input da UI)."""
    text = text.strip()
    if text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    return float(text)


# ===========================================================================
# APLICAÇÃO GUI — SOMENTE ESTRUTURA DA INTERFACE
# ===========================================================================
class PutzCleanerApp:
    def __init__(self, root: tk.Tk, project_root: Path) -> None:
        self.root = root
        self.project_root = project_root

        # Estado da configuração (no app real vem de load_config()).
        self.config = dict(DEFAULT_CONFIG)

        # Janela: título, tamanho inicial e mínimo, e fechamento seguro.
        root.title("PutzCleaner")
        root.geometry("900x720")
        root.minsize(760, 600)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._apply_config_to_widgets()

    # ---- Construção da tela (layout em grid) ----
    def _build_ui(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)

        frame = ttk.Frame(root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        r = 0
        # Título e subtítulo
        title = ttk.Label(frame, text="PutzCleaner", font=("Segoe UI", 18, "bold"))
        title.grid(row=r, column=0, sticky="w")
        r += 1
        subtitle = ttk.Label(
            frame, text="Removedor automático de vícios de fala para entrevistas"
        )
        subtitle.grid(row=r, column=0, sticky="w", pady=(0, 8))
        r += 1

        # Linha do vídeo: botão + campo readonly com o caminho
        video_frame = ttk.Frame(frame)
        video_frame.grid(row=r, column=0, sticky="ew", pady=4)
        video_frame.columnconfigure(1, weight=1)
        ttk.Button(
            video_frame, text="Selecionar vídeo", command=self._choose_video
        ).grid(row=0, column=0, padx=(0, 8))
        self.video_var = tk.StringVar()
        ttk.Entry(video_frame, textvariable=self.video_var, state="readonly").grid(
            row=0, column=1, sticky="ew"
        )
        r += 1

        # Linha de saída: campo readonly + botão "Escolher pasta"
        out_frame = ttk.Frame(frame)
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

        # LabelFrame da lista editável de palavras/sons (Text + Scrollbar)
        list_frame = ttk.LabelFrame(frame, text="Palavras/sons a remover", padding=8)
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

        # Opções (linha 1): Modelo, Dispositivo, Margem antes, Margem depois
        opts = ttk.Frame(frame)
        opts.grid(row=r, column=0, sticky="ew", pady=6)
        ttk.Label(opts, text="Modelo:").grid(row=0, column=0, padx=(0, 4))
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(
            opts,
            textvariable=self.model_var,
            values=MODEL_CHOICES,
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
        self.margin_before_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.margin_before_var, width=8).grid(
            row=0, column=5, padx=(0, 16)
        )
        ttk.Label(opts, text="Margem depois (s):").grid(row=0, column=6, padx=(0, 4))
        self.margin_after_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.margin_after_var, width=8).grid(
            row=0, column=7
        )
        r += 1

        # Opções (linha 2): Confiança mínima + ícone de informação ⓘ
        opts2 = ttk.Frame(frame)
        opts2.grid(row=r, column=0, sticky="ew", pady=(0, 2))
        ttk.Label(opts2, text="Confiança mínima (0-1):").grid(
            row=0, column=0, padx=(0, 4)
        )
        self.confidence_var = tk.StringVar()
        ttk.Entry(opts2, textvariable=self.confidence_var, width=8).grid(
            row=0, column=1, padx=(0, 4)
        )
        # Ícone de informação: hover mostra o tooltip; clique abre a ajuda.
        info_icon = ttk.Label(
            opts2, text="ⓘ", foreground="#1a6fd4", cursor="hand2"
        )
        info_icon.grid(row=0, column=2)
        info_icon.bind("<Button-1>", lambda _e: self._show_confidence_help())
        Tooltip(info_icon, _CONFIDENCE_HELP_TEXT)
        r += 1

        # Dicas explicativas (labels cinza)
        device_hint = ttk.Label(
            frame,
            text=(
                "Dispositivo: auto usa GPU NVIDIA se disponível (senão CPU). "
                "cpu usa todos os núcleos. cuda força a GPU."
            ),
            foreground="gray",
        )
        device_hint.grid(row=r, column=0, sticky="w")
        r += 1
        confidence_hint = ttk.Label(
            frame,
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
        self.process_button.grid(row=r, column=0, pady=6)
        r += 1

        # Label de status
        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(frame, textvariable=self.status_var).grid(
            row=r, column=0, sticky="w"
        )
        r += 1

        # Barra de progresso (0..100)
        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=100.0)
        self.progress.grid(row=r, column=0, sticky="ew", pady=4)
        r += 1

        # Área de logs readonly (Text + Scrollbar)
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

    # ---- Preenche os widgets com os valores iniciais dos inputs ----
    def _apply_config_to_widgets(self) -> None:
        self.terms_text.delete("1.0", "end")
        self.terms_text.insert("1.0", "\n".join(self.config["palavras_removidas"]))
        self.model_var.set(self.config["modelo_padrao"])
        self.device_var.set(self.config.get("dispositivo", "auto"))
        self.margin_before_var.set(str(self.config["margem_antes"]))
        self.margin_after_var.set(str(self.config["margem_depois"]))
        self.confidence_var.set(str(self.config.get("limiar_confianca", 0.60)))
        pasta = self.config.get("pasta_saida", "")
        self.output_var.set(pasta if pasta else "Mesma pasta do vídeo")

    # ---- Seleção de arquivos (interação de UI) ----
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
            self.output_var.set(path)

    def _show_confidence_help(self) -> None:
        messagebox.showinfo("Confiança mínima", _CONFIDENCE_HELP_TEXT)

    # ---- Ação principal: lê o estado dos inputs e (no app real) inicia o worker ----
    def _on_process(self) -> None:
        # Snapshot dos inputs. As validações e o processamento foram removidos.
        options = self._collect_options()
        # BACKEND REMOVIDO: aqui o app real salvaria a config, desativaria os
        # controles e iniciaria a transcrição/corte em uma thread de trabalho.
        self.status_var.set("(demo de UI) inputs capturados; backend removido.")
        self._append_log(f"[demo] terms={len(options.terms)} model={options.model} "
                         f"device={options.device} conf={options.min_probability}")

    def _collect_options(self) -> ProcessingOptions:
        """Lê o estado atual de TODOS os inputs da interface."""
        video = self.video_var.get().strip()
        input_path = Path(video) if video else Path()

        model = self.model_var.get().strip()
        device = self.device_var.get().strip()

        raw_lines = self.terms_text.get("1.0", "end").splitlines()
        terms = tuple(line.strip() for line in raw_lines if line.strip())

        try:
            margin_before = parse_decimal(self.margin_before_var.get())
            margin_after = parse_decimal(self.margin_after_var.get())
            min_probability = parse_decimal(self.confidence_var.get())
        except ValueError:
            margin_before = margin_after = min_probability = 0.0

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
        )

    # ---- Saídas de UI (status, progresso, logs) ----
    # No app real, um worker em thread envia eventos por uma queue.Queue e a
    # thread principal os consome via root.after() para atualizar estes widgets.
    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > _LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{line_count - _LOG_MAX_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.process_button.configure(state=state)
        self.terms_text.configure(state=state)
        self.model_combo.configure(state="readonly" if enabled else "disabled")
        self.device_combo.configure(state="readonly" if enabled else "disabled")

    # ---- Fechamento da janela ----
    def _on_close(self) -> None:
        # BACKEND REMOVIDO: no app real, se houver processamento em andamento,
        # pergunta se deseja cancelar, sinaliza o cancelamento e aguarda a
        # limpeza antes de destruir a janela.
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    PutzCleanerApp(root, Path("."))
    root.mainloop()


if __name__ == "__main__":
    main()
