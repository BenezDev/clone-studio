"""Esquema tipado da configuração da aplicação."""

from __future__ import annotations

import os
import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

HardwareProfile = Literal[
    "auto", "CPU_ONLY", "LOW_VRAM", "MID_VRAM", "HIGH_VRAM", "EXTREME"
]


def _local_service_url(value: str) -> str:
    """Aceita apenas serviços HTTP no próprio computador.

    Esses endpoints recebem roteiros e workflows. Permitir um host remoto
    violaria o contrato local-first mesmo com ``allow_outbound_media`` falso.
    """
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError as exc:
        raise ValueError("URL de serviço local inválida.") from exc
    if parsed.scheme not in {"http", "https"} or not host or parsed.username:
        raise ValueError("Serviços locais exigem URL http(s) sem credenciais.")
    if parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URL de serviço local não aceita credenciais, query ou fragmento.")
    if host.lower() != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        except ValueError as exc:
            raise ValueError(
                "O endpoint de inferência deve apontar para localhost/loopback; "
                "roteiros e mídia não podem sair da máquina."
            ) from exc
    return value.rstrip("/")


class AppConfig(BaseModel):
    name: str = "Local Clone Studio"
    language: str = "pt-BR"
    host: str = "127.0.0.1"
    api_port: int = Field(default=8756, ge=1, le=65535)
    web_port: int = Field(default=3000, ge=1, le=65535)
    allow_external_bind: bool = False

    @model_validator(mode="after")
    def _enforce_loopback(self) -> "AppConfig":
        """Trava de privacidade: o backend só escuta fora do loopback com
        consentimento duplo (config + variável de ambiente)."""
        loopback = {"127.0.0.1", "localhost", "::1"}
        if self.host in loopback:
            return self
        override = os.environ.get("CLONE_STUDIO_ALLOW_EXTERNAL_BIND") == "1"
        if self.allow_external_bind and override:
            return self
        raise ValueError(
            f"host='{self.host}' expõe o backend na rede. Este projeto é "
            "local-first. Para permitir mesmo assim, defina "
            "app.allow_external_bind: true E exporte "
            "CLONE_STUDIO_ALLOW_EXTERNAL_BIND=1."
        )


class PrivacyConfig(BaseModel):
    telemetry: bool = False
    analytics: bool = False
    auto_update_check: bool = False
    allow_outbound_media: bool = False

    @field_validator("telemetry", "analytics", "allow_outbound_media")
    @classmethod
    def _must_stay_off(cls, value: bool) -> bool:
        if value:
            raise ValueError(
                "Telemetria, analytics e envio de mídia são desativados por "
                "design neste projeto e não podem ser habilitados via config."
            )
        return value


class HardwareConfig(BaseModel):
    profile: HardwareProfile = "auto"
    cpu_threads: int = Field(default=0, ge=0, le=512)
    max_resident_engines: int = Field(default=1, ge=1, le=4)


class TTSModelsConfig(BaseModel):
    high: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    low: str = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"


class TTSConfig(BaseModel):
    engine: str = "qwen3"
    model: str = "auto"
    models: TTSModelsConfig = Field(default_factory=TTSModelsConfig)
    language: str = "Portuguese"
    sample_rate: int = Field(default=24000, ge=8000, le=192000)
    ab_variants: int = Field(default=3, ge=1, le=5)
    seed: int | None = None
    default_speed: float = Field(default=1.0, ge=0.25, le=4.0)
    cache_voice_prompt: bool = True
    max_new_tokens: int = Field(default=4096, ge=64, le=16384)

    def resolve_model(self, profile: str) -> str:
        if self.model != "auto":
            return self.model
        if profile in {"CPU_ONLY", "LOW_VRAM"}:
            return self.models.low
        return self.models.high


class LipSyncConfig(BaseModel):
    engine: str = "musetalk"
    version: str = "v15"
    fps: int = Field(default=25, ge=1, le=120)
    batch_size: int = Field(default=1, ge=1, le=64)
    bbox_shift: int = Field(default=0, ge=-100, le=100)
    cache_preprocessing: bool = True
    warn_frames_above: int = Field(default=900, ge=1, le=100_000)


