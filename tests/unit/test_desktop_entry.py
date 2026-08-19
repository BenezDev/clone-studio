"""Atalho de aplicativo (.desktop).

Um `.desktop` malformado é ignorado **em silêncio** pelo menu do sistema: o
ícone simplesmente não aparece, sem nenhuma mensagem. É o pior modo de falha
possível, então a estrutura é verificada aqui.
"""

from __future__ import annotations

import configparser
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "app-launcher.sh"
INSTALLER = ROOT / "scripts" / "install-desktop-entry.sh"
ICON = ROOT / "assets" / "clone-studio.svg"


class TestArquivosDoAtalho:
    def test_lancador_existe_e_e_executavel(self) -> None:
        assert LAUNCHER.exists()
        assert os.access(LAUNCHER, os.X_OK), "o .desktop não roda um script sem +x"

    def test_instalador_existe_e_e_executavel(self) -> None:
        assert INSTALLER.exists()
        assert os.access(INSTALLER, os.X_OK)

    def test_icone_e_svg_valido(self) -> None:
        import xml.etree.ElementTree as ET

        assert ICON.exists()
        raiz = ET.parse(ICON).getroot()
        assert raiz.tag.endswith("svg")
        # Sem viewBox o ícone não escala nos vários tamanhos do tema.
        assert "viewBox" in raiz.attrib

    def test_instalador_nao_usa_sudo(self) -> None:
        """Comentários podem citar 'sudo'; o que importa é não invocá-lo."""
        codigo = [
            linha.split("#", 1)[0]
            for linha in INSTALLER.read_text(encoding="utf-8").splitlines()
        ]
        invocacoes = [l for l in codigo if re.search(r"(^|[;&|]\s*)sudo\s", l)]
        assert not invocacoes, (
            f"o atalho é por usuário e não deve escalar privilégio: {invocacoes}"
        )
        assert ".local/share/applications" in "\n".join(codigo)


class TestLancador:
    def test_aceita_as_tres_acoes(self) -> None:
        conteudo = LAUNCHER.read_text(encoding="utf-8")
        for acao in ("--stop", "--doctor", "--start"):
            assert acao in conteudo

    def test_desacopla_da_sessao_grafica(self) -> None:
        """Sem setsid, fechar a sessão que lançou o ícone derruba a aplicação."""
        conteudo = LAUNCHER.read_text(encoding="utf-8")
        assert "setsid" in conteudo

    def test_nao_sobe_instancia_duplicada(self) -> None:
        """Clicar duas vezes no ícone precisa apenas reabrir o navegador."""
        conteudo = LAUNCHER.read_text(encoding="utf-8")
        assert "/api/health" in conteudo, (
            "o lançador precisa checar se já está no ar antes de iniciar"
        )

    def test_reporta_erro_graficamente(self) -> None:
        """Sem terminal, uma falha silenciosa deixaria o usuário sem pista."""
        conteudo = LAUNCHER.read_text(encoding="utf-8")
        assert "zenity" in conteudo or "notify-send" in conteudo

    @pytest.mark.skipif(shutil.which("bash") is None, reason="bash ausente")
    def test_sintaxe_bash_valida(self) -> None:
        proc = subprocess.run(
            ["bash", "-n", str(LAUNCHER)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.skipif(shutil.which("bash") is None, reason="bash ausente")
    def test_sintaxe_do_instalador(self) -> None:
        proc = subprocess.run(
            ["bash", "-n", str(INSTALLER)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr


class TestDesktopGerado:
    """Gera o .desktop num HOME temporário e valida o resultado."""

    @pytest.fixture
    def entrada(self, tmp_path: Path) -> Path:
        if shutil.which("bash") is None:
            pytest.skip("bash ausente")
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        # Sem área de trabalho no HOME falso: só o menu é exercitado.
        proc = subprocess.run(
            [str(INSTALLER), "--no-desktop"],
            capture_output=True,
            text=True,
            env=env,
        )
        destino = tmp_path / ".local/share/applications/local-clone-studio.desktop"
        if not destino.exists():
            pytest.fail(f"instalador não gerou o atalho:\n{proc.stdout}\n{proc.stderr}")
        return destino

    def _parse(self, caminho: Path) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(caminho, encoding="utf-8")
        return parser

    def test_secao_e_campos_obrigatorios(self, entrada: Path) -> None:
        cfg = self._parse(entrada)
        assert "Desktop Entry" in cfg
        secao = cfg["Desktop Entry"]
        assert secao["Type"] == "Application"
        assert secao["Name"]
        assert secao["Icon"]
        assert secao["Terminal"] == "false", "o atalho não deve abrir terminal"

    def test_exec_aponta_para_arquivo_executavel(self, entrada: Path) -> None:
        """A raiz real tem espaço e acento; o Exec precisa sobreviver a isso."""
        cfg = self._parse(entrada)
        for secao in cfg.sections():
            if "Exec" not in cfg[secao]:
                continue
            argv = shlex.split(cfg[secao]["Exec"])
            alvo = Path(argv[0])
            assert alvo.is_file(), f"[{secao}] Exec quebrado: {argv[0]}"
            assert os.access(alvo, os.X_OK), f"[{secao}] Exec sem permissão"

    def test_acoes_declaradas_tem_secao(self, entrada: Path) -> None:
        cfg = self._parse(entrada)
        declaradas = [
            a for a in cfg["Desktop Entry"].get("Actions", "").split(";") if a
        ]
        assert declaradas, "esperado ao menos Parar e Diagnóstico"
        for acao in declaradas:
            assert f"Desktop Action {acao}" in cfg, (
                f"ação '{acao}' declarada sem a seção correspondente"
            )

    def test_icone_instalado_no_tema(self, entrada: Path) -> None:
        icone = (
            entrada.parents[1]
            / "icons/hicolor/scalable/apps/local-clone-studio.svg"
        )
        assert icone.exists(), "o ícone precisa entrar no tema hicolor"

    @pytest.mark.skipif(
        shutil.which("desktop-file-validate") is None,
        reason="desktop-file-validate não instalado",
    )
    def test_passa_no_validador_oficial(self, entrada: Path) -> None:
        proc = subprocess.run(
            ["desktop-file-validate", str(entrada)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_desinstalacao_remove_tudo(self, entrada: Path, tmp_path: Path) -> None:
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        subprocess.run(
            [str(INSTALLER), "--uninstall"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert not entrada.exists()
        assert ROOT.exists(), "desinstalar o atalho não pode tocar no projeto"
