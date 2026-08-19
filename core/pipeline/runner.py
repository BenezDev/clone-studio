"""Executor do pipeline.

Roda as etapas em ordem, converte o progresso de cada uma numa fração global
ponderada e garante que uma falha traga sempre: estágio, comando, exit code,
stderr e provável solução.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import platform_hints as hints
from core.config.loader import load_settings
from core.pipeline.context import (
    CancelCheck,
    PipelineCancelled,
    PipelineContext,
    ProgressCallback,
    StageFailed,
)
from core.pipeline.stages import PRODUCTION_STAGES, STAGE_ORDER, Stage
from core.storage.project import Project

logger = logging.getLogger("clone_studio.pipeline")


@dataclass
class PipelineResult:
    project: Project
    output: Path | None
    duration_seconds: float
    stages: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project.id,
            "output": str(self.output) if self.output else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "stages": {k: round(v, 2) for k, v in self.stages.items()},
            "warnings": self.warnings,
            "skipped": self.skipped,
        }


def run_pipeline(
    project: Project,
    *,
    preview: bool = False,
    force_stages: set[str] | None = None,
    stages: list[Stage] | None = None,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> PipelineResult:
    """Executa o pipeline de produção para um projeto.

    `force_stages` reexecuta etapas específicas mesmo que já estejam
    concluídas; tudo o mais é retomado do estado anterior.
    """
    settings = load_settings()
    plan = stages or PRODUCTION_STAGES
    forced = set(force_stages or ())

    # Forçar uma etapa invalida todas as posteriores: reaproveitar um lip-sync
    # feito com outro áudio produziria um vídeo dessincronizado.
    for name in list(forced):
        if name in STAGE_ORDER:
            forced.update(STAGE_ORDER[STAGE_ORDER.index(name):])

    total_weight = sum(s.weight for s in plan) or 1.0
    completed_weight = 0.0

    ctx = PipelineContext(
        project=project,
        settings=settings,
        preview=preview,
        force_stages=forced,
        should_cancel=should_cancel,
    )

    result = PipelineResult(project=project, output=None, duration_seconds=0.0)
    started = time.monotonic()

    for stage in plan:
        ctx.check_cancelled()

        # Se havia um registro mas seu arquivo sumiu, a etapa será refeita e
        # todas as dependentes precisam deixar de ser consideradas válidas.
        if (
            stage.name in STAGE_ORDER
            and stage.name in project.stages
            and not project.stage_completed(stage.name)
        ):
            project.invalidate_from(stage.name, STAGE_ORDER)
            project.save()

        base = completed_weight / total_weight
        span = stage.weight / total_weight

        def stage_progress(fraction: float, message: str, _b=base, _s=span,
                           _label=stage.label) -> None:
            if on_progress:
                on_progress(_b + _s * max(0.0, min(1.0, fraction)),
                            f"{_label}: {message}" if message else _label)

        ctx.on_progress = stage_progress
        stage_started = time.monotonic()

        logger.info("etapa '%s' iniciando", stage.name)
        try:
            stage.run(ctx)
        except PipelineCancelled:
            raise
        except StageFailed:
            raise
        except FileNotFoundError as exc:
            raise StageFailed(
                stage.name,
                f"Arquivo necessário não encontrado: {exc}",
                hint="Verifique se as etapas anteriores concluíram e se os "
                     "arquivos do projeto não foram movidos.",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - fronteira do pipeline
            if stage.optional:
                logger.warning("etapa opcional '%s' falhou: %s", stage.name, exc)
                result.warnings.append(f"{stage.label}: {exc}")
                completed_weight += stage.weight
                continue
            raise StageFailed(
                stage.name,
                str(exc) or f"Falha inesperada em '{stage.label}'.",
                detail=repr(exc),
            ) from exc

        elapsed = time.monotonic() - stage_started
        result.stages[stage.name] = elapsed
        logger.info("etapa '%s' concluída em %.1fs", stage.name, elapsed)

        completed_weight += stage.weight
        if on_progress:
            on_progress(completed_weight / total_weight, f"{stage.label}: pronto")

    if ctx.get("lipsync_skipped"):
        result.skipped.append("lipsync")
        result.warnings.append(
            "O vídeo foi gerado SEM sincronia labial porque o MuseTalk não "
            f"está instalado. Rode {hints.install(musetalk=True)}."
        )

    plan_warnings = ctx.get("template_plan")
    if plan_warnings is not None and getattr(plan_warnings, "warnings", None):
        result.warnings.extend(plan_warnings.warnings)

    result.output = ctx.get("render_output")
    result.duration_seconds = time.monotonic() - started
    project.save()
    return result
