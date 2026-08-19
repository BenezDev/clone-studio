"""Rotas de configuração e presets."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from core.config.loader import active_profile, load_settings, save_user_overrides
from core.storage.cache import get_cache
from core.storage.paths import get_paths
from services.video.captions import PRESETS as CAPTION_PRESETS

router = APIRouter(prefix="/settings", tags=["settings"])

CACHE_NAMESPACES = ("tts", "transcripts", "templates", "lipsync", "voice_previews")


@router.get("")
def get_settings() -> dict[str, Any]:
    settings = load_settings()
    paths = get_paths()
    return {
        "settings": settings.model_dump(),
        "active_profile": active_profile(),
        "paths": {
            "root": str(paths.root),
            "models": str(paths.models),
            "projects": str(paths.projects),
            "exports": str(paths.exports),
            "identity": str(paths.identity),
            "cache": str(paths.cache),
            "logs": str(paths.logs),
        },
        "config_file": str(paths.user_config_file),
    }


@router.patch("")
def patch_settings(overrides: dict[str, Any]) -> dict[str, Any]:
    """Grava sobreposições em config/local.yaml.

    A validação roda ANTES da escrita: uma configuração inválida nunca chega
    ao disco. Chaves de privacidade e bind externo são rejeitadas pelo próprio
    esquema.
    """
    try:
        settings = save_user_overrides(overrides)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"settings": settings.model_dump(), "active_profile": active_profile()}


@router.get("/presets")
def get_presets() -> dict[str, Any]:
    from services.llm.script import PRESETS as SCRIPT_PRESETS

    return {
        "captions": [
            {
                "key": preset.key,
                "label": preset.label,
                "font": preset.font,
                "uppercase": preset.uppercase,
                "words_per_cue": preset.words_per_cue,
            }
            for preset in CAPTION_PRESETS.values()
        ],
        "editing": ["clean", "fast", "podcast", "viral", "cinematic"],
        "script": [
            {"key": key, "label": value["label"]}
            for key, value in SCRIPT_PRESETS.items()
        ],
    }


@router.get("/cache")
def get_cache_usage() -> dict[str, Any]:
    entries = []
    total = 0
    for name in CACHE_NAMESPACES:
        namespace = get_cache(name)
        size = namespace.size_bytes()
        total += size
        entries.append(
            {"name": name, "size_bytes": size, "path": str(namespace.root)}
        )
    return {"namespaces": entries, "total_bytes": total}


@router.delete("/cache/{name}")
def clear_cache(name: str) -> dict[str, Any]:
    if name not in CACHE_NAMESPACES:
        raise HTTPException(
            status_code=404,
            detail=f"Namespace desconhecido. Válidos: {', '.join(CACHE_NAMESPACES)}",
        )
    removed = get_cache(name).clear()
    return {"cleared": name, "entries_removed": removed}
