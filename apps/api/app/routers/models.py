"""Rotas de modelos e licenças — a página Models consome estas."""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config.loader import active_profile, get_hardware
from core.licensing.registry import get_registry
from core.storage.paths import get_paths

router = APIRouter(prefix="/models", tags=["models"])
_download_lock = threading.Lock()
_downloads: dict[str, subprocess.Popen] = {}


class InstallPayload(BaseModel):
    key: str
    force: bool = False


def _serialize(entry, hardware, registry) -> dict[str, Any]:
    verdict = registry.check_hardware(entry, hardware)
    installed = (entry.install_dir / ".installed.json").exists()
    size_on_disk = 0
    if installed:
        size_on_disk = sum(
            f.stat().st_size for f in entry.install_dir.rglob("*") if f.is_file()
        )

    return {
        "key": entry.key,
        "display_name": entry.display_name,
        "purpose": entry.purpose,
        "engine": entry.engine,
        "repo": entry.repo,
        "source": entry.source,
        "upstream_code": entry.upstream_code,
        "revision": entry.revision,
        "code_revision": entry.code_revision,
        "license": entry.license,
        "license_url": entry.license_url,
        "verified_at": entry.verified_at,
        "commercial_status": entry.commercial.value,
        "commercial_ok": entry.commercial_ok(),
        "commercial_blockers": entry.commercial_blockers(),
        "dependencies": [
            {
                "name": d.name,
                "license": d.license,
                "commercial": d.commercial.value,
                "source": d.source,
                "note": d.note,
            }
            for d in entry.dependencies
        ],
        "size_gb": entry.size_gb,
        "size_on_disk_bytes": size_on_disk,
        "install_dir": str(entry.install_dir),
        "env": entry.env,
        "optional": entry.optional,
        "notes": entry.notes,
        "installed": installed,
        "hardware_compatible": verdict.compatible,
        "hardware_reasons": list(verdict.reasons),
        "hardware_degraded": verdict.degraded,
        "requires": {
            "min_ram_gb": entry.requires.min_ram_gb,
            "min_vram_gb": entry.requires.min_vram_gb,
            "recommended_vram_gb": entry.requires.recommended_vram_gb,
            "python": entry.requires.python,
        },
    }


@router.get("")
def get_models(license_mode: str = "personal") -> dict[str, Any]:
    registry = get_registry()
    hardware = get_hardware()

    models = [_serialize(e, hardware, registry) for e in registry]
    recommended: dict[str, list[str]] = {}
    for purpose in {e.purpose for e in registry}:
        recommended[purpose] = [
            e.key
            for e in registry.recommended_for(purpose, license_mode)  # type: ignore[arg-type]
        ]

    return {
        "models": sorted(models, key=lambda m: (m["purpose"], m["size_gb"])),
        "recommended": recommended,
        "license_mode": license_mode,
        "profile": active_profile(),
        "models_dir": str(get_paths().models),
    }


@router.get("/{key}")
def get_model(key: str) -> dict[str, Any]:
    registry = get_registry()
    entry = registry.get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Modelo desconhecido: {key}")
    return _serialize(entry, get_hardware(), registry)


@router.post("/install", status_code=202)
def post_install(payload: InstallPayload) -> dict[str, Any]:
    """Dispara o download em background.

    Downloads são deliberadamente explícitos: a interface mostra tamanho e
    licença antes, e nada é baixado sozinho.
    """
    registry = get_registry()
    entry = registry.get(payload.key)
    if entry is None:
        raise HTTPException(
            status_code=404, detail=f"Modelo desconhecido: {payload.key}"
        )

    verdict = registry.check_hardware(entry, get_hardware())
    if not verdict.compatible and not payload.force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Modelo incompatível com este hardware.",
                "reasons": list(verdict.reasons),
                "hint": "Reenvie com force=true para baixar mesmo assim.",
            },
        )

    paths = get_paths()
    log_file = paths.logs / f"download-{entry.key}.log"
    command = [
        sys.executable,
        str(paths.root / "scripts" / "models.py"),
        "install",
        entry.key,
        "--yes",
    ]
    if payload.force:
        command.append("--force")

    with _download_lock:
        existing = _downloads.get(entry.key)
        if existing is not None and existing.poll() is None:
            return {
                "started": False,
                "already_running": True,
                "key": entry.key,
                "size_gb": entry.size_gb,
                "log_file": str(log_file),
                "detail": "O download deste modelo já está em andamento.",
            }
        handle = log_file.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command, stdout=handle, stderr=subprocess.STDOUT, cwd=str(paths.root)
            )
        finally:
            handle.close()
        _downloads[entry.key] = process

    return {
        "started": True,
        "key": entry.key,
        "size_gb": entry.size_gb,
        "log_file": str(log_file),
        "detail": f"Baixando ~{entry.size_gb:.1f} GB. Acompanhe pelo log.",
    }


@router.get("/{key}/install-log")
def get_install_log(key: str, tail: int = 60) -> dict[str, Any]:
    log_file = get_paths().logs / f"download-{key}.log"
    if not log_file.exists():
        return {"log": "", "detail": "Nenhum download registrado para este modelo."}
    text = log_file.read_text(encoding="utf-8", errors="replace")
    # Barras de progresso usam \r; converte para linhas legíveis.
    lines = text.replace("\r", "\n").splitlines()
    return {"log": "\n".join(lines[-tail:])}
