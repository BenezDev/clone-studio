"""Dicas de comando corretas para a plataforma.

Dois bugs reais motivaram este arquivo:

1. O `models.ps1` mandava o usuário de Windows rodar `./scripts/models.sh` —
   comando que não existe lá. Ele digita, recebe "não encontrado" e conclui que
   o programa está quebrado.
2. Ao centralizar as dicas, o import de `platform_hints` não entrou em 6
   arquivos. O código só quebraria com `NameError` no momento exato de mostrar
   um erro ao usuário — o pior momento possível.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from core import platform_hints as hints

ROOT = Path(__file__).resolve().parents[2]

# Arquivos que produzem texto de dica para o usuário.
FONTES = [
    "core/pipeline/stages.py",
    "core/pipeline/runner.py",
    "core/diagnostics/checks.py",
    "services/lipsync/musetalk.py",
    "services/tts/qwen3_tts.py",
    "services/transcription/faster_whisper.py",
    "services/comfyui/wan22.py",
]


class TestComandosPorPlataforma:
    def test_linux_usa_sh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hints.os, "name", "posix")
        assert hints.install() == "./install.sh"
        assert hints.install("whisper") == "./install.sh --only whisper"
        assert hints.install(musetalk=True) == "./install.sh --with-musetalk"
        assert hints.models("install", "x") == "./scripts/models.sh install x"
        assert hints.doctor() == "./scripts/doctor.sh"
        assert hints.start() == "./start.sh"
        assert hints.stop() == "./stop.sh"
        assert hints.logs("api.out") == "logs/api.out"

    def test_windows_usa_ps1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hints.os, "name", "nt")
        assert ".ps1" in hints.install()
        assert "-Only whisper" in hints.install("whisper")
        assert "-WithMuseTalk" in hints.install(musetalk=True)
        assert hints.models("install", "x") == ".\\scripts\\models.ps1 install x"
        assert hints.doctor() == ".\\scripts\\doctor.ps1"
        assert hints.start() == ".\\start.ps1"
        assert hints.logs("api.out") == "logs\\api.out"

    def test_windows_lembra_da_execution_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sem isso o Windows recusa o script e o usuário trava logo no início."""
        monkeypatch.setattr(hints.os, "name", "nt")
        assert "ExecutionPolicy Bypass" in hints.install()

    def test_nenhuma_dica_sai_vazia(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for sistema in ("posix", "nt"):
            monkeypatch.setattr(hints.os, "name", sistema)
            for valor in (
                hints.install(), hints.install("whisper"),
                hints.install(musetalk=True), hints.models(),
                hints.cli("doctor"), hints.doctor(),
                hints.start(), hints.stop(), hints.logs("x"),
            ):
                assert valor.strip(), f"dica vazia em {sistema}"


class TestImportPresente:
    """Usar `hints.` sem importar dá NameError na hora de mostrar o erro."""

    @pytest.mark.parametrize("caminho", FONTES, ids=lambda p: Path(p).name)
    def test_quem_usa_hints_importa_hints(self, caminho: str) -> None:
        fonte = (ROOT / caminho).read_text(encoding="utf-8")
        if "hints." not in fonte:
            pytest.skip("arquivo não usa dicas")
        arvore = ast.parse(fonte)
        importa = any(
            isinstance(no, ast.ImportFrom)
            and no.module == "core"
            and any(a.name == "platform_hints" for a in no.names)
            for no in ast.walk(arvore)
        )
        assert importa, f"{caminho} usa hints. sem importar platform_hints"


class TestSemComandoDeOutraPlataforma:
    """Nenhuma dica deve conter comando fixo de uma plataforma só."""

    PADROES = [
        r'"\./install\.sh',
        r'"\./start\.sh',
        r'"\./stop\.sh',
        r'"\./scripts/models\.sh',
        r'"\./scripts/doctor\.sh',
    ]

    @pytest.mark.parametrize("caminho", FONTES, ids=lambda p: Path(p).name)
    def test_sem_comando_linux_hardcoded(self, caminho: str) -> None:
        fonte = (ROOT / caminho).read_text(encoding="utf-8")
        # Ignora comentários e docstrings — o problema é o texto entregue.
        codigo = "\n".join(
            l for l in fonte.splitlines() if not l.strip().startswith("#")
        )
        achados = [p for p in self.PADROES if re.search(p, codigo)]
        assert not achados, f"{caminho} tem comando Linux fixo: {achados}"


class TestProtocoloContinuaStdlib:
    def test_protocol_nao_importa_platform_hints(self) -> None:
        """`protocol.py` roda dentro dos 3 ambientes isolados — só stdlib."""
        fonte = (ROOT / "core/worker/protocol.py").read_text(encoding="utf-8")
        assert "platform_hints" not in fonte
