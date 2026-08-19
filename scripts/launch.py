"""Gerenciador portátil de backend + frontend (principalmente Windows).

O launcher Bash continua oferecendo integração POSIX; este módulo fornece a
mesma operação sem depender de Bash, setsid, kill ou caminhos ``bin/python``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from core.config.loader import load_settings
from core.storage.paths import get_paths


def _port_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def _pid_running(pid: int) -> bool:
    """Confere existência sem encerrar um PID potencialmente reutilizado."""
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            # ERROR_ACCESS_DENIED significa que existe, mas este usuário não
            # pode consultá-lo. Qualquer outro erro é tratado como ausente.
            return ctypes.get_last_error() == 5
        try:
            wait_state = kernel32.WaitForSingleObject(handle, 0)
            # WAIT_OBJECT_0 (zero) significa encerrado. WAIT_TIMEOUT significa
            # ativo; um estado inesperado é tratado conservadoramente como
            # ativo para jamais transformar uma falha de consulta em limpeza.
            return wait_state == wait_timeout or wait_state != 0
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=15,
            )
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            process.kill()
            process.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()


def stop_running() -> int:
    paths = get_paths()
    target = paths.runtime / "manager.pid"
    stop_request = paths.runtime / "manager.stop"
    if not target.exists():
        print("Nenhuma instância registrada.")
        return 0
    try:
        pid = int(target.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        target.unlink(missing_ok=True)
        print("PID inválido removido.")
        return 1
    if not _pid_running(pid):
        target.unlink(missing_ok=True)
        stop_request.unlink(missing_ok=True)
        print("Registro obsoleto removido; a instância já havia encerrado.")
        return 0

    # Um PID pode ter sido reutilizado pelo Windows depois de uma queda. Em vez
    # de chamar taskkill cegamente, o manager confirma o pedido por este arquivo
    # e encerra a própria árvore de processos.
    stop_request.write_text(str(pid), encoding="ascii")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not target.exists() or not _pid_running(pid):
            target.unlink(missing_ok=True)
            stop_request.unlink(missing_ok=True)
            return 0
        time.sleep(0.2)

    print(
        "A instância não confirmou o encerramento em 10 segundos. "
        "Feche a janela que executa start.ps1 ou use o Gerenciador de Tarefas."
    )
    return 1


def run(*, api_only: bool, open_browser: bool, dev: bool) -> int:
    paths = get_paths()
    paths.ensure_runtime_dirs()
    settings = load_settings()
    host = settings.app.host
    api_port = settings.app.api_port
    web_port = settings.app.web_port

    if not _port_free(host, api_port):
        raise RuntimeError(
            f"A porta {api_port} já está em uso. Rode stop.ps1 ou altere a configuração."
        )

    manager_pid = paths.runtime / "manager.pid"
    stop_request = paths.runtime / "manager.stop"
    stop_request.unlink(missing_ok=True)
    manager_pid.write_text(str(os.getpid()), encoding="ascii")
    children: list[tuple[str, subprocess.Popen, object, object]] = []
    stopping = False

    def spawn(
        label: str,
        command: list[str],
        stdout_name: str,
        stderr_name: str,
        *,
        cwd: Path | None = None,
    ):
        stdout = (paths.logs / stdout_name).open("a", encoding="utf-8")
        stderr = (paths.logs / stderr_name).open("a", encoding="utf-8")
        options = {
            "cwd": str(cwd or paths.root),
            "stdout": stdout,
            "stderr": stderr,
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **options)
        except Exception:
            stdout.close()
            stderr.close()
            raise
        children.append((label, process, stdout, stderr))
        return process

    def request_stop(*_args) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    try:
        api_command = [
            sys.executable,
            "-m",
            "uvicorn",
            "apps.api.app.main:app",
            "--host",
            host,
            "--port",
            str(api_port),
            "--log-level",
            "info",
        ]
        if dev:
            # Sem --reload-dir o watcher do uvicorn varre a raiz inteira, o que
            # inclui .envs/ (~4 GB) e models/ (~7 GB). Isso satura o watcher de
            # arquivos e, no Windows, torna o hot reload inutilizável.
            api_command.append("--reload")
            for watched in ("apps", "core", "services"):
                api_command += ["--reload-dir", str(paths.root / watched)]
        api = spawn("API", api_command, "api.out", "api.err")
        if not _wait_for_port(host, api_port, 45):
            raise RuntimeError(
                "O backend não subiu em 45 segundos; veja logs/api.err."
            )

        url = f"http://{host}:{api_port}"
        if not api_only:
            web_root = paths.root / "apps" / "web"
            runner = (
                shutil.which("pnpm.cmd")
                or shutil.which("pnpm")
                or shutil.which("npm.cmd")
                or shutil.which("npm")
            )
            if runner and (web_root / "package.json").is_file():
                web_cmd = (
                    ["cmd.exe", "/c", runner, "run", "dev"]
                    if os.name == "nt" and not runner.lower().endswith(".exe")
                    else [runner, "run", "dev"]
                )
                web = spawn(
                    "interface",
                    web_cmd,
                    "web.out",
                    "web.err",
                    cwd=web_root,
                )
                if _wait_for_port(host, web_port, 60):
                    url = f"http://{host}:{web_port}"
                else:
                    print("Aviso: interface não respondeu; veja logs/web.err.")
            else:
                print(
                    "Aviso: frontend ou pnpm/npm não encontrado; "
                    "iniciando apenas a API."
                )

        print(f"Local Clone Studio: {url}")
        if open_browser:
            webbrowser.open(url)

        while not stopping:
            if stop_request.exists():
                try:
                    requested_pid = int(
                        stop_request.read_text(encoding="ascii").strip()
                    )
                except (OSError, ValueError):
                    requested_pid = -1
                if requested_pid == os.getpid():
                    stopping = True
                    continue
            for label, process, _, _ in children:
                code = process.poll()
                if code is not None:
                    print(f"{label} encerrou com código {code}.")
                    stopping = True
                    break
            time.sleep(0.4)
    finally:
        for _, process, _, _ in reversed(children):
            _terminate_tree(process)
        for _, _, stdout, stderr in children:
            stdout.close()
            stderr.close()
        manager_pid.unlink(missing_ok=True)
        stop_request.unlink(missing_ok=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.stop:
        return stop_running()
    return run(api_only=args.api_only, open_browser=not args.no_open, dev=args.dev)


if __name__ == "__main__":
    raise SystemExit(main())
