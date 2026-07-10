"""Ponto de entrada do PutzCleaner (seção 12).

Responsabilidades exclusivas: calcular PROJECT_ROOT, configurar caches locais
do Hugging Face antes de importar faster_whisper indiretamente, criar uma
única instância de ``tk.Tk`` e tratar erro fatal de inicialização.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _configure_local_caches() -> None:
    models_dir = PROJECT_ROOT / "models"
    hf_home = models_dir / ".hf"
    hf_hub = hf_home / "hub"
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("HF_HUB_CACHE", str(hf_hub))
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def _show_fatal(message: str) -> None:
    """Mostra erro fatal ao usuário, mesmo sob pythonw.exe."""

    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("PutzCleaner", message)
        root.destroy()
    except Exception:  # noqa: BLE001
        if os.name == "nt":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(0, message, "PutzCleaner", 0x10)
            except Exception:  # noqa: BLE001
                sys.stderr.write(message + "\n")
        else:
            sys.stderr.write(message + "\n")


def main() -> int:
    _configure_local_caches()
    try:
        import tkinter as tk

        from gui import PutzCleanerApp

        root = tk.Tk()
        PutzCleanerApp(root, PROJECT_ROOT)
        root.mainloop()
        return 0
    except Exception as exc:  # noqa: BLE001
        _show_fatal(
            "Não foi possível iniciar o PutzCleaner.\n\n"
            f"{type(exc).__name__}: {exc}"
        )
        return 1


if __name__ == "__main__":
    # Garantir que os módulos irmãos em src/ sejam importáveis.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
