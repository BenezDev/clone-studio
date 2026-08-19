"""Adapter do MuseTalk 1.5.

Único arquivo do projeto que sabe que a engine de lip-sync é o MuseTalk.
O trabalho roda em subprocesso, no ambiente Python 3.10 isolado.

O que é preservado: rosto, barba, cabelo, pele, iluminação, cabeça, corpo,
fundo e gestos — porque a base é um vídeo real seu e apenas a região da boca
é regenerada e recomposta por face parsing.
"""

from __future__ import annotations

from pathlib import Path

from core import platform_hints as hints
from core.config.loader import active_profile, get_hardware, load_settings
from core.licensing.registry import get_registry
from core.media import ffmpeg
from core.storage.cache import hash_inputs
from core.storage.paths import get_paths
from core.worker.runner import (
    EngineNotInstalled,
    WorkerError,
    engine_available,
    run_worker,
)
from services.lipsync.base import (
    CancelCheck,
    LipSyncConfig,
    LipSyncEngine,
    LipSyncResult,
    ProgressCallback,
)
from services.tts.base import HealthStatus

WORKER_MODULE = "services.lipsync.worker.musetalk_worker"
ENV_NAME = "musetalk"
REGISTRY_KEY = "musetalk_15"
CACHE_FORMAT_VERSION = 3


