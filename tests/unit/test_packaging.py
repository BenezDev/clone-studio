"""Empacotamento para outra máquina.

Este teste existe por um bug real: a primeira versão do pacote de Windows foi
montada à mão e saiu sem o COMO-RODAR.md e sem os 50 roteiros da biblioteca.
Um pacote incompleto só é descoberto na casa do usuário, quando já é tarde.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "package-windows.sh"


class TestScriptDeEmpacotamento:
    def test_existe_e_e_executavel(self) -> None:
        import os

        assert SCRIPT.exists(), "empacotar à mão foi o que causou o pacote incompleto"
        assert os.access(SCRIPT, os.X_OK)

    @pytest.mark.skipif(shutil.which("bash") is None, reason="bash ausente")
    def test_sintaxe_valida(self) -> None:
        proc = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr

    def test_exclui_dados_pessoais(self) -> None:
        """`data/` guarda voz e vídeos do usuário. Nunca pode viajar."""
        conteudo = SCRIPT.read_text(encoding="utf-8")
        assert "VAZAMENTO" in conteudo or "data/" in conteudo
        # A lista de diretórios copiados não pode conter `data`.
        bloco = re.search(r"DIRETORIOS=\((.*?)\)", conteudo, re.DOTALL)
        assert bloco, "lista de diretórios não encontrada"
        itens = bloco.group(1).split()
        assert "data" not in itens
        assert "models" not in itens
        assert ".envs" not in itens

    def test_confere_o_proprio_resultado(self) -> None:
        """O script precisa falhar quando algo essencial ficou de fora."""
        conteudo = SCRIPT.read_text(encoding="utf-8")
        assert "OBRIGATORIOS=" in conteudo
        assert "COMO-RODAR.md" in conteudo
        assert "assets/scripts" in conteudo

    def test_licenca_viaja_com_o_pacote(self) -> None:
        """Software proprietário sem o termo de licença junto não se defende."""
        conteudo = SCRIPT.read_text(encoding="utf-8")
        bloco = re.search(r"ARQUIVOS=\((.*?)\)", conteudo, re.DOTALL)
        assert bloco, "lista de arquivos não encontrada"
        assert "LICENSE" in bloco.group(1).split()
        assert '"LICENSE"' in conteudo, "LICENSE fora da conferência final"

    def test_lista_obrigatoria_cobre_o_windows(self) -> None:
        conteudo = SCRIPT.read_text(encoding="utf-8")
        for essencial in (
            "install.ps1",
            "start.ps1",
            "stop.ps1",
            "scripts/doctor.ps1",
            "scripts/launch.py",
            "docs/WINDOWS.md",
        ):
            assert essencial in conteudo, f"{essencial} fora da conferência"


class TestFontesExistem:
    """Tudo que o empacotador promete copiar precisa existir no repositório."""

    @pytest.mark.parametrize(
        "caminho",
        [
            "install.ps1", "start.ps1", "stop.ps1",
            "install.bat", "start.bat", "stop.bat", "doctor.bat",
            "install.sh", "start.sh", "stop.sh",
            "scripts/doctor.ps1", "scripts/models.ps1",
            "scripts/clone-studio.ps1", "scripts/clone-studio.bat",
            "scripts/launch.py",
            "COMO-RODAR.md", "README.md", "LICENSE",
            "docs/WINDOWS.md", "docs/TROUBLESHOOTING.md",
            "config/default.yaml", "config/model_registry.yaml",
            "apps/web/package.json", "apps/api/app/main.py",
        ],
    )
    def test_arquivo_existe(self, caminho: str) -> None:
        assert (ROOT / caminho).exists(), f"{caminho} não existe no repositório"

    def test_todos_os_requirements(self) -> None:
        for nome in ("backend", "qwen-tts", "whisper", "musetalk"):
            assert (ROOT / "requirements" / f"{nome}.txt").exists()

    def test_biblioteca_de_roteiros_completa(self) -> None:
        arquivos = list((ROOT / "assets" / "scripts").glob("*.yaml"))
        assert len(arquivos) == 5, f"esperado 5 nichos, achei {len(arquivos)}"
