"""Rotas da biblioteca de B-roll."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from apps.api.app.uploads import (
    MAX_BROLL_UPLOAD_BYTES,
    UploadTooLarge,
    copy_file_limited,
    safe_upload_stem,
    unique_upload_path,
)
from core.media import ffmpeg
from core.storage.paths import get_paths, safe_path_component
from services.video.broll import (
    IMAGE_SUFFIXES,
    VIDEO_SUFFIXES,
    find_asset,
    index_broll,
)

router = APIRouter(prefix="/assets", tags=["assets"])


def _serialize(asset: Any) -> dict[str, Any]:
    payload = asset.to_dict()
    payload["preview_url"] = f"/api/media/file?path={asset.path}"
    return payload


@router.get("/broll")
def get_broll(q: str = "") -> dict[str, Any]:
    """Lista a biblioteca. Com `q`, ordena pela correspondência com o pedido."""
    biblioteca = index_broll()
    paths = get_paths()

    if q.strip():
        from services.video.broll import normalize, score_asset

        termos = normalize(q)
        biblioteca = sorted(
            biblioteca, key=lambda a: score_asset(a, termos), reverse=True
        )
        melhor, nota = find_asset(q, biblioteca)
        escolhido = melhor.id if melhor and nota >= 0.34 else None
    else:
        escolhido = None

    return {
        "assets": [_serialize(a) for a in biblioteca],
        "directory": str(paths.broll_dir),
        "matched": escolhido,
        "accepted": sorted(VIDEO_SUFFIXES | IMAGE_SUFFIXES),
    }


@router.post("/broll/upload", status_code=201)
def upload_broll(file: UploadFile = File(...)) -> dict[str, Any]:
    paths = get_paths()
    paths.broll_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "asset.mp4").suffix.lower()
    if suffix not in VIDEO_SUFFIXES | IMAGE_SUFFIXES:
        raise HTTPException(
            status_code=400, detail=f"Formato não suportado para B-roll: {suffix}"
        )

    # O nome do arquivo É a busca: é dele que saem as palavras-chave.
    stem = safe_upload_stem(file.filename, fallback="broll")
    destino = unique_upload_path(paths.broll_dir, stem, suffix)
    try:
        copy_file_limited(file.file, destino, MAX_BROLL_UPLOAD_BYTES)
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    try:
        # O reparo de cabeçalho é para contêiner de vídeo gravado em stream.
        # Imagem parada não tem duração para recuperar, e chamar isso nela só
        # gastaria um remux inútil.
        if suffix in VIDEO_SUFFIXES:
            ffmpeg.ensure_container_metadata(destino)
        ffmpeg.probe(destino)
    except Exception as exc:  # noqa: BLE001 - devolve o arquivo ruim ao usuário
        destino.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400, detail=f"Arquivo inválido: {exc}"
        ) from exc

    encontrado = next((a for a in index_broll() if a.id == destino.name), None)
    if encontrado is None:
        raise HTTPException(status_code=500, detail="O asset não pôde ser indexado.")
    return {"asset": _serialize(encontrado)}


@router.delete("/broll/{asset_id}")
def delete_broll(asset_id: str) -> dict[str, Any]:
    try:
        asset_id = safe_path_component(asset_id, label="ID do asset")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    paths = get_paths()
    alvo = next((a for a in index_broll() if a.id == asset_id), None)
    if alvo is None:
        raise HTTPException(status_code=404, detail="Asset não encontrado.")

    arquivo = (paths.root / alvo.path).resolve()
    if paths.broll_dir.resolve() not in arquivo.parents:
        raise HTTPException(
            status_code=409, detail="O asset aponta para fora da biblioteca."
        )
    arquivo.unlink(missing_ok=True)
    return {"deleted": True}
