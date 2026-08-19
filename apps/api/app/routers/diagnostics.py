"""Rotas de diagnóstico — a página Diagnostics consome estas."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.config.loader import active_profile, get_hardware, refresh_hardware
from core.diagnostics.checks import TESTS, run_diagnostics

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("")
def get_diagnostics(services: bool = True) -> dict[str, object]:
    return run_diagnostics(include_services=services).to_dict()


@router.get("/hardware")
def get_hardware_report() -> dict[str, object]:
    report = get_hardware()
    payload = report.to_dict()
    payload["active_profile"] = active_profile()
    return payload


@router.post("/hardware/refresh")
def refresh_hardware_report() -> dict[str, object]:
    return refresh_hardware().to_dict()


@router.get("/tests")
def list_tests() -> dict[str, list[str]]:
    return {"tests": sorted(TESTS)}


@router.post("/tests/{name}")
def run_test(name: str) -> dict[str, object]:
    runner = TESTS.get(name)
    if runner is None:
        raise HTTPException(
            status_code=404,
            detail=f"Teste desconhecido: {name}. Disponíveis: {', '.join(TESTS)}",
        )
    return runner().to_dict()
