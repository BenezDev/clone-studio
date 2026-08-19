"""Comandos sugeridos ao usuário, corretos para a plataforma em uso.

Uma dica que manda o usuário de Windows rodar `./scripts/models.sh` é pior que
nenhuma dica: ele digita, recebe "comando não encontrado" e conclui que o
programa está quebrado.

Todo texto que sugere um comando passa por aqui.
"""

from __future__ import annotations

import os


def is_windows() -> bool:
    return os.name == "nt"


def install(target: str = "", *, musetalk: bool = False) -> str:
    """Comando para instalar (ou reinstalar) um ambiente."""
    if is_windows():
        if musetalk:
            return "powershell -ExecutionPolicy Bypass -File .\\install.ps1 -WithMuseTalk"
        if target:
            return f"powershell -ExecutionPolicy Bypass -File .\\install.ps1 -Only {target}"
        return "powershell -ExecutionPolicy Bypass -File .\\install.ps1"
    if musetalk:
        return "./install.sh --with-musetalk"
    if target:
        return f"./install.sh --only {target}"
    return "./install.sh"


def models(action: str = "install", target: str = "") -> str:
    """Comando do gerenciador de pesos."""
    base = ".\\scripts\\models.ps1" if is_windows() else "./scripts/models.sh"
    partes = [base, action]
    if target:
        partes.append(target)
    return " ".join(partes)


def cli(command: str = "") -> str:
    """Comando da CLI."""
    base = ".\\scripts\\clone-studio.ps1" if is_windows() else "./scripts/clone-studio"
    return f"{base} {command}".strip()


def doctor() -> str:
    return ".\\scripts\\doctor.ps1" if is_windows() else "./scripts/doctor.sh"


def start() -> str:
    return ".\\start.ps1" if is_windows() else "./start.sh"


def stop() -> str:
    return ".\\stop.ps1" if is_windows() else "./stop.sh"


def logs(nome: str) -> str:
    """Caminho de log com o separador da plataforma."""
    return f"logs\\{nome}" if is_windows() else f"logs/{nome}"