class MuseTalkEngine(LipSyncEngine):
    name = "musetalk"

    def __init__(self) -> None:
        self.settings = load_settings()
        self.paths = get_paths()
        self.profile = active_profile()
        self.hardware = get_hardware()

    # -- localização -----------------------------------------------------

    @property
    def weights_dir(self) -> Path:
        entry = get_registry().get(REGISTRY_KEY)
        return entry.install_dir if entry else self.paths.models / REGISTRY_KEY

    @property
    def repo_dir(self) -> Path:
        return self.paths.external / self.name

    def _extra_env(self) -> dict[str, str]:
        """Mantém os downloads do torch.hub dentro dos pesos do MuseTalk.

        O módulo `face_detection` do MuseTalk busca o detector S3FD com
        `torch.hub.load_url`, que por padrão grava em ~/.cache/torch. Apontar
        TORCH_HOME para cá faz o peso pré-baixado ser encontrado — e é o que
        permite rodar offline.
        """
        return {"TORCH_HOME": str(self.weights_dir / "_torch")}

    def _device(self) -> str:
        return "cpu" if self.profile == "CPU_ONLY" else "cuda:0"

    def _cpu_threads(self) -> int:
        configured = self.settings.hardware.cpu_threads
        if configured > 0:
            return configured
        return self.hardware.cpu_cores_physical or 4

    def _default_config(self) -> LipSyncConfig:
        return LipSyncConfig(
            fps=self.settings.lipsync.fps,
            batch_size=self.settings.lipsync.batch_size,
            bbox_shift=self.settings.lipsync.bbox_shift,
            version=self.settings.lipsync.version,
        )

    # -- healthcheck -----------------------------------------------------

    def healthcheck(self) -> HealthStatus:
        status = HealthStatus(
            ok=False,
            engine=self.name,
            model=f"MuseTalk {self.settings.lipsync.version}",
            env_ready=engine_available(ENV_NAME),
            weights_ready=(self.weights_dir / "musetalkV15" / "unet.pth").exists(),
        )

        if not status.env_ready:
            status.detail = f"Ambiente '{ENV_NAME}' não instalado."
            status.hints.append(hints.install(musetalk=True))
            return status
        if not (self.repo_dir / "musetalk").is_dir():
            status.detail = f"Código do MuseTalk ausente em {self.repo_dir}."
            status.hints.append(hints.models("install", REGISTRY_KEY))
            return status
        if not status.weights_ready:
            status.detail = "Pesos do MuseTalk não baixados (~4.2 GB)."
            status.hints.append(hints.models("install", REGISTRY_KEY))
            return status

        try:
            result = run_worker(
                env=ENV_NAME,
                module=WORKER_MODULE,
                stage="lipsync-healthcheck",
                request={
                    "action": "healthcheck",
                    "repo_dir": str(self.repo_dir),
                    "weights_dir": str(self.weights_dir),
                },
                extra_env=self._extra_env(),
                timeout=300,
            )
        except (EngineNotInstalled, WorkerError) as exc:
            status.detail = getattr(exc, "message", str(exc))
            hint = getattr(exc, "hint", "")
            if hint:
                status.hints.append(hint)
            return status

        data = result.data
        status.ok = True
        status.installed = True
        status.detail = (
            f"torch {data.get('torch')} · mmcv {data.get('mmcv')} · "
            f"mmpose {data.get('mmpose')} · cuda={data.get('cuda')}"
        )
        if self.profile == "CPU_ONLY":
            status.hints.append(
                "Em CPU o lip-sync roda a alguns segundos por frame. "
                "Prefira templates e roteiros curtos."
            )
        return status

    # -- processamento ---------------------------------------------------

    def _template_cache_dir(
        self,
        video_path: Path,
        config: LipSyncConfig,
        cache_seed: dict | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> Path:
        """Cache de preprocessamento do template.

        A chave inclui os parâmetros que afetam o recorte do rosto: mudar
        bbox_shift precisa invalidar o cache, mudar o áudio não.

        `cache_seed` existe porque o vídeo recebido aqui é o **composto** pelo
        template engine, e o x264 multi-thread não é determinístico: duas
        composições idênticas produzem bytes diferentes. Usar o hash do arquivo
        composto faria o cache nunca acertar — e é justamente o passo de ~5 s
        por frame que mais compensa reaproveitar. Quando o chamador sabe de que
        fontes o vídeo veio, ele descreve isso aqui e a chave passa a ser
        estável.
        """
        params = {
            "cache_format": CACHE_FORMAT_VERSION,
            "bbox_shift": config.bbox_shift,
            "extra_margin": config.extra_margin,
            "version": config.version,
        }
        entry = get_registry().get(REGISTRY_KEY)
        if entry is not None:
            params["weights_revision"] = entry.revision
            params["code_revision"] = entry.code_revision
        if cache_seed:
            params["seed"] = cache_seed
            key = hash_inputs(params=params)
        else:
            key = hash_inputs(files=[video_path], params=params)
        return self.paths.template_cache / f"{video_path.stem}-{key[:16]}"

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
        config = config or self._default_config()
        # Absolutos obrigatoriamente: o worker faz chdir para o repositório do
        # MuseTalk (que usa caminhos relativos nas suas configs), e qualquer
        # caminho relativo do chamador passaria a apontar para o lugar errado.
        video_path = Path(video_path).resolve()
        audio_path = Path(audio_path).resolve()
        output_path = Path(output_path).resolve()

        video_info = ffmpeg.validate_video(video_path, min_duration=0.2)
        audio_info = ffmpeg.validate_audio(audio_path, min_duration=0.15)

        estimated_frames = int(audio_info.duration * config.fps)
        if (
            self.profile == "CPU_ONLY"
            and estimated_frames > self.settings.lipsync.warn_frames_above
        ):
            minutes = estimated_frames * 1.5 / 60
            if on_progress:
                on_progress(
                    0.0,
                    f"Atenção: {estimated_frames} frames em CPU — estimativa de "
                    f"~{minutes:.0f} min. Considere um roteiro mais curto.",
                )

        cache_dir = self._template_cache_dir(
            video_path, config, cache_seed
        ).resolve()
        work_dir = (self.paths.cache / "lipsync" / output_path.stem).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            result = run_worker(
                env=ENV_NAME,
                module=WORKER_MODULE,
                stage="lipsync",
                request={
                    "action": "process",
                    "repo_dir": str(self.repo_dir),
                    "weights_dir": str(self.weights_dir),
                    "video": str(video_path),
                    "audio": str(audio_path),
                    "output": str(output_path),
                    "work_dir": str(work_dir),
                    "cache_dir": str(cache_dir),
                    "device": self._device(),
                    "cpu_threads": self._cpu_threads(),
                    "fps": video_info.video.fps or config.fps,
                    "batch_size": config.batch_size,
                    "bbox_shift": config.bbox_shift,
                    "extra_margin": config.extra_margin,
                    "parsing_mode": config.parsing_mode,
                    "version": config.version,
                    "left_cheek_width": config.left_cheek_width,
                    "right_cheek_width": config.right_cheek_width,
                    "cleanup": config.cleanup,
                },
                extra_env=self._extra_env(),
                on_progress=on_progress,
                should_cancel=should_cancel,
                timeout=None,
            )
        except WorkerError:
            raise

        ffmpeg.validate_video(output_path, min_duration=0.2, require_audio=True)

        data = result.data
        return LipSyncResult(
            output=Path(data["output"]),
            frames=int(data.get("frames", 0)),
            fps=float(data.get("fps", config.fps)),
            seconds_per_frame=float(data.get("seconds_per_frame", 0.0)),
            duration_seconds=result.duration_seconds,
            log_file=result.log_file,
        )

    def clear_template_cache(self) -> int:
        """Apaga o preprocessamento cacheado de todos os templates."""
        import shutil

        root = self.paths.template_cache
        if not root.exists():
            return 0
        count = sum(1 for _ in root.iterdir())
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return count
