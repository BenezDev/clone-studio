"""Servidor de arquivos locais para a interface.

Só entrega arquivos de dentro de diretórios explicitamente permitidos. Isso
importa: o backend tem acesso de leitura a tudo que o usuário tem, e um
parâmetro `path` sem validação viraria leitura arbitrária de disco.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from core.storage.paths import get_paths

router = APIRouter(prefix="/media", tags=["media"])

# Extensões que a interface precisa exibir. Nada além disso é servido.
ALLOWED_SUFFIXES = {
    ".mp4", ".webm", ".mov", ".m4v",
    ".wav", ".mp3", ".m4a", ".flac", ".ogg",
    ".jpg", ".jpeg", ".png", ".webp",
    ".ass", ".srt", ".json", ".log", ".txt",
}


def _allowed_roots() -> list[Path]:
    paths = get_paths()
    return [
        paths.projects.resolve(),
        paths.exports.resolve(),
        paths.data.resolve(),
        paths.cache.resolve(),
        paths.logs.resolve(),
    ]


def _resolve(raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = get_paths().root / candidate

    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.") from exc

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Não é um arquivo.")

    if resolved.suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=403,
            detail=f"Tipo de arquivo não servido: {resolved.suffix}",
        )

    for root in _allowed_roots():
        if resolved == root or root in resolved.parents:
            return resolved

    raise HTTPException(
        status_code=403,
        detail="Este caminho está fora dos diretórios do Local Clone Studio.",
    )


@router.get("/file")
def get_file(path: str = Query(..., description="caminho absoluto ou relativo à raiz")):
    resolved = _resolve(path)
    media_type, _ = mimetypes.guess_type(resolved.name)
    return FileResponse(
        resolved,
        media_type=media_type or "application/octet-stream",
        filename=resolved.name,
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/exists")
def file_exists(path: str = Query(...)) -> dict[str, object]:
    try:
        resolved = _resolve(path)
    except HTTPException:
        return {"exists": False}
    stat = resolved.stat()
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "path": str(resolved),
    }
