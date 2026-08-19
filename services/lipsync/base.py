"""Interface de lip-sync."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


@dataclass
class LipSyncConfig:
    fps: int = 25
    batch_size: int = 1
    bbox_shift: int = 0
    extra_margin: int = 10
    parsing_mode: str = "jaw"
    version: str = "v15"
    left_cheek_width: int = 90
    right_cheek_width: int = 90
    cleanup: bool = True


@dataclass
class LipSyncResult:
    output: Path
    frames: int
    fps: float
    seconds_per_frame: float
    duration_seconds: float
    log_file: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "frames": self.frames,
            "fps": self.fps,
            "seconds_per_frame": self.seconds_per_frame,
            "duration_seconds": self.duration_seconds,
        }


class LipSyncEngine(ABC):
    name: str = "abstract"

    @abstractmethod
    def process(
        self,
        video_path: Path,
        audio_path: Path,
        output_path: Path,
        config: LipSyncConfig | None = None,
        on_progress: ProgressCallback | None = None,
        cache_seed: dict | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> LipSyncResult:
        """Aplica a fala do áudio ao rosto do vídeo.

        `cache_seed` descreve de onde o vídeo veio (templates de origem e como
        foram compostos). Serve para o cache de preprocessamento, que não pode
        depender dos bytes do arquivo recebido — encoders não são
        determinísticos. Quando omitido, cai no hash do próprio arquivo.
        """

    @abstractmethod
    def healthcheck(self):
        """Estado da engine."""
