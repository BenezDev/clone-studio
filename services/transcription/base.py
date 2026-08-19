"""Interface de transcrição com timestamps por palavra."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


@dataclass
class Word:
    text: str
    start: float
    end: float
    probability: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Segment:
    text: str
    start: float
    end: float
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    segments: list[Segment]
    language: str
    duration: float
    engine: str
    model: str

    @property
    def words(self) -> list[Word]:
        result: list[Word] = []
        for segment in self.segments:
            result.extend(segment.words)
        return result

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "duration": self.duration,
            "engine": self.engine,
            "model": self.model,
            "segments": [asdict(s) for s in self.segments],
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        raw = json.loads(path.read_text(encoding="utf-8"))
        segments = [
            Segment(
                text=s["text"],
                start=s["start"],
                end=s["end"],
                words=[Word(**w) for w in s.get("words", [])],
            )
            for s in raw.get("segments", [])
        ]
        return cls(
            segments=segments,
            language=raw.get("language", "pt"),
            duration=raw.get("duration", 0.0),
            engine=raw.get("engine", ""),
            model=raw.get("model", ""),
        )


class TranscriptionEngine(ABC):
    name: str = "abstract"

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "pt",
        initial_prompt: str = "",
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> Transcript:
        """Transcreve com timestamps por palavra."""

    @abstractmethod
    def healthcheck(self):
        """Estado da engine."""
