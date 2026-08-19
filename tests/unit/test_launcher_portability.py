"""Portabilidade dos launchers e wrappers (POSIX + Windows).

Estes testes existem por bugs reais encontrados numa revisão cross-platform.
Nenhum deles exige Windows: todos verificam invariantes que, quando quebrados,
falham em **alguma** plataforma — e vários já falhavam no Linux também.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

POWERSHELL_SCRIPTS = [
    ROOT / "install.ps1",
    ROOT / "start.ps1",
    ROOT / "stop.ps1",
    ROOT / "scripts" / "doctor.ps1",
    ROOT / "scripts" / "models.ps1",
    ROOT / "scripts" / "clone-studio.ps1",
]

# Wrappers que invocam `python -m <pacote>` e por isso dependem de a raiz do
# projeto estar no sys.path.
MODULE_WRAPPERS = [
    ROOT / "scripts" / "doctor.ps1",
    ROOT / "scripts" / "clone-studio.ps1",
    ROOT / "start.ps1",
    ROOT / "stop.ps1",
]


class TestPythonPathIndependenteDoCwd:
    """`python -m apps.cli.main` só resolve com a raiz no sys.path.

    O `-m` do Python coloca no sys.path apenas o diretório atual. Sem
    PYTHONPATH, todo wrapper falha com ModuleNotFoundError quando o usuário o
    chama de outra pasta — que é o caso normal de uso.
    """

    def test_modulo_falha_sem_pythonpath(self, tmp_path: Path) -> None:
        """Documenta o modo de falha que os wrappers precisam evitar."""
        proc = subprocess.run(
            [sys.executable, "-m", "apps.cli.main", "--help"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        )
        assert proc.returncode != 0
        assert "No module named" in (proc.stderr + proc.stdout)

    def test_modulo_resolve_com_pythonpath(self, tmp_path: Path) -> None:
        import os

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, "-c", "import apps.cli.main; print('ok')"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        assert "ok" in proc.stdout

    def test_lib_sh_exporta_pythonpath(self) -> None:
        conteudo = (ROOT / "scripts" / "lib.sh").read_text(encoding="utf-8")
        assert "export PYTHONPATH=" in conteudo
        assert "CS_ROOT" in conteudo

    @pytest.mark.parametrize(
        "script", MODULE_WRAPPERS, ids=lambda p: p.name
    )
    def test_wrappers_ps1_definem_pythonpath(self, script: Path) -> None:
        conteudo = script.read_text(encoding="utf-8")
        assert "$env:PYTHONPATH" in conteudo, (
            f"{script.name} chama 'python -m' sem garantir PYTHONPATH; "
            "vai falhar se invocado de fora da raiz do projeto."
        )


class TestCompatibilidadePowerShell51:
    """Windows 10/11 trazem o Windows PowerShell 5.1 por padrão.

    Usar um cmdlet ou operador exclusivo do PowerShell 7 quebra a instalação
    na configuração mais comum do público-alvo.
    """

    # Cmdlets/operadores introduzidos no PowerShell 6/7.
    APENAS_PS7 = [
        r"\bJoin-String\b",
        r"\bForEach-Object\s+-Parallel\b",
        r"\bGet-Error\b",
        r"\bTest-Json\b",
        r"\bConvertFrom-Json\s+-AsHashtable\b",
        r"\?\?=",          # operador de atribuição de coalescência nula
        r"[^|]\|\|[^|]",   # operador pipeline-chain ||
        r"[^&]&&[^&]",     # operador pipeline-chain &&
    ]

    @pytest.mark.parametrize(
        "script", POWERSHELL_SCRIPTS, ids=lambda p: p.name
    )
    def test_sem_construcoes_de_ps7(self, script: Path) -> None:
        conteudo = script.read_text(encoding="utf-8")
        for padrao in self.APENAS_PS7:
            encontrado = re.search(padrao, conteudo)
            assert not encontrado, (
                f"{script.name} usa '{encontrado.group(0).strip()}', que não "
                "existe no Windows PowerShell 5.1."
            )


class TestScriptsPowerShellExistem:
    @pytest.mark.parametrize(
        "script", POWERSHELL_SCRIPTS, ids=lambda p: p.name
    )
    def test_existe_e_nao_esta_vazio(self, script: Path) -> None:
        assert script.exists(), f"{script} ausente"
        assert script.stat().st_size > 0

    @pytest.mark.parametrize(
        "script", POWERSHELL_SCRIPTS, ids=lambda p: p.name
    )
    def test_usa_caminho_do_proprio_script(self, script: Path) -> None:
        """Nunca depender do diretório atual para achar a raiz."""
        conteudo = script.read_text(encoding="utf-8")
        assert "$PSScriptRoot" in conteudo or "MyCommand.Path" in conteudo

    @pytest.mark.parametrize(
        "script", POWERSHELL_SCRIPTS, ids=lambda p: p.name
    )
    def test_python_do_venv_no_layout_windows(self, script: Path) -> None:
        """No Windows o interpretador fica em Scripts\\python.exe, não bin/."""
        conteudo = script.read_text(encoding="utf-8")
        if ".envs" not in conteudo:
            pytest.skip("script não referencia ambientes virtuais")
        assert "Scripts\\python.exe" in conteudo
        assert "bin/python" not in conteudo


class TestInstallPs1:
    def test_toda_instalacao_verifica_exit_code(self) -> None:
        """Uma falha no meio da sequência não pode ser mascarada.

        `$ErrorActionPreference = 'Stop'` não afeta o código de saída de
        comandos nativos: sem checagem explícita, um `uv pip install` que falha
        passa despercebido porque o comando seguinte devolve 0.
        """
        linhas = (ROOT / "install.ps1").read_text(encoding="utf-8").splitlines()
        pendentes: list[str] = []
        for indice, linha in enumerate(linhas):
            if not re.search(r"^\s*&\s*(uv|\$Muse|\$Python|\$Backend)", linha):
                continue
            janela = "\n".join(linhas[indice + 1 : indice + 4])
            # Um comentário logo acima marcando a intenção isenta a checagem —
            # há casos legítimos, como o doctor, cujo != 0 é informativo.
            anterior = "\n".join(linhas[max(0, indice - 3) : indice])
            if "$LASTEXITCODE ignorado de propósito" in anterior:
                continue
            if "$LASTEXITCODE" not in janela:
                pendentes.append(f"linha {indice + 1}: {linha.strip()}")
        assert not pendentes, (
            "comandos nativos sem checagem de $LASTEXITCODE:\n"
            + "\n".join(pendentes)
        )

    def test_nao_baixa_pesos_sem_confirmacao(self) -> None:
        conteudo = (ROOT / "install.ps1").read_text(encoding="utf-8")
        assert "Read-Host" in conteudo, (
            "o instalador precisa perguntar antes de baixar modelos"
        )
        assert "--essential" in conteudo


class TestLaunchPy:
    """`scripts/launch.py` é o launcher portátil usado pelo start.ps1."""

    def test_frontend_roda_no_diretorio_do_package_json(self) -> None:
        """`pnpm run dev` com cwd na raiz falha: não há package.json lá."""
        conteudo = (ROOT / "scripts" / "launch.py").read_text(encoding="utf-8")
        assert "cwd=web_root" in conteudo, (
            "o spawn do frontend precisa usar apps/web como cwd"
        )
        assert not (ROOT / "package.json").exists(), (
            "se existir package.json na raiz, este teste perde o sentido"
        )

    def test_reload_limita_diretorios_observados(self) -> None:
        """Sem --reload-dir o watcher varre .envs/ (~4 GB) e models/ (~7 GB)."""
        conteudo = (ROOT / "scripts" / "launch.py").read_text(encoding="utf-8")
        if "--reload" not in conteudo:
            pytest.skip("launcher não oferece modo dev")
        assert "--reload-dir" in conteudo

    def test_encerra_arvore_de_processos_nas_duas_plataformas(self) -> None:
        conteudo = (ROOT / "scripts" / "launch.py").read_text(encoding="utf-8")
        assert "taskkill" in conteudo, "Windows precisa de taskkill /T"
        assert "killpg" in conteudo, "POSIX precisa matar o process group"
        assert "start_new_session" in conteudo
        assert "CREATE_NEW_PROCESS_GROUP" in conteudo

    def test_importa_sem_dependencia_de_ml(self) -> None:
        """O launcher roda no ambiente do backend, que não tem torch."""
        conteudo = (ROOT / "scripts" / "launch.py").read_text(encoding="utf-8")
        for proibido in ("import torch", "import transformers", "import mmcv"):
            assert proibido not in conteudo


class TestStopSh:
    """A rede de segurança do stop.sh precisa casar com a linha de comando real."""

    # Como o Vite é realmente lançado pelo pnpm/npm:
    CMDLINE_VITE = (
        "node /home/user/Área de trabalho/Clone Studio/apps/web/"
        "node_modules/.bin/../vite/bin/vite.js"
    )
    CMDLINE_UVICORN = (
        "/proj/.envs/backend/bin/python -m uvicorn apps.api.app.main:app "
        "--host 127.0.0.1 --port 8756"
    )

    def _padroes(self) -> list[str]:
        conteudo = (ROOT / "stop.sh").read_text(encoding="utf-8")
        linha = next(
            l for l in conteudo.splitlines() if l.strip().startswith("for pattern in")
        )
        return re.findall(r'"([^"]+)"', linha)

    def test_padrao_casa_com_o_vite_real(self) -> None:
        """'apps/web' vem ANTES de 'vite' no caminho — a ordem importa.

        Um padrão 'vite.*apps/web' nunca casa e deixa a porta 3000 ocupada.
        """
        padroes = self._padroes()
        assert any(
            re.search(p, self.CMDLINE_VITE) for p in padroes
        ), f"nenhum padrão de {padroes} casa com:\n  {self.CMDLINE_VITE}"

    def test_padrao_casa_com_o_uvicorn_real(self) -> None:
        padroes = self._padroes()
        assert any(re.search(p, self.CMDLINE_UVICORN) for p in padroes)

    def test_ignora_o_proprio_processo(self) -> None:
        """`pgrep -f` casa com o próprio stop.sh, cuja cmdline contém o padrão."""
        conteudo = (ROOT / "stop.sh").read_text(encoding="utf-8")
        assert '"$$"' in conteudo or "$$" in conteudo, (
            "stop.sh precisa se excluir do pgrep -f, senão mata a si mesmo"
        )


class TestCaminhosComEspacosEAcentos:
    """A raiz real deste projeto é 'Área de trabalho/Clone Studio'."""

    def test_raiz_atual_tem_espaco_ou_acento(self) -> None:
        texto = str(ROOT)
        assert " " in texto or any(ord(c) > 127 for c in texto), (
            "este ambiente deveria exercitar caminhos não triviais"
        )

    def test_subprocesso_recebe_caminho_com_espaco(self, tmp_path: Path) -> None:
        import os

        alvo = tmp_path / "pasta com espaço e acentuação"
        alvo.mkdir()
        arquivo = alvo / "arquivo.txt"
        arquivo.write_text("ok", encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys,pathlib; print(pathlib.Path(sys.argv[1]).read_text())",
             str(arquivo)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        assert "ok" in proc.stdout

    def test_paths_do_projeto_resolvem_com_acento(self) -> None:
        from core.storage.paths import get_paths

        paths = get_paths()
        assert paths.root.exists()
        assert paths.models.parent == paths.root
