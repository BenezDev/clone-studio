"""Fila de jobs local.

Uma thread executora consome a fila em série. Renders são pesados; rodar dois
ao mesmo tempo numa máquina sem GPU só faz ambos ficarem mais lentos e
aumentar o risco de estourar a RAM.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Any

from core.jobs.store import Job, JobState, JobStore, get_store
from core.jobs.instance import InstanceLock
from core.pipeline.context import PipelineCancelled, StageFailed
from core.pipeline.runner import run_pipeline
from core.storage.project import Project

logger = logging.getLogger("clone_studio.jobs")

# Mapeia a etapa em execução para o estado exibido na fila.
_STAGE_TO_STATE = {
    "prepare": JobState.PREPARING,
    "tts": JobState.TTS,
    "template": JobState.EDITING,
    "lipsync": JobState.LIPSYNC,
    "captions": JobState.CAPTIONS,
    "render": JobState.RENDERING,
    "thumbnail": JobState.RENDERING,
}

_STAGE_LABELS = {
    "Preparando": JobState.PREPARING,
    "Gerando voz": JobState.TTS,
    "Montando base": JobState.EDITING,
    "Sincronizando lábios": JobState.LIPSYNC,
    "Legendando": JobState.CAPTIONS,
    "Renderizando": JobState.RENDERING,
    "Thumbnail": JobState.RENDERING,
}


class JobQueue:
    def __init__(self, store: JobStore | None = None) -> None:
        self.store = store or get_store()
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._current: str | None = None
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._instance_lock = InstanceLock(self.store.path.parent / "backend.lock")

    # -- ciclo de vida ---------------------------------------------------

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return
            self._instance_lock.acquire()
            try:
                orphans = self.store.reset_orphans()
                if orphans:
                    logger.warning("%d job(s) órfão(s) marcados como falha", orphans)
                for pending in self.store.queued():
                    self._queue.put(pending.id)
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._loop, name="clone-studio-jobs", daemon=True
                )
                self._thread.start()
            except Exception:
                self._instance_lock.release()
                raise
            logger.info("fila de jobs iniciada")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._queue.put("")  # destrava o get()
        if self._thread:
            self._thread.join(timeout=timeout)
        if not self._thread or not self._thread.is_alive():
            self._instance_lock.release()

    # -- API -------------------------------------------------------------

    def submit(
        self,
        project_id: str,
        kind: str = "render",
        options: dict[str, Any] | None = None,
    ) -> Job:
        self.start()
        job = self.store.create(project_id, kind, options)
        self._queue.put(job.id)
        return job

    def cancel(self, job_id: str) -> bool:
        return self.store.request_cancel(job_id)

    @property
    def current_job_id(self) -> str | None:
        with self._lock:
            return self._current

    # -- execução --------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if not job_id:
                continue

            job = self.store.get(job_id)
            if job is None or job.state is JobState.CANCELLED:
                continue

            with self._lock:
                self._current = job_id
            try:
                self._execute(job)
            except Exception:  # noqa: BLE001 - a thread nunca pode morrer
                logger.exception("erro não tratado ao executar o job %s", job_id)
            finally:
                with self._lock:
                    self._current = None

    def _execute(self, job: Job) -> None:
        store = self.store

        def cancelled() -> bool:
            current = store.get(job.id)
            return self._stop.is_set() or (
                current is not None and current.state is JobState.CANCELLED
            )

        try:
            project = Project.load(job.project_id)
        except (FileNotFoundError, ValueError) as exc:
            store.update(
                job.id,
                state=JobState.FAILED,
                error={
                    "stage": "load",
                    "message": str(exc),
                    "hint": "O projeto pode ter sido apagado ou renomeado.",
                },
            )
            return

        store.update(
            job.id, state=JobState.PREPARING, progress=0.0, message="Iniciando…"
        )

        def on_progress(fraction: float, message: str) -> None:
            label = message.split(":", 1)[0].strip()
            state = _STAGE_LABELS.get(label)
            store.update(
                job.id, progress=fraction, message=message,
                state=state if state else None,
            )

        preview = job.kind == "preview" or bool(job.options.get("preview"))
        force = set(job.options.get("force_stages") or ())

        try:
            result = run_pipeline(
                project,
                preview=preview,
                force_stages=force,
                on_progress=on_progress,
                should_cancel=cancelled,
            )
        except PipelineCancelled:
            store.update(
                job.id, state=JobState.CANCELLED, message="Cancelado pelo usuário."
            )
            logger.info("job %s cancelado", job.id)
            return
        except StageFailed as exc:
            payload = exc.to_dict()
            store.update(
                job.id,
                state=JobState.FAILED,
                message=f"Falha em '{exc.stage}': {exc.message}",
                error=payload,
                log_file=str(exc.log_file) if exc.log_file else "",
            )
            logger.error("job %s falhou na etapa %s: %s", job.id, exc.stage, exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s falhou", job.id)
            store.update(
                job.id,
                state=JobState.FAILED,
                message=str(exc),
                error={
                    "stage": "unknown",
                    "message": str(exc),
                    "hint": "Consulte logs/ para o traceback completo.",
                },
            )
            return

        store.update(
            job.id,
            state=JobState.COMPLETED,
            progress=1.0,
            message="Concluído.",
            result=result.to_dict(),
        )
        logger.info(
            "job %s concluído em %.1fs -> %s",
            job.id,
            result.duration_seconds,
            result.output,
        )


_queue_instance: JobQueue | None = None
_queue_lock = threading.Lock()


def get_queue() -> JobQueue:
    global _queue_instance
    with _queue_lock:
        if _queue_instance is None:
            _queue_instance = JobQueue()
        return _queue_instance
