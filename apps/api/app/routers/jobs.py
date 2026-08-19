"""Rotas da fila de render."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from core.jobs.queue import get_queue
from core.jobs.store import JobState, get_store

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def get_jobs(
    project_id: str | None = None, state: str | None = None, limit: int = 50
) -> dict[str, Any]:
    parsed_state = None
    if state:
        try:
            parsed_state = JobState(state)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Estado inválido: {state}. "
                       f"Válidos: {[s.value for s in JobState]}",
            ) from exc

    jobs = get_store().list(
        project_id=project_id, state=parsed_state, limit=min(limit, 200)
    )
    return {
        "jobs": [j.to_dict() for j in jobs],
        "current": get_queue().current_job_id,
    }


@router.get("/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = get_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return job.to_dict()


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    job = get_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    cancelled = get_queue().cancel(job_id)
    return {
        "cancelled": cancelled,
        "detail": (
            "O cancelamento é aplicado entre etapas; a etapa em andamento "
            "termina antes de parar."
            if cancelled
            else "O job já havia terminado."
        ),
    }


@router.get("/{job_id}/stream")
async def stream_job(job_id: str) -> StreamingResponse:
    """Server-Sent Events com o progresso do job.

    Evita polling agressivo da interface durante renders longos.
    """
    store = get_store()
    if store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    async def events():
        last = None
        while True:
            job = store.get(job_id)
            if job is None:
                break
            payload = job.to_dict()
            if payload != last:
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                last = payload
            if job.state.terminal:
                break
            await asyncio.sleep(0.8)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{job_id}/log")
def get_job_log(job_id: str, tail: int = 400) -> dict[str, Any]:
    from pathlib import Path

    job = get_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    if not job.log_file:
        return {"log": "", "detail": "Este job não gerou log de subprocesso."}

    path = Path(job.log_file)
    if not path.exists():
        return {"log": "", "detail": f"Log não encontrado: {path}"}

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"log": "\n".join(lines[-tail:]), "path": str(path)}
