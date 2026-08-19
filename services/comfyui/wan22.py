"""Adapter do Wan2.2 sobre o ComfyUI.

Único arquivo do projeto que sabe que a engine de vídeo generativo é o Wan2.2.

Este módulo é OPT-IN e nunca é requisito. Ele se declara indisponível — com o
motivo real — quando falta GPU, VRAM, o serviço do ComfyUI, os pesos ou os nós
do workflow. Nenhuma dessas situações deve derrubar o resto do programa.
"""

from __future__ import annotations

import time
from pathlib import Path

from core import platform_hints as hints
from core.config.loader import active_profile, get_hardware, load_settings
from core.licensing.registry import get_registry
from core.media import ffmpeg
from core.storage.paths import get_paths
from services.comfyui.base import (
    EngineAvailability,
    GenerationRequest,
    GenerationResult,
    GenerativeUnavailable,
    GenerativeVideoEngine,
    ProgressCallback,
)
from services.comfyui.client import (
    ComfyUIClient,
    ComfyUIError,
    ComfyUIUnavailable,
)

REGISTRY_KEY = "wan22_ti2v_5b"
WORKFLOW_T2V = "wan22_t2v"
WORKFLOW_I2V = "wan22_i2v"


class Wan22Engine(GenerativeVideoEngine):
    name = "wan22"

    def __init__(self) -> None:
        self.settings = load_settings()
        self.paths = get_paths()
        self.hardware = get_hardware()
        self.profile = active_profile()
        self.client = ComfyUIClient(self.settings.broll.generative.comfyui_url)

    # -- disponibilidade -------------------------------------------------

    def availability(self) -> EngineAvailability:
        entry = get_registry().get(REGISTRY_KEY)

        if not self.settings.broll.generative.enabled:
            return EngineAvailability(
                available=False,
                engine=self.name,
                reason="B-roll generativo está desativado na configuração.",
                hint="Ative em Settings ou em config/local.yaml "
                     "(broll.generative.enabled).",
            )

        if entry is not None:
            verdict = get_registry().check_hardware(entry, self.hardware)
            if not verdict.compatible:
                return EngineAvailability(
                    available=False,
                    engine=self.name,
                    reason="; ".join(verdict.reasons),
                    hint="Difusão de vídeo exige GPU dedicada com VRAM "
                         "significativa. O restante do pipeline funciona sem isso.",
                )
            if not entry.installed:
                return EngineAvailability(
                    available=False,
                    engine=self.name,
                    reason=f"Pesos do {entry.display_name} não instalados "
                           f"(~{entry.size_gb:.0f} GB).",
                    hint=hints.models("install", REGISTRY_KEY),
                )

        if not self.client.online():
            return EngineAvailability(
                available=False,
                engine=self.name,
                reason=f"ComfyUI offline em {self.client.base_url}.",
                hint="Suba o ComfyUI como serviço separado antes de usar.",
            )

        try:
            nodes = self.client.object_info()
        except (ComfyUIUnavailable, ComfyUIError) as exc:
            return EngineAvailability(
                available=False, engine=self.name, reason=str(exc)
            )

        missing = [
            node for node in ("WanVideoSampler", "WanVideoModelLoader")
            if node not in nodes
        ]
        if missing:
            return EngineAvailability(
                available=False,
                engine=self.name,
                reason=f"Nós do Wan2.2 ausentes no ComfyUI: {', '.join(missing)}.",
                hint="Instale a extensão do Wan2.2 no ComfyUI.",
            )

        return EngineAvailability(available=True, engine=self.name)

    # -- geração ---------------------------------------------------------

    def generate(
        self,
        request: GenerationRequest,
        on_progress: ProgressCallback | None = None,
    ) -> GenerationResult:
        status = self.availability()
        if not status.available:
            raise GenerativeUnavailable(status.reason, hint=status.hint)

        workflow_name = WORKFLOW_I2V if request.image else WORKFLOW_T2V
        try:
            workflow = self.client.load_workflow(workflow_name)
        except FileNotFoundError as exc:
            raise GenerativeUnavailable(
                str(exc),
                hint="Exporte o workflow do ComfyUI em formato API e salve em "
                     f"workflows/comfyui/{workflow_name}.json.",
            ) from exc

        overrides = {
            "positive_prompt": {"text": request.prompt},
            "negative_prompt": {"text": request.negative_prompt},
            "video_settings": {
                "width": request.width,
                "height": request.height,
                "num_frames": max(1, int(request.duration * request.fps)),
                "fps": request.fps,
            },
        }
        if request.seed is not None:
            overrides["sampler"] = {"seed": request.seed}
        if request.image:
            overrides["input_image"] = {"image": str(request.image)}

        try:
            patched = self.client.apply_inputs(workflow, overrides)
            if on_progress:
                on_progress(0.02, "enfileirando no ComfyUI…")

            started = time.monotonic()
            prompt_id = self.client.queue(patched)
            entry = self.client.wait(prompt_id, on_progress=on_progress)

            if on_progress:
                on_progress(0.96, "baixando o resultado…")
            output = self.client.download_output(entry, request.output_path)
        except (ComfyUIUnavailable, ComfyUIError) as exc:
            raise GenerativeUnavailable(
                f"Falha na geração: {exc}",
                hint="Verifique o log do ComfyUI; ele reporta o erro do nó "
                     "que falhou.",
            ) from exc

        info = ffmpeg.validate_video(output, min_duration=0.2)
        if on_progress:
            on_progress(1.0, "clipe gerado")

        return GenerationResult(
            output=output,
            duration=info.duration,
            engine=self.name,
            model=REGISTRY_KEY,
            seconds_taken=time.monotonic() - started,
        )
