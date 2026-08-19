"""Worker stdlib-only para testes do protocolo host/subprocesso."""

from __future__ import annotations

import time

from core.worker.protocol import WorkerFailure, emit_progress, run_worker


def handle(request: dict) -> dict:
    action = request.get("action", "success")
    if action == "error":
        raise WorkerFailure(
            "falha controlada", hint="corrija a entrada", error_type="test_error"
        )
    if action == "no_result":
        raise SystemExit(0)
    if action == "slow":
        for index in range(200):
            emit_progress(index / 200, "aguardando")
            time.sleep(0.02)
    else:
        emit_progress(0.5, "metade")
    return {"value": request.get("value", 42)}


if __name__ == "__main__":
    raise SystemExit(run_worker(handle))
