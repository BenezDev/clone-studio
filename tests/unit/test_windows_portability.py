"""Regressões do launcher e dos entrypoints usados no Windows."""

from __future__ import annotations

import ctypes
from pathlib import Path
from types import SimpleNamespace


def test_launcher_inicia_frontend_no_diretorio_correto(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.launch as launcher
    from core.storage.paths import Paths

    paths = Paths(tmp_path)
    web_root = tmp_path / "apps" / "web"
    web_root.mkdir(parents=True)
    (web_root / "package.json").write_text("{}", encoding="utf-8")

    created: list[tuple[list[str], dict]] = []

    class FakeProcess:
        _next_pid = 1000

        def __init__(self, command: list[str], **options) -> None:
            self.command = command
            self.options = options
            self.pid = FakeProcess._next_pid
            FakeProcess._next_pid += 1
            created.append((command, options))

        def poll(self) -> int:
            return 0

    settings = SimpleNamespace(
        app=SimpleNamespace(host="127.0.0.1", api_port=8756, web_port=3000)
    )
    monkeypatch.setattr(launcher, "get_paths", lambda: paths)
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)
    monkeypatch.setattr(launcher, "_port_free", lambda *_: True)
    monkeypatch.setattr(launcher, "_wait_for_port", lambda *_: True)
    monkeypatch.setattr(launcher, "_terminate_tree", lambda *_: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(
        launcher.shutil,
        "which",
        lambda name: r"C:\Program Files\nodejs\pnpm.cmd"
        if name == "pnpm.cmd"
        else None,
    )
    monkeypatch.setattr(launcher.signal, "signal", lambda *_: None)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda *_: False)
    monkeypatch.setattr(launcher.time, "sleep", lambda *_: None)

    assert launcher.run(api_only=False, open_browser=False, dev=False) == 0
    assert len(created) == 2
    assert created[0][1]["cwd"] == str(tmp_path)
    assert created[1][0][-2:] == ["run", "dev"]
    assert created[1][1]["cwd"] == str(web_root)
    assert not (paths.runtime / "manager.pid").exists()


def test_scripts_powershell_independem_do_diretorio_atual() -> None:
    root = Path(__file__).resolve().parents[2]
    scripts = [
        root / "install.ps1",
        root / "start.ps1",
        root / "stop.ps1",
        root / "scripts" / "clone-studio.ps1",
        root / "scripts" / "doctor.ps1",
        root / "scripts" / "models.ps1",
    ]
    for script in scripts:
        source = script.read_text(encoding="utf-8")
        assert "$PSScriptRoot" in source, script
        assert "$env:CLONE_STUDIO_ROOT" in source, script
        assert "$env:PYTHONPATH" in source, script


def test_stop_nao_mata_pid_reutilizado(monkeypatch, tmp_path: Path) -> None:
    import scripts.launch as launcher
    from core.storage.paths import Paths

    paths = Paths(tmp_path)
    paths.ensure_runtime_dirs()
    (paths.runtime / "manager.pid").write_text("4242", encoding="ascii")
    monkeypatch.setattr(launcher, "get_paths", lambda: paths)
    monkeypatch.setattr(launcher, "_pid_running", lambda _: False)

    assert launcher.stop_running() == 0
    assert not (paths.runtime / "manager.pid").exists()
    assert not (paths.runtime / "manager.stop").exists()


def test_consulta_de_pid_windows_usa_api_somente_leitura(monkeypatch) -> None:
    import scripts.launch as launcher

    calls: list[tuple[str, tuple]] = []

    class NativeFunction:
        def __init__(self, name: str, result: int) -> None:
            self.name = name
            self.result = result

        def __call__(self, *args):
            calls.append((self.name, args))
            return self.result

    class Kernel32:
        OpenProcess = NativeFunction("OpenProcess", 123)
        WaitForSingleObject = NativeFunction("WaitForSingleObject", 0x102)
        CloseHandle = NativeFunction("CloseHandle", 1)

    monkeypatch.setattr(launcher.os, "name", "nt")
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: Kernel32(), raising=False
    )
    monkeypatch.setattr(
        launcher.os,
        "kill",
        lambda *_: (_ for _ in ()).throw(AssertionError("os.kill não é seguro")),
    )

    assert launcher._pid_running(4242)
    assert [name for name, _ in calls] == [
        "OpenProcess",
        "WaitForSingleObject",
        "CloseHandle",
    ]
    calls.clear()
    Kernel32.WaitForSingleObject.result = 0
    assert not launcher._pid_running(4242)
    assert calls[-1][0] == "CloseHandle"


def test_installer_windows_usa_wheel_pytorch_existente() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "install.ps1"
    ).read_text(encoding="utf-8")
    assert "whl/cu126" in source
    assert "whl/cu124" not in source
    assert "$Download = [bool]$Yes" in source


def test_diagnostico_mostra_comandos_windows(monkeypatch) -> None:
    """As dicas do diagnóstico saem no formato do Windows.

    O alvo do monkeypatch é `core.platform_hints`, que passou a ser a fonte
    única desses comandos — `checks` agora só delega.

    O comando de instalação inclui `-ExecutionPolicy Bypass` de propósito: a
    dica é lida na tela e colada num PowerShell **novo**, onde a política volta
    ao padrão restritivo e o script seria recusado sem esse prefixo.
    """
    import core.diagnostics.checks as checks
    from core import platform_hints as hints

    monkeypatch.setattr(hints.os, "name", "nt")

    instalar = checks._install_hint("whisper")
    assert "ExecutionPolicy Bypass" in instalar
    assert instalar.endswith(".\\install.ps1 -Only whisper")

    musetalk = checks._install_hint("musetalk", musetalk=True)
    assert musetalk.endswith(".\\install.ps1 -WithMuseTalk")

    assert checks._model_hint("modelo") == (
        ".\\scripts\\models.ps1 install modelo"
    )


def test_detector_windows_reconhece_o_python_em_execucao(monkeypatch) -> None:
    import sys

    import core.hardware.detect as detect

    monkeypatch.setattr(detect.platform, "system", lambda: "Windows")
    monkeypatch.setattr(detect.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        detect,
        "_run",
        lambda command, timeout=15: (
            "Python 3.12.0" if command[0] == sys.executable else None
        ),
    )
    report = detect.HardwareReport()
    detect._detect_tools(report)

    python = report.tool("python")
    assert python is not None
    assert python.found
    assert python.path == sys.executable
    assert python.version == "3.12.0"


def test_ffmpeg_subtitle_path_escaping_windows() -> None:
    from core.media.ffmpeg import _escape_subtitle_path

    # Caminho com drive letter e backslashes
    win_path = Path("C:/CloneStudio/cache/test.ass")
    escaped = _escape_subtitle_path(win_path)
    # Não pode conter backslashes não-escapadas e precisa ter dois pontos escapados
    assert r"\:" in escaped
    assert "\\" not in escaped.replace(r"\:", "")


def test_batch_scripts_usam_bypass_e_dp0() -> None:
    root = Path(__file__).resolve().parents[2]
    bats = [
        root / "install.bat",
        root / "start.bat",
        root / "stop.bat",
        root / "doctor.bat",
        root / "scripts" / "clone-studio.bat",
    ]
    for bat in bats:
        assert bat.exists(), f"{bat} não existe"
        content = bat.read_text(encoding="utf-8")
        assert "-ExecutionPolicy Bypass" in content, bat
        assert "%~dp0" in content, bat

