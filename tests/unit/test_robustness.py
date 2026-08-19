"""Regressões de segurança, concorrência, workers e portabilidade."""

from __future__ import annotations

import io
import asyncio
import time
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response


def test_componentes_de_caminho_sao_portateis() -> None:
    from core.storage.paths import safe_path_component

    assert safe_path_component("perfil-01") == "perfil-01"
    for invalid in (
        "../fora",
        "..",
        "a/b",
        r"a\b",
        "CON",
        "CON.txt",
        "LPT1.json",
        "arquivo.",
        "nul.",
        "",
    ):
        with pytest.raises(ValueError):
            safe_path_component(invalid)


def test_python_do_venv_windows(monkeypatch, tmp_path) -> None:
    import core.storage.paths as paths_module

    monkeypatch.setattr(paths_module.os, "name", "nt")
    assert paths_module.Paths(tmp_path).env_python("backend") == (
        tmp_path / ".envs" / "backend" / "Scripts" / "python.exe"
    )


def test_servicos_de_inferencia_devem_ser_loopback() -> None:
    from core.config.schema import GenerativeBrollConfig, LLMConfig

    assert LLMConfig(base_url="http://localhost:11434").base_url.endswith("11434")
    with pytest.raises(ValueError, match="loopback"):
        LLMConfig(base_url="https://example.com")
    with pytest.raises(ValueError, match="loopback"):
        GenerativeBrollConfig(comfyui_url="http://192.168.1.20:8188")


def test_upload_limitado_remove_parcial(tmp_path) -> None:
    from apps.api.app.uploads import UploadTooLarge, copy_file_limited

    target = tmp_path / "upload.bin"
    with pytest.raises(UploadTooLarge):
        copy_file_limited(io.BytesIO(b"x" * 20), target, 10)
    assert not target.exists()


def test_limite_asgi_age_antes_do_parser() -> None:
    from apps.api.app.uploads import RequestBodyLimitMiddleware

    called = False
    sent: list[dict] = []

    async def inner(scope, receive, send):
        nonlocal called
        called = True

    async def _unused():
        return {"type": "http.request", "body": b""}
    async def run() -> None:
        async def receive():
            return await _unused()

        async def send(message):
            sent.append(message)

        middleware = RequestBodyLimitMiddleware(inner)
        await middleware(
            {
                "type": "http",
                "path": "/api/voice/profiles/me/enroll",
                "headers": [(b"content-length", str(1024**3).encode())],
            },
            receive,
            send,
        )

    asyncio.run(run())
    assert not called
    assert sent[0]["status"] == 413


def test_api_bloqueia_origem_externa_e_valida_patch(temp_root) -> None:
    from apps.api.app.main import block_remote_clients
    from apps.api.app.routers.projects import CreatePayload, UpdatePayload, post_project

    async def exercise_origin() -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/projects",
                "headers": [(b"origin", b"https://site-malicioso.example")],
                "client": ("127.0.0.1", 12345),
            }
        )
        response = await block_remote_clients(request, lambda _: Response(status_code=204))
        assert response.status_code == 403

    asyncio.run(exercise_origin())
    created = post_project(CreatePayload(title="seguro"))
    assert created["id"]
    with pytest.raises(ValidationError):
        UpdatePayload.model_validate({"render": {"width": 999_999}})
    with pytest.raises(ValidationError):
        UpdatePayload.model_validate({"voice": {"campo_inventado": 1}})


def test_router_de_midia_bloqueia_traversal_e_symlink(temp_root, tmp_path) -> None:
    from fastapi import HTTPException

    from apps.api.app.routers.media import _resolve

    allowed = temp_root / "projects" / "video.mp4"
    allowed.write_bytes(b"video")
    assert _resolve(str(allowed)) == allowed.resolve()

    outside = tmp_path / "segredo.txt"
    outside.write_text("privado", encoding="utf-8")
    with pytest.raises(HTTPException) as traversal:
        _resolve(str(outside))
    assert traversal.value.status_code == 403

    link = temp_root / "projects" / "atalho.txt"
    link.symlink_to(outside)
    with pytest.raises(HTTPException) as symlink:
        _resolve(str(link))
    assert symlink.value.status_code == 403


