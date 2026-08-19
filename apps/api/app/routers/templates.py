"""Rotas de templates de vídeo."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from apps.api.app.uploads import (
    MAX_TEMPLATE_UPLOAD_BYTES,
    UploadTooLarge,
    copy_file_limited,
    safe_upload_stem,
    unique_upload_path,
)
from core.media import ffmpeg
from core.storage.paths import get_paths, safe_path_component
from services.templates.library import (
    SelectionCriteria,
    TemplateError,
    index_templates,
    load_templates,
    plan_composition,
    score_template,
    template_path,
)
from services.templates.models import TemplateMetadata

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orientation: Literal["vertical", "horizontal", "square"] | None = None
    style: Literal[
        "talking_head", "podcast", "selfie", "walking", "seated", "standing"
    ] | None = None
    energy: str | None = Field(default=None, pattern=r"^(low|medium|high)$")
    gestures: str | None = Field(
        default=None, pattern=r"^(none|low|medium|high)$"
    )
    camera: str | None = Field(
        default=None, pattern=r"^(close|medium_close|medium|wide)$"
    )
    background: str | None = Field(default=None, max_length=80)
    loopable: bool | None = None
    face_detected: bool | None = None
    notes: str | None = Field(default=None, max_length=2_000)
    tags: list[str] | None = Field(default=None, max_length=50)
    enabled: bool | None = None


def _serialize(metadata: TemplateMetadata) -> dict[str, Any]:
    payload = metadata.to_dict()
    payload["preview_url"] = f"/api/media/file?path={template_path(metadata)}"
    return payload


@router.get("")
def get_templates(include_disabled: bool = False) -> dict[str, Any]:
    templates = load_templates(only_enabled=not include_disabled)
    return {
        "templates": [_serialize(t) for t in templates],
        "total_duration": round(sum(t.duration for t in templates), 2),
        "directory": str(get_paths().templates_dir),
    }


@router.post("/index")
def post_index() -> dict[str, Any]:
    """Varre o diretório e cria/atualiza os metadados."""
    templates = index_templates()
    return {
        "indexed": len(templates),
        "templates": [_serialize(t) for t in templates],
    }


@router.post("/upload", status_code=201)
def upload_template(
    video: UploadFile = File(...),
    style: Literal[
        "talking_head", "podcast", "selfie", "walking", "seated", "standing"
    ] = Form("talking_head"),
    energy: Literal["low", "medium", "high"] = Form("medium"),
    gestures: Literal["none", "low", "medium", "high"] = Form("medium"),
    camera: Literal["close", "medium_close", "medium", "wide"] = Form(
        "medium_close"
    ),
    background: str = Form("neutral", max_length=80),
) -> dict[str, Any]:
    paths = get_paths()
    paths.templates_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(video.filename or "template.mp4").suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        raise HTTPException(
            status_code=400, detail=f"Formato de vídeo não suportado: {suffix}"
        )

    stem = safe_upload_stem(video.filename, fallback="template")
    destination = unique_upload_path(paths.templates_dir, stem, suffix)
    try:
        copy_file_limited(video.file, destination, MAX_TEMPLATE_UPLOAD_BYTES)
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409, detail="Já existe um upload com esse nome; tente novamente."
        ) from exc

    try:
        # A gravação feita no app chega como WebM de MediaRecorder: sem duração
        # e sem índice no cabeçalho, porque o navegador escreve em stream. O
        # remux devolve os dois sem reencodar. Upload normal não é tocado.
        repaired = ffmpeg.ensure_container_metadata(destination)
        
        # Tentar validar; se falhar com WebM/MKV, converter para MP4
        try:
            info = ffmpeg.validate_video(destination, min_duration=1.0)
        except Exception as validation_error:
            if destination.suffix.lower() in {".webm", ".mkv"}:
                # `with_suffix` sozinho atropelaria um template `.mp4` já
                # existente com o mesmo nome: a unicidade tinha sido garantida
                # só para o `.webm`.
                mp4_dest = unique_upload_path(
                    paths.templates_dir, destination.stem, ".mp4"
                )
                try:
                    # Converter para MP4 com recodificação rápida
                    ffmpeg.run_ffmpeg([
                        "-fflags", "+genpts",
                        "-avoid_negative_ts", "make_zero",
                        "-i", str(destination),
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                        "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart",
                        str(mp4_dest),
                    ])
                    info = ffmpeg.validate_video(mp4_dest, min_duration=1.0)
                    destination.unlink(missing_ok=True)
                    destination = mp4_dest
                except Exception as convert_error:
                    mp4_dest.unlink(missing_ok=True)
                    # Se a conversão falhar, relançar o erro de validação original
                    raise validation_error from convert_error
            else:
                raise

        width, height = info.resolution or (0, 0)
        fps = info.video.fps if info.video else 0
        if info.duration > 900:
            raise ValueError("O template pode ter no máximo 15 minutos.")
        if width > 7680 or height > 7680 or (fps or 0) > 120:
            raise ValueError(
                "Resolução/FPS excessivos; máximo de 7680×7680 e 120 fps."
            )
    except Exception as exc:  # noqa: BLE001 - devolve o arquivo ruim ao usuário
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400, detail=f"Vídeo inválido: {exc}"
        ) from exc

    metadata_file = destination.with_suffix(".json")
    try:
        metadata = TemplateMetadata(
            id=destination.stem,
            duration=round(info.duration, 2),
            style=style,
            energy=energy,
            gestures=gestures,
            camera=camera,
            background=background,
        )
        metadata.save(metadata_file)
        # `index_templates` é quem preenche resolução, fps e caminho. Sem reler,
        # a resposta sai com width/height/fps em 0 e video_path vazio.
        metadata = next(
            (t for t in index_templates() if t.id == metadata.id), metadata
        )
    except Exception as exc:
        destination.unlink(missing_ok=True)
        metadata_file.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail="Não foi possível registrar o template; nenhum arquivo parcial foi mantido.",
        ) from exc

    warnings: list[str] = []
    if info.resolution and info.resolution[0] >= info.resolution[1]:
        warnings.append(
            "Este vídeo é horizontal. Ele será cortado para 9:16 e o "
            "enquadramento pode ficar ruim — prefira gravar na vertical."
        )
    return {"template": _serialize(metadata), "warnings": warnings}


@router.patch("/{template_id}")
def patch_template(template_id: str, payload: TemplatePatch) -> dict[str, Any]:
    paths = get_paths()
    try:
        template_id = safe_path_component(template_id, label="ID do template")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    metadata_file = paths.templates_dir / f"{template_id}.json"
    if not metadata_file.exists():
        raise HTTPException(status_code=404, detail="Template não encontrado.")

    metadata = TemplateMetadata.load(metadata_file)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(metadata, key, value)
    metadata.save(metadata_file)
    return _serialize(metadata)


@router.delete("/{template_id}")
def delete_template(template_id: str, remove_file: bool = False) -> dict[str, Any]:
    """Desativa (padrão) ou apaga o template.

    O padrão é apenas desativar: apagar um vídeo original do usuário por
    engano é irreversível.
    """
    paths = get_paths()
    try:
        template_id = safe_path_component(template_id, label="ID do template")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    metadata_file = paths.templates_dir / f"{template_id}.json"
    if not metadata_file.exists():
        raise HTTPException(status_code=404, detail="Template não encontrado.")

    metadata = TemplateMetadata.load(metadata_file)
    if not remove_file:
        metadata.enabled = False
        metadata.save(metadata_file)
        return {"disabled": True, "deleted": False}

    video = (paths.root / metadata.video_path).resolve()
    templates_root = paths.templates_dir.resolve()
    if templates_root not in video.parents:
        raise HTTPException(
            status_code=409,
            detail="Metadados do template apontam para fora da biblioteca.",
        )
    video.unlink(missing_ok=True)
    metadata_file.unlink(missing_ok=True)
    return {"disabled": True, "deleted": True}


@router.post("/plan")
def post_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Simula a composição para uma duração — usado no passo Template."""
    duration = float(payload.get("duration", 30))
    criteria = SelectionCriteria(
        energy=payload.get("energy", "medium"),
        gestures=payload.get("gestures", "medium"),
        style=payload.get("style", "talking_head"),
        min_duration=duration,
    )
    templates = load_templates()
    try:
        plan = plan_composition(duration, criteria, templates)
    except TemplateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "strategy": plan.strategy,
        "warnings": plan.warnings,
        "total_duration": round(plan.total_duration, 2),
        "segments": [
            {
                "template_id": s.template_id,
                "start": s.start,
                "duration": s.duration,
            }
            for s in plan.segments
        ],
        "ranking": [
            {"id": t.id, "score": round(score_template(t, criteria), 2)}
            for t in sorted(
                templates, key=lambda t: score_template(t, criteria), reverse=True
            )
        ],
    }
