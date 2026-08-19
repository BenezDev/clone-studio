"""Adapter do faster-whisper (CTranslate2).

Engine padrão de transcrição em CPU. Produz timestamps por palavra, que é o
que as legendas estilo Reels/TikTok exigem.
"""

from __future__ import annotations

from pathlib import Path

from core import platform_hints as hints
from core.config.loader import active_profile, get_hardware, load_settings
from core.media import ffmpeg
from core.storage.cache import get_cache
from core.storage.paths import get_paths
from core.worker.runner import (
    EngineNotInstalled,
    WorkerError,
    engine_available,
    run_worker,
)
from services.tts.base import HealthStatus
from services.transcription.base import (
    CancelCheck,
    ProgressCallback,
    Segment,
    Transcript,
    TranscriptionEngine,
    Word,
)

WORKER_MODULE = "services.transcription.worker.faster_whisper_worker"
ENV_NAME = "whisper"


class FasterWhisperEngine(TranscriptionEngine):
    name = "faster-whisper"

    def __init__(self) -> None:
        self.settings = load_settings()
        self.paths = get_paths()
        self.profile = active_profile()
        self.hardware = get_hardware()

    @property
    def model_name(self) -> str:
        return self.settings.transcription.resolve_model(self.profile)

    @property
    def registry_key(self) -> str:
        """Chave no model_registry para o modelo ativo ('small' -> ...small)."""
        slug = self.model_name.replace("-", "_").replace(".", "_")
        return f"faster_whisper_{slug}"

    def _resolve_model_source(self) -> str:
        """Caminho local dos pesos, ou o nome curto se ainda não instalados.

        Sem isso, o faster-whisper baixaria o modelo de novo para o seu próprio
        cache mesmo com os pesos já presentes em models/ — meio giga duplicado.
        """
        from core.licensing.registry import get_registry

        entry = get_registry().get(self.registry_key)
        if entry is not None:
            directory = entry.install_dir
            if (directory / "model.bin").exists():
                return str(directory)
        return self.model_name

    @property
    def _download_root(self) -> Path:
        """Destino de download só quando os pesos NÃO estão no registro."""
        return self.paths.models / "faster_whisper"

    def _device(self) -> tuple[str, str]:
        if self.profile == "CPU_ONLY":
            return "cpu", self.settings.transcription.resolve_compute_type(self.profile)
        return "cuda", self.settings.transcription.resolve_compute_type(self.profile)

    def _cpu_threads(self) -> int:
        configured = self.settings.hardware.cpu_threads
        if configured > 0:
            return configured
        return self.hardware.cpu_cores_physical or 4

    def healthcheck(self) -> HealthStatus:
        status = HealthStatus(
            ok=False,
            engine=self.name,
            model=self.model_name,
            env_ready=engine_available(ENV_NAME),
        )
        if not status.env_ready:
            status.detail = f"Ambiente '{ENV_NAME}' não instalado."
            status.hints.append(hints.install("whisper"))
            return status

        try:
            result = run_worker(
                env=ENV_NAME,
                module=WORKER_MODULE,
                stage="captions-healthcheck",
                request={"action": "healthcheck", "model": self.model_name},
                timeout=120,
            )
        except (EngineNotInstalled, WorkerError) as exc:
            status.detail = str(exc)
            return status

        data = result.data
        status.ok = True
        status.installed = True
        status.weights_ready = True
        status.detail = (
            f"faster-whisper {data.get('faster_whisper')} · "
            f"ctranslate2 {data.get('ctranslate2')} · "
            f"cuda_devices={data.get('cuda_devices')}"
        )
        return status

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "pt",
        initial_prompt: str = "",
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> Transcript:
        audio_path = Path(audio_path)
        ffmpeg.validate_audio(audio_path, min_duration=0.1)

        device, compute_type = self._device()
        model_source = self._resolve_model_source()
        cache = get_cache("transcripts")
        entry = cache.entry(
            files=[audio_path],
            params={
                "model": self.model_name,
                "language": language,
                "compute_type": compute_type,
                "prompt": initial_prompt,
            },
            label="asr",
        )

        target = entry.file("transcript.json")
        if entry.hit and target.exists():
            if on_progress:
                on_progress(1.0, "Transcrição reaproveitada do cache.")
            return Transcript.load(target)

        self._download_root.mkdir(parents=True, exist_ok=True)

        result = run_worker(
            env=ENV_NAME,
            module=WORKER_MODULE,
            stage="captions",
            request={
                "action": "transcribe",
                "audio": str(audio_path),
                "model": model_source,
                "device": device,
                "compute_type": compute_type,
                "cpu_threads": self._cpu_threads(),
                "language": language,
                "initial_prompt": initial_prompt,
                "word_timestamps": self.settings.transcription.word_timestamps,
                "download_root": str(self._download_root),
            },
            on_progress=on_progress,
            should_cancel=should_cancel,
        )

        data = result.data
        segments = [
            Segment(
                text=s["text"],
                start=s["start"],
                end=s["end"],
                words=[Word(**w) for w in s.get("words", [])],
            )
            for s in data.get("segments", [])
        ]
        transcript = Transcript(
            segments=segments,
            language=data.get("language", language),
            duration=data.get("duration", 0.0),
            engine=self.name,
            # Guarda o nome curto, não o caminho absoluto: o project.json
            # precisa ser reproduzível em outra máquina.
            model=self.model_name,
        )

        entry.directory.mkdir(parents=True, exist_ok=True)
        transcript.save(target)
        entry.commit({"model": self.model_name, "words": len(transcript.words)})
        return transcript


def get_transcription_engine() -> TranscriptionEngine:
    """Seleciona a engine conforme a configuração e o hardware.

    WhisperX oferece alinhamento por fonema mais preciso, mas exige torch +
    pyannote. Em CPU_ONLY o custo não compensa: o faster-whisper já entrega
    timestamps por palavra bons o bastante para legendas.
    """
    settings = load_settings()
    profile = active_profile()
    engine = settings.transcription.resolve_engine(profile)

    if engine == "whisperx":
        try:
            from services.transcription.whisperx_engine import WhisperXEngine

            candidate = WhisperXEngine()
            if candidate.healthcheck().ok:
                return candidate
        except ImportError:
            pass
    return FasterWhisperEngine()