def test_routers_rejeitam_ids_com_caminho(temp_root) -> None:
    from fastapi import HTTPException

    from apps.api.app.routers.templates import TemplatePatch, patch_template
    from apps.api.app.routers.voice import get_profile

    with pytest.raises(HTTPException) as template_error:
        patch_template("..", TemplatePatch())
    assert template_error.value.status_code == 400
    with pytest.raises(HTTPException) as voice_error:
        get_profile("..")
    assert voice_error.value.status_code == 400


def test_estado_terminal_nao_e_ressuscitado(tmp_path) -> None:
    from core.jobs.store import JobState, JobStore

    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create("p", "render")
    assert store.request_cancel(job.id)
    store.update(job.id, state=JobState.LIPSYNC, progress=0.8)
    current = store.get(job.id)
    assert current is not None
    assert current.state is JobState.CANCELLED
    assert current.progress == 0.0


def test_reset_orphans_preserva_fila(tmp_path) -> None:
    from core.jobs.store import JobState, JobStore

    store = JobStore(tmp_path / "jobs.sqlite3")
    queued = store.create("p", "render")
    running = store.create("p", "render")
    store.update(running.id, state=JobState.PREPARING)
    assert store.reset_orphans() == 1
    assert store.get(queued.id).state is JobState.QUEUED
    assert store.get(running.id).state is JobState.FAILED


def test_lock_de_instancia_e_exclusivo(tmp_path) -> None:
    from core.jobs.instance import InstanceAlreadyRunning, InstanceLock

    first = InstanceLock(tmp_path / "backend.lock")
    second = InstanceLock(tmp_path / "backend.lock")
    first.acquire()
    try:
        with pytest.raises(InstanceAlreadyRunning):
            second.acquire()
    finally:
        first.release()


def test_worker_sucesso_erro_e_cancelamento(temp_root) -> None:
    from core.worker.runner import WorkerCancelled, WorkerError, run_worker

    progress: list[float] = []
    worker_env = {"PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    result = run_worker(
        env="backend",
        module="tests.worker_stub",
        request={"action": "success", "value": 7},
        stage="test",
        on_progress=lambda pct, _: progress.append(pct),
        extra_env=worker_env,
    )
    assert result.data == {"value": 7}
    assert progress == [0.5]

    with pytest.raises(WorkerError) as failure:
        run_worker(
            env="backend",
            module="tests.worker_stub",
            request={"action": "error"},
            stage="test",
            extra_env=worker_env,
        )
    assert failure.value.error_type == "test_error"
    assert failure.value.hint == "corrija a entrada"

    with pytest.raises(WorkerError) as missing:
        run_worker(
            env="backend",
            module="tests.worker_stub",
            request={"action": "no_result"},
            stage="test",
            extra_env=worker_env,
        )
    assert missing.value.error_type == "no_result"

    with pytest.raises(WorkerError) as timed_out:
        run_worker(
            env="backend",
            module="tests.worker_stub",
            request={"action": "slow"},
            stage="test",
            timeout=0,
            extra_env=worker_env,
        )
    assert timed_out.value.error_type == "timeout"

    started = time.monotonic()
    with pytest.raises(WorkerCancelled):
        run_worker(
            env="backend",
            module="tests.worker_stub",
            request={"action": "slow"},
            stage="test",
            should_cancel=lambda: time.monotonic() - started > 0.15,
            extra_env=worker_env,
        )
    assert time.monotonic() - started < 2.0


