"""Metadados de um template de vídeo.

Um *template* é um vídeo vertical real do usuário — a matéria-prima do
Production Mode. Nada é gerado por diffusion: o rosto, o corpo, a roupa, o
cenário e os gestos são os verdadeiros.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

METADATA_SUFFIX = ".json"


class Energy(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Gestures(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Camera(str, Enum):
    CLOSE = "close"
    MEDIUM_CLOSE = "medium_close"
    MEDIUM = "medium"
    WIDE = "wide"


class Style(str, Enum):
    TALKING_HEAD = "talking_head"
    PODCAST = "podcast"
    SELFIE = "selfie"
    WALKING = "walking"
    SEATED = "seated"
    STANDING = "standing"


@dataclass
class TemplateMetadata:
    """`data/identity/templates/<id>.json`."""

    id: str
    duration: float
    orientation: str = "vertical"
    style: str = Style.TALKING_HEAD.value
    energy: str = Energy.MEDIUM.value
    gestures: str = Gestures.MEDIUM.value
    camera: str = Camera.MEDIUM_CLOSE.value
    background: str = "neutral"
    loopable: bool = False

    # Preenchidos automaticamente pela indexação.
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    video_path: str = ""
    face_detected: bool | None = None
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "TemplateMetadata":
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width if self.width and self.height else True

    @property
    def aspect_ratio(self) -> float:
        return (self.width / self.height) if self.height else 9 / 16
