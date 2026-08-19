"""Rotas de roteiro (Ollama). Tudo aqui é opcional."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.llm import script as script_engine
from services.llm.ollama import OllamaClient, OllamaUnavailable

router = APIRouter(prefix="/script", tags=["script"])


class GeneratePayload(BaseModel):
    idea: str = Field(min_length=3)
    preset: str = "viral"
    duration: int = Field(default=45, ge=10, le=180)
    model: str | None = None
    extra_instructions: str = ""


class EditPayload(BaseModel):
    text: str = Field(min_length=1)
    instruction: str = ""
    model: str | None = None
    duration: int = 45


def _unavailable(exc: OllamaUnavailable) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "message": str(exc),
            "hint": "O Ollama é opcional. Você pode escrever o roteiro à mão "
                    "na página Script e seguir normalmente.",
        },
    )


@router.get("/status")
def status() -> dict[str, Any]:
    client = OllamaClient()
    if not client.available():
        return {
            "available": False,
            "base_url": client.base_url,
            "models": [],
            "detail": "Ollama offline — a escrita manual do roteiro continua "
                      "funcionando.",
        }
    models = client.list_models()
    return {
        "available": True,
        "base_url": client.base_url,
        "models": [
            {
                "name": m.name,
                "size_gb": round(m.size_gb, 2),
                "family": m.family,
                "parameter_size": m.parameter_size,
                "quantization": m.quantization,
            }
            for m in models
        ],
        "selected": client.resolve_model() if models else None,
    }


@router.get("/presets")
def presets() -> dict[str, Any]:
    return {
        "presets": [
            {"key": key, "label": value["label"], "guidance": value["guidance"]}
            for key, value in script_engine.PRESETS.items()
        ]
    }


@router.post("/generate")
def generate(payload: GeneratePayload) -> dict[str, Any]:
    try:
        script = script_engine.generate_script(
            payload.idea,
            preset=payload.preset,
            duration=payload.duration,
            model=payload.model,
            extra_instructions=payload.extra_instructions,
        )
    except OllamaUnavailable as exc:
        raise _unavailable(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "hint": "Modelos pequenos às vezes não respeitam o formato "
                        "JSON. Tente outro modelo ou gere de novo.",
            },
        ) from exc
    return {"script": script.model_dump()}


def _run_edit(func, payload: EditPayload, **extra: Any) -> dict[str, Any]:
    try:
        result = func(payload.text, model=payload.model, **extra)
    except OllamaUnavailable as exc:
        raise _unavailable(exc) from exc
    return {"result": result}


@router.post("/hook")
def hook(payload: EditPayload) -> dict[str, Any]:
    return _run_edit(script_engine.improve_hook, payload)


@router.post("/rewrite")
def rewrite(payload: EditPayload) -> dict[str, Any]:
    return _run_edit(
        script_engine.rewrite, payload,
        instruction=payload.instruction or "Deixe mais natural e falado.",
    )


@router.post("/summarize")
def summarize(payload: EditPayload) -> dict[str, Any]:
    return _run_edit(script_engine.summarize, payload)


@router.post("/shorten")
def shorten(payload: EditPayload) -> dict[str, Any]:
    return _run_edit(script_engine.long_to_short, payload, duration=payload.duration)


@router.post("/cta")
def cta(payload: EditPayload) -> dict[str, Any]:
    return _run_edit(script_engine.make_cta, payload)


@router.post("/caption")
def caption(payload: EditPayload) -> dict[str, Any]:
    return _run_edit(script_engine.make_caption, payload)


@router.post("/title")
def title(payload: EditPayload) -> dict[str, Any]:
    return _run_edit(script_engine.make_title, payload)


@router.post("/hashtags")
def hashtags(payload: EditPayload) -> dict[str, Any]:
    try:
        return {"result": script_engine.make_hashtags(payload.text, model=payload.model)}
    except OllamaUnavailable as exc:
        raise _unavailable(exc) from exc


# ---------------------------------------------------------------------------
# Biblioteca local de roteiros — funciona sem Ollama e sem internet
# ---------------------------------------------------------------------------


class LibraryFillPayload(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


@router.get("/library")
def library_list(
    niche: str | None = None,
    templates: bool | None = None,
    q: str = "",
) -> dict[str, Any]:
    """Catálogo de roteiros prontos. Não depende de LLM nem de rede."""
    from services.llm.library import NICHE_LABELS, get_library

    biblioteca = get_library()
    itens = biblioteca.filter(niche=niche, templates=templates, query=q)
    return {
        "total": len(biblioteca),
        "count": len(itens),
        "niches": [
            {
                "key": chave,
                "label": NICHE_LABELS.get(chave, chave),
                "count": quantidade,
            }
            for chave, quantidade in sorted(biblioteca.niches().items())
        ],
        "scripts": [item.to_dict() for item in itens],
    }


@router.get("/library/{script_id}")
def library_detail(script_id: str) -> dict[str, Any]:
    from services.llm.library import get_library

    item = get_library().get(script_id)
    if item is None:
        raise HTTPException(
            status_code=404, detail=f"Roteiro '{script_id}' não existe na biblioteca."
        )
    return item.to_dict()


@router.post("/library/{script_id}/build")
def library_build(script_id: str, payload: LibraryFillPayload) -> dict[str, Any]:
    """Converte um item da biblioteca no `Script` que o pipeline consome.

    Recusa enquanto sobrar lacuna: gerar um vídeo falando "{{NOME}}" em voz
    alta seria pior do que falhar aqui.
    """
    from services.llm.library import LibraryError, get_library

    item = get_library().get(script_id)
    if item is None:
        raise HTTPException(
            status_code=404, detail=f"Roteiro '{script_id}' não existe na biblioteca."
        )
    try:
        script = item.to_script(payload.values)
    except LibraryError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "missing_slots": list(item.missing_slots(payload.values)),
                "hint": "Preencha todas as lacunas antes de gerar o vídeo.",
            },
        ) from exc
    return {"script": script.model_dump(), "disclaimer": item.disclaimer}