def test_ciclo_musetalk_preserva_timeline_com_placeholder() -> None:
    from services.lipsync.worker.musetalk_worker import (
        _ping_pong_cycle,
        _valid_cycle_targets,
    )

    placeholder = (0, 0, 0, 0)
    coords = _ping_pong_cycle(["face-a", placeholder, "face-b"])
    latents = _ping_pong_cycle(["latent-a", None, "latent-b"])
    assert coords == ["face-a", placeholder, "face-b", "face-b", placeholder, "face-a"]
    assert _valid_cycle_targets(8, coords, latents, placeholder) == [0, 2, 3, 5, 6]
    assert _ping_pong_cycle(["único"]) == ["único", "único"]


def test_orquestracao_resume_e_invalidacao_em_cascata(temp_root) -> None:
    from core.pipeline.runner import run_pipeline
    from core.pipeline.stages import Stage
    from core.storage.project import create_project

    project = create_project("resume completo")
    executed: list[str] = []

    def fake_stage(name: str):
        def run(ctx):
            if not ctx.should_run(name):
                return
            executed.append(name)
            output = project.stages_dir / f"{name}.bin"
            output.write_bytes(name.encode())
            project.record_stage(name, output=output)
            project.save()
        return run

    plan = [
        Stage("tts", "TTS", fake_stage("tts")),
        Stage("template", "Template", fake_stage("template")),
        Stage("render", "Render", fake_stage("render")),
    ]

    run_pipeline(project, stages=plan)
    assert executed == ["tts", "template", "render"]

    executed.clear()
    run_pipeline(project, stages=plan)
    assert executed == []

    project.stage_output("tts").unlink()
    executed.clear()
    run_pipeline(project, stages=plan)
    assert executed == ["tts", "template", "render"]

    executed.clear()
    run_pipeline(project, stages=plan, force_stages={"template"})
    assert executed == ["template", "render"]


def test_toda_rota_de_upload_tem_teto_de_corpo(temp_root) -> None:
    """Rota nova de upload não pode escapar do limitador de corpo.

    O `UploadFile` do FastAPI protege a RAM, não o disco: sem entrada no
    middleware, o parser multipart grava o corpo inteiro antes de qualquer
    código do projeto rodar, e o teto do `copy_file_limited` chega tarde demais.

    Este teste existe porque foi exatamente assim que a rota de B-roll entrou —
    passando pelo `copy_file_limited` e esquecendo o middleware. Enumerar as
    rotas em vez de listá-las à mão é o que impede a próxima repetir.
    """
    import inspect
    import re

    from apps.api.app.main import app
    from apps.api.app.uploads import RequestBodyLimitMiddleware

    def caminhos_de_upload(rotas, prefixo=""):
        """Coleta (caminho completo) de toda rota que receba um UploadFile.

        Esta versão do FastAPI não achata os routers incluídos: guarda cada um
        num `_IncludedRouter` com o prefixo à parte. Percorrer só `app.routes`
        encontraria zero rotas — e um teste que não encontra nada passa em
        silêncio, que é pior que não existir.
        """
        for rota in rotas:
            interno = getattr(rota, "original_router", None)
            if interno is not None:
                contexto = getattr(rota, "include_context", None)
                yield from caminhos_de_upload(
                    interno.routes, prefixo + getattr(contexto, "prefix", "")
                )
                continue

            endpoint = getattr(rota, "endpoint", None)
            if endpoint is None:
                continue
            try:
                assinatura = inspect.signature(endpoint)
            except (TypeError, ValueError):
                continue
            # Os routers usam `from __future__ import annotations`, então a
            # anotação chega como string.
            if any(
                "UploadFile" in str(parametro.annotation)
                for parametro in assinatura.parameters.values()
            ):
                yield prefixo + rota.path

    # O middleware decide pelo caminho real da requisição, não pelo template.
    achadas = [re.sub(r"\{[^}]+\}", "x", c) for c in caminhos_de_upload(app.routes)]

    assert achadas, "nenhuma rota de upload encontrada — o teste cegou"
    sem_teto = [
        caminho
        for caminho in achadas
        if RequestBodyLimitMiddleware._limit(caminho) is None
    ]
    assert not sem_teto, (
        f"rota(s) de upload sem teto de corpo no middleware: {sem_teto}"
    )
