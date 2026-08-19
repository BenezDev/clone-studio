"""Interface de TTS.

O restante da aplicação fala apenas com `TTSEngine`. Nenhum módulo fora de
`services/tts/` deve importar `qwen_tts`, conhecer nomes de repositório do
HuggingFace ou saber que existe um subprocesso envolvido.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]

VOICE_PROFILE_FILENAME = "voice_profile.json"


@dataclass
class VoiceReference:
    """Uma amostra de referência dentro de um perfil de voz."""

    audio_path: str
    transcript: str
    duration: float
    sample_rate: int
    label: str = ""
    primary: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoiceProfile:
    """`voice_profile.json` — a identidade vocal do usuário."""

    id: str
    display_name: str
    language: str = "Portuguese"
    created_at: str = ""
    updated_at: str = ""
    references: list[VoiceReference] = field(default_factory=list)
    default_speed: float = 1.0
    default_emotion: str = "neutral"
    notes: str = ""
    # Preenchido pelo A/B: qual configuração o usuário preferiu.
    preferred_settings: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_reference(self) -> VoiceReference | None:
        for ref in self.references:
            if ref.primary:
                return ref
        return self.references[0] if self.references else None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target = directory / VOICE_PROFILE_FILENAME
        target.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return target

    @classmethod
    def load(cls, directory: Path) -> "VoiceProfile":
        target = directory / VOICE_PROFILE_FILENAME
        if not target.exists():
            raise FileNotFoundError(
                f"Perfil de voz não encontrado em {target}. "
                "Cadastre sua voz na página Voice antes de gerar áudio."
            )
        raw = json.loads(target.read_text(encoding="utf-8"))
        references = [VoiceReference(**r) for r in raw.pop("references", [])]
        profile = cls(**raw)
        profile.references = references
        return profile


@dataclass
class SynthesisRequest:
    """Um pedido de síntese."""

    text: str
    voice_profile: VoiceProfile
    output_path: Path
    language: str = "Portuguese"
    speed: float = 1.0
    emotion: str = "neutral"
    seed: int | None = None
    variants: int = 1
    max_new_tokens: int = 4096


@dataclass
class SynthesisVariant:
    path: Path
    duration: float
    sample_rate: int
    seed: int | None
    label: str


@dataclass
class SynthesisResult:
    variants: list[SynthesisVariant]
    engine: str
    model: str
    duration_seconds: float
    log_file: Path | None = None

    @property
    def primary(self) -> SynthesisVariant:
        return self.variants[0]


@dataclass
class HealthStatus:
    ok: bool
    engine: str
    model: str = ""
    detail: str = ""
    installed: bool = False
    env_ready: bool = False
    weights_ready: bool = False
    hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TTSEngine(ABC):
    """Contrato de qualquer engine de TTS do projeto."""

    name: str = "abstract"

    @abstractmethod
    def synthesize(
        self,
        request: SynthesisRequest,
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> SynthesisResult:
        """Gera fala a partir de texto usando um perfil de voz."""

    @abstractmethod
    def clone_voice(
        self,
        audio_path: Path,
        transcript: str,
        profile_id: str,
        display_name: str = "",
        on_progress: ProgressCallback | None = None,
    ) -> VoiceProfile:
        """Cadastra (ou atualiza) um perfil de voz a partir de uma amostra."""

    @abstractmethod
    def healthcheck(self) -> HealthStatus:
        """Diz se a engine consegue rodar agora, e o que falta se não consegue."""
