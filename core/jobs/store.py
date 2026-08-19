"""Persistência de jobs em SQLite.

Render demora. O usuário fecha a aba, reinicia o backend, volta no dia
seguinte — e o histórico precisa continuar lá. SQLite dá isso sem exigir
Redis nem nenhum serviço extra rodando.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from core.storage.paths import get_paths


class JobState(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    TTS = "tts"
    LIPSYNC = "lipsync"
    CAPTIONS = "captions"
    EDITING = "editing"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}


ACTIVE_STATES = tuple(s.value for s in JobState if not s.terminal)
RUNNING_STATES = tuple(s for s in ACTIVE_STATES if s != JobState.QUEUED.value)
TERMINAL_STATES = tuple(s.value for s in JobState if s.terminal)


@dataclass
class Job:
    id: str
    project_id: str
    kind: str                      # render | preview | tts | ...
    state: JobState = JobState.QUEUED
    progress: float = 0.0
    message: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: dict[str, Any] | None = None
    result: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    log_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "kind": self.kind,
            "state": self.state.value,
            "progress": round(self.progress, 4),
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
            "options": self.options,
            "log_file": self.log_file,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    state        TEXT NOT NULL,
    progress     REAL NOT NULL DEFAULT 0,
    message      TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    started_at   TEXT NOT NULL DEFAULT '',
    finished_at  TEXT NOT NULL DEFAULT '',
    error        TEXT,
    result       TEXT NOT NULL DEFAULT '{}',
    options      TEXT NOT NULL DEFAULT '{}',
    log_file     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, database: Path | None = None) -> None:
        self.path = database or get_paths().jobs_db
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._local = threading.local()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.path, check_same_thread=False, timeout=15.0
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    # -- escrita ---------------------------------------------------------

    def create(
        self, project_id: str, kind: str, options: dict[str, Any] | None = None
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            project_id=project_id,
            kind=kind,
            created_at=_now(),
            options=options or {},
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, project_id, kind, state, created_at, options)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    job.id,
                    job.project_id,
                    job.kind,
                    job.state.value,
                    job.created_at,
                    json.dumps(job.options, ensure_ascii=False),
                ),
            )
        return job

    def update(
        self,
        job_id: str,
        *,
        state: JobState | None = None,
        progress: float | None = None,
        message: str | None = None,
        error: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        log_file: str | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []

        if state is not None:
            assignments.append("state = ?")
            values.append(state.value)
            if state is JobState.PREPARING:
                assignments.append("started_at = ?")
                values.append(_now())
            if state.terminal:
                assignments.append("finished_at = ?")
                values.append(_now())
        if progress is not None:
            assignments.append("progress = ?")
            values.append(max(0.0, min(1.0, progress)))
        if message is not None:
            assignments.append("message = ?")
            values.append(message)
        if error is not None:
            assignments.append("error = ?")
            values.append(json.dumps(error, ensure_ascii=False))
        if result is not None:
            assignments.append("result = ?")
            values.append(json.dumps(result, ensure_ascii=False))
        if log_file is not None:
            assignments.append("log_file = ?")
            values.append(log_file)

        if not assignments:
            return

        values.extend((job_id, *TERMINAL_STATES))
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?"
                " AND state NOT IN (?, ?, ?)",
                values,
            )

    def request_cancel(self, job_id: str) -> bool:
        """Marca o job como cancelado se ele ainda não terminou."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET state = ?, finished_at = ?, message = ?"
                " WHERE id = ? AND state NOT IN (?, ?, ?)",
                (
                    JobState.CANCELLED.value,
                    _now(),
                    "cancelado pelo usuário",
                    job_id,
                    JobState.COMPLETED.value,
                    JobState.FAILED.value,
                    JobState.CANCELLED.value,
                ),
            )
            return cursor.rowcount > 0

    # -- leitura ---------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def list(
        self,
        *,
        project_id: str | None = None,
        state: JobState | None = None,
        limit: int = 100,
    ) -> list[Job]:
        query = "SELECT * FROM jobs"
        clauses: list[str] = []
        values: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            values.append(project_id)
        if state:
            clauses.append("state = ?")
            values.append(state.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        values.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [_row_to_job(r) for r in rows]

    def active(self) -> list[Job]:
        placeholders = ", ".join("?" * len(ACTIVE_STATES))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM jobs WHERE state IN ({placeholders})"
                " ORDER BY created_at ASC",
                ACTIVE_STATES,
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def queued(self) -> list[Job]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY created_at ASC",
                (JobState.QUEUED.value,),
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def reset_orphans(self) -> int:
        """Jobs que ficaram 'rodando' quando o backend caiu.

        Chamado na subida: um job em estado ativo sem processo por trás é
        mentira, e mostrar isso na fila confunde mais que ajuda.
        """
        placeholders = ", ".join("?" * len(RUNNING_STATES))
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE jobs SET state = ?, finished_at = ?, error = ?"
                f" WHERE state IN ({placeholders})",
                (
                    JobState.FAILED.value,
                    _now(),
                    json.dumps(
                        {
                            "type": "interrupted",
                            "message": "O backend foi reiniciado durante a execução.",
                            "hint": "Rode novamente — as etapas já concluídas "
                                    "serão reaproveitadas.",
                        },
                        ensure_ascii=False,
                    ),
                    *RUNNING_STATES,
                ),
            )
            return cursor.rowcount

    def purge_older_than(self, days: int) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(
            timespec="seconds"
        )
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM jobs WHERE state IN (?, ?, ?) AND created_at < ?",
                (
                    JobState.COMPLETED.value,
                    JobState.FAILED.value,
                    JobState.CANCELLED.value,
                    cutoff_iso,
                ),
            )
            return cursor.rowcount


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        project_id=row["project_id"],
        kind=row["kind"],
        state=JobState(row["state"]),
        progress=row["progress"],
        message=row["message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error=json.loads(row["error"]) if row["error"] else None,
        result=json.loads(row["result"] or "{}"),
        options=json.loads(row["options"] or "{}"),
        log_file=row["log_file"],
    )


_store: JobStore | None = None
_store_lock = threading.Lock()


def get_store() -> JobStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = JobStore()
        return _store
