"""Backend do Local Clone Studio.

Local-first: escuta apenas no loopback, sem telemetria, sem chamada externa.
O único tráfego de saída que existe no projeto é o download de pesos, e ele
acontece por comando explícito, fora do backend.

Este processo NÃO importa torch. Toda inferência acontece em subprocessos, o
que faz a API subir instantaneamente e devolver a memória ao sistema entre
etapas.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.app.uploads import RequestBodyLimitMiddleware
from apps.api.app.routers import (
    assets,
    diagnostics,
    jobs,
    media,
    models,
    projects,
    script,
    settings as settings_router,
    templates,
    voice,
)
from core.config.loader import load_settings
from core.jobs.queue import get_queue
from core.storage.paths import get_paths

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("clone_studio.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    paths = get_paths()
    paths.ensure_runtime_dirs()

    file_handler = logging.FileHandler(paths.logs / "backend.log", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s · %(message)s")
    )
    logging.getLogger().addHandler(file_handler)

    queue = get_queue()
    queue.start()
    logger.info("Local Clone Studio pronto · %s", paths.root)
    yield
    queue.stop()
    logger.info("encerrando")


settings = load_settings()

app = FastAPI(
    title="Local Clone Studio",
    version="0.1.0",
    description="Estúdio local de vídeos verticais com voz e rosto próprios.",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# O frontend Vite roda em outra porta do mesmo loopback durante o dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://127.0.0.1:{settings.app.web_port}",
        f"http://localhost:{settings.app.web_port}",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestBodyLimitMiddleware)

_TRUSTED_BROWSER_ORIGINS = {
    f"http://127.0.0.1:{settings.app.web_port}",
    f"http://localhost:{settings.app.web_port}",
    f"http://127.0.0.1:{settings.app.api_port}",
    f"http://localhost:{settings.app.api_port}",
}


@app.middleware("http")
async def block_remote_clients(request: Request, call_next):
    """Recusa qualquer requisição que não venha do próprio computador.

    Segunda camada de defesa: mesmo que alguém force o bind externo, os dados
    de identidade continuam inacessíveis pela rede.
    """
    client = request.client.host if request.client else ""
    if client and client not in {"127.0.0.1", "::1", "localhost"}:
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Local Clone Studio aceita conexões apenas do próprio "
                          "computador."
            },
        )
    # CORS controla leitura da resposta, não impede um site malicioso de
    # enviar um POST multipart simples para localhost. Para mutações feitas
    # pelo navegador, rejeitamos explicitamente origens externas e Fetch
    # Metadata cross-site. Clientes locais sem Origin (CLI/curl) continuam
    # suportados.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if (origin and origin not in _TRUSTED_BROWSER_ORIGINS) or fetch_site == "cross-site":
            return JSONResponse(
                status_code=403,
                content={"detail": "Origem externa não autorizada para alterar dados locais."},
            )
    return await call_next(request)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    """Nunca devolver apenas 'erro interno'."""
    logger.exception("erro não tratado em %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc) or exc.__class__.__name__,
            "type": exc.__class__.__name__,
            "hint": "Consulte logs/backend.log para o traceback completo.",
        },
    )


for router in (
    diagnostics.router,
    settings_router.router,
    models.router,
    assets.router,
    voice.router,
    templates.router,
    projects.router,
    jobs.router,
    script.router,
    media.router,
):
    app.include_router(router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, object]:
    from core.config.loader import active_profile

    return {
        "status": "ok",
        "app": settings.app.name,
        "version": app.version,
        "hardware_profile": active_profile(),
        "local_only": True,
        "telemetry": False,
    }
