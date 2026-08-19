"""Contrato entre o backend e os workers de ML.

IMPORTANTE: este módulo é importado DENTRO de cada ambiente virtual isolado
(qwen-tts, musetalk, whisper...). Ele não pode importar nada além da stdlib,
porque esses ambientes têm árvores de dependências propositalmente diferentes
e incompatíveis entre si.

Protocolo:
  - O host escreve a requisição num arquivo JSON e passa o caminho por argv.
  - O worker emite JSON Lines em stdout: eventos `progress`, `log` e, por
    último, exatamente um `result` ou um `error`.
  - stderr fica livre para tracebacks e ruído de bibliotecas; o host guarda
    tudo em `logs/`.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

PROTOCOL_VERSION = 1


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def emit_progress(
    pct: float, message: str = "", stage: str | None = None
) -> None:
    _emit(
        {
            "event": "progress",
            "pct": max(0.0, min(1.0, float(pct))),
            "message": message,
            "stage": stage,
        }
    )


def emit_log(message: str, level: str = "info") -> None:
    _emit({"event": "log", "level": level, "message": message})


def emit_result(data: dict[str, Any]) -> None:
    _emit({"event": "result", "ok": True, "data": data})


def emit_error(
    error_type: str, message: str, hint: str = "", detail: str = ""
) -> None:
    _emit(
        {
            "event": "error",
            "ok": False,
            "error": {
                "type": error_type,
                "message": message,
                "hint": hint,
                "detail": detail,
            },
        }
    )


class WorkerFailure(Exception):
    """Erro esperado do worker, com dica de solução para o usuário."""

    def __init__(self, message: str, hint: str = "", error_type: str = "worker_error"):
        super().__init__(message)
        self.hint = hint
        self.error_type = error_type


def read_request(argv: list[str] | None = None) -> dict[str, Any]:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        raise SystemExit(
            "uso: python -m <worker> <caminho-do-request.json>"
        )
    with open(args[0], "r", encoding="utf-8") as handle:
        return json.load(handle)


def run_worker(handler: Callable[[dict[str, Any]], dict[str, Any]]) -> int:
    """Envelopa um handler com o tratamento de erros do protocolo.

    Uso no worker:

        if __name__ == "__main__":
            raise SystemExit(run_worker(handle))
    """
    try:
        request = read_request()
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - falha de I/O do host
        emit_error("bad_request", f"Não foi possível ler a requisição: {exc}")
        return 2

    try:
        data = handler(request)
    except WorkerFailure as exc:
        emit_error(exc.error_type, str(exc), hint=exc.hint,
                   detail=traceback.format_exc())
        return 1
    except ImportError as exc:
        emit_error(
            "missing_dependency",
            f"Dependência ausente no ambiente do worker: {exc}",
            hint=(
                "O ambiente virtual desta engine parece incompleto. "
                "Reinstale o ambiente desta engine (veja o COMO-RODAR.md)."
            ),
            detail=traceback.format_exc(),
        )
        return 1
    except MemoryError:
        emit_error(
            "out_of_memory",
            "Memória insuficiente para executar esta etapa.",
            hint=(
                "Use um modelo menor (perfil Low VRAM), reduza o batch_size ou "
                "feche outros programas. Verifique também se há swap ativo."
            ),
            detail=traceback.format_exc(),
        )
        return 1
    except Exception as exc:  # noqa: BLE001 - fronteira do processo
        emit_error(
            type(exc).__name__,
            str(exc) or "Falha não identificada no worker.",
            detail=traceback.format_exc(),
        )
        return 1

    emit_result(data if isinstance(data, dict) else {"value": data})
    return 0
