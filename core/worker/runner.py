"""Lado host do protocolo de workers.

Cada engine de ML roda como um subprocesso curto, no seu próprio ambiente
virtual. Isso resolve quatro problemas de uma vez:

  * isolamento de dependências (torch 2.0 + mmcv do MuseTalk não convivem com
    transformers 4.57 do Qwen3-TTS no mesmo interpretador);
  * liberação de memória — ao terminar o processo, a RAM/VRAM volta ao SO,
    sem depender de `del model` e coleta de lixo;
  * o backend FastAPI nunca importa torch, então sobe em menos de um segundo;
  * erros ficam observáveis: comando exato, exit code e stderr completo.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.storage.paths import get_paths

ProgressCallback = Callable[[float, str], None]
LogCallback = Callable[[str, str], None]


class EngineNotInstalled(RuntimeError):
    """O ambiente virtual da engine não existe."""


class WorkerCancelled(RuntimeError):
    """O worker foi encerrado por cancelamento cooperativo do pipeline."""


@dataclass
class WorkerError(RuntimeError):
    """Falha de worker com contexto suficiente para diagnóstico real."""

    stage: str
    message: str
    error_type: str = "worker_error"
    hint: str = ""
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    stderr_tail: str = ""
    detail: str = ""
    log_file: Path | None = None

    def __str__(self) -> str:
        parts = [f"[{self.stage}] {self.message}"]
        if self.hint:
            parts.append(f"Provável solução: {self.hint}")
        if self.exit_code is not None:
            parts.append(f"exit code: {self.exit_code}")
        if self.command:
            parts.append("comando: " + " ".join(self.command))
        if self.stderr_tail:
            parts.append("stderr (final):\n" + self.stderr_tail)
        if self.log_file:
            parts.append(f"log completo: {self.log_file}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "type": self.error_type,
            "message": self.message,
            "hint": self.hint,
            "command": self.command,
            "exit_code": self.exit_code,
            "stderr_tail": self.stderr_tail,
            "log_file": str(self.log_file) if self.log_file else None,
        }


@dataclass
class WorkerResult:
    data: dict[str, Any]
    duration_seconds: float
    log_file: Path
    command: list[str]


_STDERR_KEEP_LINES = 60

# Sintomas comuns em stderr -> dica acionável. Evita o inútil "Generation failed".
_HINT_PATTERNS: list[tuple[str, str]] = [
    (
        "CUDA out of memory",
        "VRAM insuficiente. Use um modelo menor, reduza batch_size ou ative "
        "CPU offload em config/local.yaml.",
    ),
    (
        "No module named 'torch'",
        "O ambiente da engine está sem torch. Reinstale com "
        "o instalador para a engine correspondente.",
    ),
    (
        "No module named",
        "Dependência ausente neste ambiente virtual. Reinstale a engine.",
    ),
    (
        "Killed",
        "O processo foi encerrado pelo sistema — quase sempre falta de RAM. "
        "Use o modelo menor ou aumente o swap.",
    ),
    (
        "ffmpeg",
        "Verifique se o FFmpeg está instalado e acessível no PATH "
        "(`ffmpeg -version`).",
    ),
    (
        "HTTPSConnectionPool",
        "Falha de rede ao baixar pesos. Rode `./scripts/models.sh install <id>` "
        "com conexão ativa; depois disso o pipeline funciona offline.",
    ),
    (
        "does not exist",
        "Arquivo de entrada não encontrado. Confira os caminhos do projeto.",
    ),
]


def _guess_hint(stderr: str) -> str:
    for needle, hint in _HINT_PATTERNS:
        if needle.lower() in stderr.lower():
            return hint
    return ""


def env_python(env_name: str) -> Path:
    """Interpretador do ambiente da engine, com fallback para o atual."""
    paths = get_paths()
    if env_name in {"backend", "", None}:
        candidate = paths.env_python("backend")
        return candidate if candidate.exists() else Path(sys.executable)
    return paths.env_python(env_name)


def engine_available(env_name: str) -> bool:
    return env_python(env_name).exists()


def run_worker(
    *,
    env: str,
    module: str,
    request: dict[str, Any],
    stage: str,
    on_progress: ProgressCallback | None = None,
    on_log: LogCallback | None = None,
    timeout: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    extra_env: dict[str, str] | None = None,
    log_dir: Path | None = None,
) -> WorkerResult:
    """Executa um worker e devolve o payload de `result`.

    Levanta `WorkerError` com contexto completo em qualquer falha.
    """
    paths = get_paths()
    python = env_python(env)
    if not python.exists():
        raise EngineNotInstalled(
            f"Ambiente '{env}' não instalado (esperado em {python}). "
            f"Rode ./install.sh para criá-lo."
        )

    run_id = f"{stage}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    target_log_dir = log_dir or paths.logs
    target_log_dir.mkdir(parents=True, exist_ok=True)

    request_file = target_log_dir / f"{run_id}.request.json"
    request_file.write_text(
        json.dumps(request, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log_file = target_log_dir / f"{run_id}.log"

    command = [str(python), "-m", module, str(request_file)]

    process_env = os.environ.copy()
    process_env["PYTHONPATH"] = str(paths.root) + os.pathsep + process_env.get(
        "PYTHONPATH", ""
    )
    process_env["PYTHONUNBUFFERED"] = "1"
    process_env["CLONE_STUDIO_ROOT"] = str(paths.root)
    # Pesos ficam sempre sob models/, nunca no ~/.cache do usuário.
    # TORCH_HOME importa em especial: bibliotecas de terceiros chamam
    # `torch.hub.load_url` e gravariam em ~/.cache/torch por padrão.
    process_env.setdefault("HF_HOME", str(paths.models / "_hf"))
    process_env.setdefault("HUGGINGFACE_HUB_CACHE", str(paths.models / "_hf" / "hub"))
    process_env.setdefault("TORCH_HOME", str(paths.models / "_torch"))
    # Sem telemetria de terceiros.
    process_env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    process_env["DO_NOT_TRACK"] = "1"
    if extra_env:
        process_env.update(extra_env)

    started = time.monotonic()
    result_payload: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None
    stderr_lines: list[str] = []

    with open(log_file, "w", encoding="utf-8") as log_handle:
        log_handle.write(f"$ {' '.join(command)}\n\n")
        log_handle.flush()

        popen_options: dict[str, Any] = {}
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=process_env,
            cwd=str(paths.root),
            **popen_options,
        )

        def stop_process() -> None:
            if process.poll() is not None:
                return
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
                return
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            except (OSError, ValueError, subprocess.SubprocessError):
                process.kill()
                process.wait()

        def drain_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_lines.append(line.rstrip("\n"))
                del stderr_lines[:-400]
                log_handle.write(line)

        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stderr_thread.start()

        assert process.stdout is not None
        stdout_lines: queue.Queue[str | None] = queue.Queue()

        def drain_stdout() -> None:
            assert process.stdout is not None
            for raw in process.stdout:
                stdout_lines.put(raw)
            stdout_lines.put(None)

        stdout_thread = threading.Thread(target=drain_stdout, daemon=True)
        stdout_thread.start()

        stdout_done = False
        deadline = started + timeout if timeout is not None else None
        try:
            while not stdout_done or process.poll() is None or not stdout_lines.empty():
                if should_cancel and should_cancel():
                    stop_process()
                    raise WorkerCancelled(f"Worker da etapa '{stage}' cancelado.")
                if deadline is not None and time.monotonic() >= deadline:
                    stop_process()
                    raise WorkerError(
                        stage=stage,
                        message=f"A etapa excedeu o tempo limite de {timeout}s.",
                        error_type="timeout",
                        hint=(
                            "Em CPU essa etapa é lenta. Aumente o timeout na config ou "
                            "reduza a duração do vídeo/roteiro."
                        ),
                        command=command,
                        log_file=log_file,
                    )
                try:
                    raw_line = stdout_lines.get(timeout=0.1)
                except queue.Empty:
                    continue
                if raw_line is None:
                    stdout_done = True
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Worker imprimindo texto solto: preserva no log.
                    log_handle.write(f"[stdout] {line}\n")
                    continue

                kind = event.get("event")
                if kind == "progress":
                    log_handle.write(
                        f"[progress] {event.get('pct', 0):.2f} "
                        f"{event.get('message', '')}\n"
                    )
                    if on_progress:
                        on_progress(
                            float(event.get("pct", 0.0)),
                            str(event.get("message", "")),
                        )
                elif kind == "log":
                    log_handle.write(
                        f"[{event.get('level', 'info')}] {event.get('message', '')}\n"
                    )
                    if on_log:
                        on_log(
                            str(event.get("level", "info")),
                            str(event.get("message", "")),
                        )
                elif kind == "result":
                    result_payload = event.get("data") or {}
                elif kind == "error":
                    error_payload = event.get("error") or {}

            exit_code = process.wait()
        finally:
            if process.poll() is None:
                stop_process()
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
            log_handle.flush()

    duration = time.monotonic() - started
    stderr_tail = "\n".join(stderr_lines[-_STDERR_KEEP_LINES:])

    if error_payload is not None:
        raise WorkerError(
            stage=stage,
            message=error_payload.get("message", "Falha no worker."),
            error_type=error_payload.get("type", "worker_error"),
            hint=error_payload.get("hint") or _guess_hint(stderr_tail),
            command=command,
            exit_code=exit_code,
            stderr_tail=stderr_tail,
            detail=error_payload.get("detail", ""),
            log_file=log_file,
        )

    if exit_code != 0:
        raise WorkerError(
            stage=stage,
            message=(
                f"O worker '{module}' terminou com código {exit_code} sem "
                "reportar um erro estruturado."
            ),
            error_type="process_failed",
            hint=_guess_hint(stderr_tail)
            or "Consulte o log completo para o traceback original.",
            command=command,
            exit_code=exit_code,
            stderr_tail=stderr_tail,
            log_file=log_file,
        )

    if result_payload is None:
        raise WorkerError(
            stage=stage,
            message=f"O worker '{module}' terminou sem produzir resultado.",
            error_type="no_result",
            hint="Isso indica bug no worker; o log traz a saída bruta.",
            command=command,
            exit_code=exit_code,
            stderr_tail=stderr_tail,
            log_file=log_file,
        )

    return WorkerResult(
        data=result_payload,
        duration_seconds=duration,
        log_file=log_file,
        command=command,
    )
