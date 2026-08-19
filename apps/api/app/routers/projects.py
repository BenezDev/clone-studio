"""Rotas de projetos: criar, editar, renderizar."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.jobs.queue import get_queue
from core.pipeline.stages import STAGE_ORDER
from core.storage.project import Project, create_project, list_projects

router = APIRouter(prefix="/projects", tags=["projects"])


class CreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200)
    idea: str = Field(default="", max_length=20_000, description="a ideia bruta do vídeo")


class VoiceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    )
    emotion: str | None = Field(default=None, max_length=80)
    speed: float | None = Field(default=None, ge=0.25, le=4.0)
    seed: int | None = None
    variant: str | None = Field(default=None, max_length=8)


class TemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "manual"] | None = None
    template_id: str | None = Field(default=None, max_length=128)


class CaptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: Literal[
        "minimal", "hormozi", "clean", "big_tech", "podcast", "karaoke"
    ] | None = None
    uppercase: bool | None = None
    words_per_cue: int | None = Field(default=None, ge=1, le=20)
    enabled: bool | None = None


class EditingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str | None = Field(default=None, max_length=80)
    auto_cut: bool | None = None


class RenderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int | None = Field(default=None, ge=240, le=7680)
    height: int | None = Field(default=None, ge=240, le=7680)
    fps: int | None = Field(default=None, ge=1, le=120)
    codec: Literal["libx264", "libx265"] | None = None
    audio_sample_rate: int | None = Field(default=None, ge=8_000, le=192_000)
    crf: int | None = Field(default=None, ge=0, le=51)
    preset: Literal[
        "ultrafast", "superfast", "veryfast", "faster", "fast",
        "medium", "slow", "slower", "veryslow",
    ] | None = None
    loudness_lufs: float | None = Field(default=None, ge=-70.0, le=-5.0)
    loudness_true_peak: float | None = Field(default=None, ge=-20.0, le=0.0)


class UpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    idea: str | None = Field(default=None, max_length=20_000)
    voice: VoiceUpdate | None = None
    template: TemplateUpdate | None = None
    captions: CaptionUpdate | None = None
    editing: EditingUpdate | None = None
    render: RenderUpdate | None = None


class ScriptPayload(BaseModel):
    """Roteiro editado manualmente pelo usuário."""

    script: dict[str, Any]


class RenderPayload(BaseModel):
    preview: bool = False
    force_stages: list[str] = Field(default_factory=list)


def _load(project_id: str) -> Project:
    try:
        return Project.load(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _summary(project: Project) -> dict[str, Any]:
    payload = project.to_dict()
    payload["has_script"] = project.script_file.exists()
    payload["has_voice"] = project.voice_file.exists()
    payload["renders"] = sorted(
        p.name for p in project.renders_dir.glob("*.mp4")
    ) if project.renders_dir.exists() else []
    return payload


@router.get("")
def get_projects() -> dict[str, Any]:
    return {"projects": [_summary(p) for p in list_projects()]}


@router.post("", status_code=201)
def post_project(payload: CreatePayload) -> dict[str, Any]:
    if not payload.title and not payload.idea:
        raise HTTPException(
            status_code=400, detail="Informe ao menos um título ou uma ideia."
        )
    project = create_project(payload.title or payload.idea, payload.idea)
    return _summary(project)


@router.get("/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    return _summary(_load(project_id))


@router.patch("/{project_id}")
def patch_project(project_id: str, payload: UpdatePayload) -> dict[str, Any]:
    project = _load(project_id)

    if payload.title is not None:
        project.title = payload.title
    if payload.idea is not None:
        project.idea = payload.idea

    # Alterar uma configuração invalida as etapas que dependem dela — senão o
    # resume devolveria um vídeo com a configuração antiga.
    invalidations = {
        "voice": "tts",
        "template": "template",
        "captions": "captions",
        "editing": "render",
        "render": "render",
    }
    for field, stage in invalidations.items():
        update = getattr(payload, field)
        if update is None:
            continue
        target = getattr(project, field)
        changed = False
        for key, value in update.model_dump(exclude_unset=True).items():
            if getattr(target, key) != value:
                setattr(target, key, value)
                changed = True
        if changed:
            project.invalidate_from(stage, STAGE_ORDER)

    project.save()
    return _summary(project)


@router.get("/{project_id}/script")
def get_script(project_id: str) -> dict[str, Any]:
    import json

    project = _load(project_id)
    if not project.script_file.exists():
        return {"script": None, "idea": project.idea}
    return {"script": json.loads(project.script_file.read_text(encoding="utf-8"))}


@router.put("/{project_id}/script")
def put_script(project_id: str, payload: ScriptPayload) -> dict[str, Any]:
    """Salva o roteiro. O usuário sempre pode editar tudo antes de gerar."""
    from services.llm.script import Script

    project = _load(project_id)
    try:
        script = Script.model_validate(payload.script)
    except Exception as exc:  # noqa: BLE001 - erro de validação vira 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not script.full_text.strip():
        raise HTTPException(
            status_code=400,
            detail="O roteiro não tem nenhum texto falado.",
        )

    script.save(project.script_file)
    if script.title and not project.title:
        project.title = script.title
    if script.voice_style.emotion:
        project.voice.emotion = script.voice_style.emotion
        project.voice.speed = script.voice_style.speed

    # O texto mudou: voz, lip-sync, legendas e render precisam ser refeitos.
    project.invalidate_from("tts", STAGE_ORDER)
    project.save()
    return {"script": script.model_dump()}


@router.post("/{project_id}/render", status_code=202)
def post_render(project_id: str, payload: RenderPayload) -> dict[str, Any]:
    project = _load(project_id)
    invalid = [s for s in payload.force_stages if s not in STAGE_ORDER]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Etapas desconhecidas: {invalid}. Válidas: {STAGE_ORDER}",
        )

    job = get_queue().submit(
        project.id,
        kind="preview" if payload.preview else "render",
        options={
            "preview": payload.preview,
            "force_stages": payload.force_stages,
        },
    )
    return job.to_dict()


@router.get("/{project_id}/stages")
def get_stages(project_id: str) -> dict[str, Any]:
    """Estado de cada etapa — alimenta a visão de resume na interface."""
    project = _load(project_id)
    return {
        "order": STAGE_ORDER,
        "stages": {
            name: {
                "completed": project.stage_completed(name),
                "record": (
                    project.stages[name].__dict__ if name in project.stages else None
                ),
            }
            for name in STAGE_ORDER
        },
    }