class TranscriptionModelsConfig(BaseModel):
    cpu: str = "small"
    gpu: str = "large-v3"


class TranscriptionConfig(BaseModel):
    engine: str = "auto"
    models: TranscriptionModelsConfig = Field(
        default_factory=TranscriptionModelsConfig
    )
    language: str = "pt"
    compute_type: str = "auto"
    word_timestamps: bool = True

    def resolve_engine(self, profile: str) -> str:
        if self.engine != "auto":
            return self.engine
        return "faster-whisper" if profile == "CPU_ONLY" else "whisperx"

    def resolve_model(self, profile: str) -> str:
        return self.models.cpu if profile == "CPU_ONLY" else self.models.gpu

    def resolve_compute_type(self, profile: str) -> str:
        if self.compute_type != "auto":
            return self.compute_type
        return "int8" if profile == "CPU_ONLY" else "float16"


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "auto"
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    timeout_seconds: int = Field(default=180, ge=1, le=3600)
    preferred_models: list[str] = Field(default_factory=list)

    _validate_base_url = field_validator("base_url")(_local_service_url)


class CaptionsConfig(BaseModel):
    preset: Literal["minimal", "hormozi", "clean", "big_tech", "podcast", "karaoke"] = "hormozi"
    max_words_per_cue: int = Field(default=3, ge=1, le=20)
    uppercase: bool = False
    safe_top: float = Field(default=0.14, ge=0.0, le=0.5)
    safe_bottom: float = Field(default=0.22, ge=0.0, le=0.5)
    font_family: str = "Montserrat"
    emojis: bool = False


class VideoConfig(BaseModel):
    width: int = Field(default=1080, ge=240, le=7680)
    height: int = Field(default=1920, ge=240, le=7680)
    fps: int = Field(default=30, ge=1, le=120)
    video_codec: Literal["libx264", "libx265"] = "libx264"
    audio_codec: Literal["aac"] = "aac"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    audio_sample_rate: int = Field(default=48000, ge=8000, le=192000)
    crf: int = Field(default=18, ge=0, le=51)
    preset: Literal[
        "ultrafast", "superfast", "veryfast", "faster", "fast",
        "medium", "slow", "slower", "veryslow",
    ] = "medium"
    loudness_lufs: float = Field(default=-14.0, ge=-70.0, le=-5.0)
    loudness_true_peak: float = Field(default=-1.5, ge=-20.0, le=0.0)


class PreviewConfig(BaseModel):
    width: int = Field(default=540, ge=120, le=3840)
    height: int = Field(default=960, ge=120, le=3840)
    crf: int = Field(default=30, ge=0, le=51)
    preset: str = Field(default="veryfast", max_length=32)
    max_seconds: int = Field(default=10, ge=0, le=600)


class EditingConfig(BaseModel):
    preset: str = "clean"
    auto_cut: bool = False
    min_shot_seconds: float = 2.0
    max_shot_seconds: float = 8.0
    punch_in_strength: float = 1.08


class GenerativeBrollConfig(BaseModel):
    enabled: bool = False
    engine: str = "wan22"
    comfyui_url: str = "http://127.0.0.1:8188"

    _validate_comfyui_url = field_validator("comfyui_url")(_local_service_url)


class BrollConfig(BaseModel):
    enabled: bool = True
    library_only: bool = True
    generative: GenerativeBrollConfig = Field(
        default_factory=GenerativeBrollConfig
    )


class JobsConfig(BaseModel):
    max_concurrent: Literal[1] = 1
    keep_completed_days: int = Field(default=30, ge=1, le=3650)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    keep_stage_logs: bool = True


class Settings(BaseModel):
    """Configuração completa da aplicação."""

    app: AppConfig = Field(default_factory=AppConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    lipsync: LipSyncConfig = Field(default_factory=LipSyncConfig)
    transcription: TranscriptionConfig = Field(
        default_factory=TranscriptionConfig
    )
    llm: LLMConfig = Field(default_factory=LLMConfig)
    captions: CaptionsConfig = Field(default_factory=CaptionsConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    preview: PreviewConfig = Field(default_factory=PreviewConfig)
    editing: EditingConfig = Field(default_factory=EditingConfig)
    broll: BrollConfig = Field(default_factory=BrollConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
