"""Contexto compartilhado entre as etapas do pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.config.schema import Settings
from core.storage.project import Project

# (fração_global, mensagem)
ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


class PipelineCancelled(RuntimeError):
    """O usuário cancelou o job."""


class StageFailed(RuntimeError):
    """Falha de etapa com contexto suficiente para diagnóstico."""

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        hint: str = "",
        detail: str = "",
        log_file: Path | None = None,
        command: list[str] | None = None,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.hint = hint
        self.detail = detail
        self.log_file = log_file
        self.command = command or []
        self.exit_code = exit_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "message": self.message,
            "hint": self.hint,
            "detail": self.detail[-4000:] if self.detail else "",
            "log_file": str(self.log_file) if self.log_file else None,
            "command": self.command,
            "exit_code": self.exit_code,
        }


@dataclass
class PipelineContext:
    """Estado que atravessa todas as etapas."""

    project: Project
    settings: Settings
    preview: bool = False
    force_stages: set[str] = field(default_factory=set)

    on_progress: ProgressCallback | None = None
    should_cancel: CancelCheck | None = None

    # Artefatos produzidos pelas etapas, disponíveis para as seguintes.
    artifacts: dict[str, Any] = field(default_factory=dict)

    def emit(self, fraction: float, message: str) -> None:
        if self.on_progress:
            self.on_progress(max(0.0, min(1.0, fraction)), message)

    def check_cancelled(self) -> None:
        if self.should_cancel and self.should_cancel():
            raise PipelineCancelled("Job cancelado pelo usuário.")

    def set(self, key: str, value: Any) -> None:
        self.artifacts[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.artifacts.get(key, default)

    def require(self, key: str) -> Any:
        if key not in self.artifacts:
            raise StageFailed(
                "pipeline",
                f"Artefato obrigatório ausente: '{key}'.",
                hint="Isso indica que uma etapa anterior não rodou. "
                     "Rode o render novamente sem --resume.",
            )
        return self.artifacts[key]

    def should_run(self, stage: str) -> bool:
        """Decide entre executar a etapa ou reaproveitar o resultado anterior."""
        if stage in self.force_stages:
            return True
        return not self.project.stage_completed(stage)
