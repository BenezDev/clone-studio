"""Interface de geração de vídeo.

O restante da aplicação fala apenas com `GenerativeVideoEngine`. Nada fora
deste pacote sabe que existe Wan2.2, ComfyUI ou qualquer workflow JSON — trocar
a engine não deve exigir mudança em pipeline, UI ou CLI.

Este módulo é OPCIONAL. Se nenhuma engine estiver disponível, o B-roll vem da
biblioteca local e o programa funciona por completo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ProgressCallback = Callable[[float, str], None]


class GenerativeUnavailable(RuntimeError):
    """A engine generativa não pode rodar aqui."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass
class GenerationRequest:
    prompt: str
    output_path: Path
    image: Path | None = None
    duration: float = 3.0
    width: int = 720
    height: int = 1280
    fps: int = 16
    seed: int | None = None
    negative_prompt: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    output: Path
    duration: float
    engine: str
    model: str
    seconds_taken: float


@dataclass
class EngineAvailability:
    available: bool
    engine: str
    reason: str = ""
    hint: str = ""
    estimated_seconds_per_second: float | None = None


class GenerativeVideoEngine(ABC):
    """Contrato de qualquer engine de vídeo generativo."""

    name: str = "abstract"

    @abstractmethod
    def generate(
        self,
        request: GenerationRequest,
        on_progress: ProgressCallback | None = None,
    ) -> GenerationResult:
        """Gera um clipe a partir de um prompt (e opcionalmente uma imagem)."""

    @abstractmethod
    def availability(self) -> EngineAvailability:
        """Diz se a engine pode rodar agora, e por que não, se for o caso."""


def get_generative_engine() -> GenerativeVideoEngine | None:
    """Devolve a engine configurada, ou None quando indisponível.

    Nunca levanta exceção: B-roll generativo é opcional por design, e o
    chamador deve poder simplesmente seguir sem ele.
    """
    from core.config.loader import load_settings

    settings = load_settings()
    if not settings.broll.generative.enabled:
        return None

    if settings.broll.generative.engine == "wan22":
        from services.comfyui.wan22 import Wan22Engine

        engine = Wan22Engine()
        return engine if engine.availability().available else None

    return None
