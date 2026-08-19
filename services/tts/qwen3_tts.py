"""Adapter do Qwen3-TTS.

Este é o único arquivo do projeto que sabe que a engine de voz é o Qwen3-TTS.
Tudo mais fala com `TTSEngine`. O trabalho pesado acontece num subprocesso
isolado (`services/tts/worker/qwen_worker.py`), então este módulo não importa
torch nem transformers.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from core import platform_hints as hints
from core.audio.text_prep import chunk_for_synthesis, normalize_for_tts
from core.config.loader import active_profile, get_hardware, load_settings
from core.licensing.registry import get_registry
from core.media import ffmpeg
from core.storage.cache import get_cache
from core.storage.paths import get_paths, safe_path_component
from core.worker.runner import (
    EngineNotInstalled,
    WorkerError,
    engine_available,
    run_worker,
)
from services.tts.base import (
    CancelCheck,
    HealthStatus,
    ProgressCallback,
    SynthesisRequest,
    SynthesisResult,
    SynthesisVariant,
    TTSEngine,
    VoiceProfile,
    VoiceReference,
)

WORKER_MODULE = "services.tts.worker.qwen_worker"
ENV_NAME = "qwen-tts"

# Duração mínima/máxima recomendada para a amostra de referência.
MIN_REFERENCE_SECONDS = 3.0
MAX_REFERENCE_SECONDS = 30.0
MAX_SOURCE_REFERENCE_SECONDS = 120.0


class VoiceEnrollmentError(RuntimeError):
    """A amostra enviada não serve como referência de voz."""


class Qwen3TTSEngine(TTSEngine):
    name = "qwen3"

    def __init__(self) -> None:
        self.settings = load_settings()
        self.paths = get_paths()
        self.profile = active_profile()
        self.hardware = get_hardware()

    # -- descoberta ------------------------------------------------------

    @property
    def model_repo(self) -> str:
        return self.settings.tts.resolve_model(self.profile)

    @property
    def model_entry(self):
        return get_registry().by_repo(self.model_repo)

    def _model_dir(self) -> Path | None:
        entry = self.model_entry
        if entry is None:
            return None
        directory = entry.install_dir
        return directory if (directory / "config.json").exists() else None

    def _device_and_dtype(self) -> tuple[str, str]:
        if self.profile == "CPU_ONLY":
            # bfloat16 em CPU x86 sem AMX é mais lento que float32.
            return "cpu", "float32"
        return "cuda:0", "bfloat16"

    def _cpu_threads(self) -> int:
        configured = self.settings.hardware.cpu_threads
        if configured > 0:
            return configured
        # Núcleos físicos rendem mais que threads lógicas em inferência.
        return self.hardware.cpu_cores_physical or self.hardware.cpu_threads or 4

    # -- healthcheck -----------------------------------------------------

    def healthcheck(self) -> HealthStatus:
        entry = self.model_entry
        status = HealthStatus(
            ok=False,
            engine=self.name,
            model=self.model_repo,
            env_ready=engine_available(ENV_NAME),
            weights_ready=self._model_dir() is not None,
        )

        if not status.env_ready:
            status.detail = (
                f"Ambiente '{ENV_NAME}' não instalado "
                f"({self.paths.env_python(ENV_NAME)} não existe)."
            )
            status.hints.append(f"Rode {hints.install()}")
            return status

        if not status.weights_ready:
            size = f"{entry.size_gb:.1f} GB" if entry else "vários GB"
            status.detail = f"Pesos de {self.model_repo} não baixados ({size})."
            status.hints.append(
                f"Rode {hints.models('install', entry.key if entry else 'qwen3_tts_0_6b_base')}"
            )
            return status

        try:
            result = run_worker(
                env=ENV_NAME,
                module=WORKER_MODULE,
                stage="tts-healthcheck",
                request={
                    "action": "healthcheck",
                    "model": self.model_repo,
                    "model_dir": str(self._model_dir()),
                },
                timeout=180,
            )
        except EngineNotInstalled as exc:
            status.detail = str(exc)
            status.hints.append(f"Rode {hints.install()}")
            return status
        except WorkerError as exc:
            status.detail = exc.message
            if exc.hint:
                status.hints.append(exc.hint)
            return status

        status.ok = True
        status.installed = True
        data = result.data
        status.detail = (
            f"qwen-tts {data.get('package')} | torch {data.get('torch')} | "
            f"cuda={data.get('cuda')} | threads={data.get('threads')}"
        )
        return status

    # -- enrollment ------------------------------------------------------

    def clone_voice(
        self,
        audio_path: Path,
        transcript: str,
        profile_id: str,
        display_name: str = "",
        on_progress: ProgressCallback | None = None,
    ) -> VoiceProfile:
        """Cadastra uma voz (Voice Enrollment).

        Não chama o modelo: a clonagem do Qwen3-TTS é zero-shot, então o
        cadastro consiste em condicionar e validar a amostra. O prompt de voz
        é calculado sob demanda na primeira síntese e cacheado.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise VoiceEnrollmentError(f"Arquivo não encontrado: {audio_path}")

        if on_progress:
            on_progress(0.1, "Analisando amostra…")

        source_info = ffmpeg.probe(audio_path)
        if not source_info.has_audio:
            raise VoiceEnrollmentError(f"{audio_path.name} não contém áudio.")

        # Duração 0 é o que uma gravação de navegador mostra antes do reparo de
        # cabeçalho — não é amostra vazia. Nesse caso a faixa útil só pode ser
        # conferida depois do `to_wav`, que decodifica o stream inteiro. Para
        # arquivo normal as duas pontas continuam valendo aqui, e a de cima
        # evita decodificar e normalizar um arquivo de horas à toa.
        if source_info.duration > 0:
            if source_info.duration < MIN_REFERENCE_SECONDS:
                raise VoiceEnrollmentError(
                    f"A amostra tem {source_info.duration:.1f}s. São necessários ao "
                    f"menos {MIN_REFERENCE_SECONDS:.0f}s de fala limpa."
                )
            if source_info.duration > MAX_SOURCE_REFERENCE_SECONDS:
                raise VoiceEnrollmentError(
                    f"A amostra tem {source_info.duration:.1f}s. Envie no máximo "
                    f"{MAX_SOURCE_REFERENCE_SECONDS:.0f}s; os primeiros "
                    f"{MAX_REFERENCE_SECONDS:.0f}s úteis serão mantidos."
                )

        profile_id = safe_path_component(profile_id, label="ID do perfil")
        profile_dir = self.paths.voice_dir / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)

        if on_progress:
            on_progress(0.4, "Normalizando áudio…")

        index = len(list(profile_dir.glob("reference_*.wav"))) + 1
        reference_path = profile_dir / f"reference_{index:02d}.wav"

        # Mono, 24 kHz (taxa nativa do speaker encoder), silêncio das pontas
        # removido e ganho normalizado sem esmagar a dinâmica.
        try:
            ffmpeg.to_wav(
                audio_path,
                reference_path,
                sample_rate=self.settings.tts.sample_rate,
                mono=True,
                normalize=True,
                trim_silence=True,
            )
        except ffmpeg.FFmpegError as exc:
            raise VoiceEnrollmentError(
                f"Não foi possível decodificar o áudio de {audio_path.name}."
            ) from exc

        if on_progress:
            on_progress(0.75, "Validando resultado…")

        try:
            info = ffmpeg.validate_audio(
                reference_path,
                min_duration=MIN_REFERENCE_SECONDS,
                expected_sample_rate=self.settings.tts.sample_rate,
            )
        except ffmpeg.ValidationError as exc:
            # A duração tem que ser lida ANTES de apagar o arquivo. Lida depois,
            # o `exists()` era sempre falso e toda amostra curta virava
            # "A amostra tem 0.0s" — a mensagem mais confusa possível, porque
            # 0.0s é o sintoma de arquivo vazio, não de arquivo curto.
            restante = (
                ffmpeg.probe(reference_path).duration
                if reference_path.exists()
                else 0.0
            )
            reference_path.unlink(missing_ok=True)
            if restante < MIN_REFERENCE_SECONDS:
                raise VoiceEnrollmentError(
                    f"Sobrou {restante:.1f}s de fala depois de remover o silêncio "
                    f"das pontas; são necessários ao menos "
                    f"{MIN_REFERENCE_SECONDS:.0f}s. Fale mais tempo, ou mais perto "
                    f"do microfone."
                ) from exc
            # Falha de taxa de amostragem não é falha de duração: repetir a
            # mensagem errada mandaria o usuário resolver o problema errado.
            raise VoiceEnrollmentError(str(exc)) from exc

        if info.duration > MAX_REFERENCE_SECONDS:
            trimmed = profile_dir / f"reference_{index:02d}.trimmed.wav"
            ffmpeg.run_ffmpeg(
                [
                    "-i", str(reference_path),
                    "-t", str(MAX_REFERENCE_SECONDS),
                    "-c:a", "pcm_s16le",
                    str(trimmed),
                ]
            )
            trimmed.replace(reference_path)
            info = ffmpeg.probe(reference_path)

        reference = VoiceReference(
            audio_path=str(reference_path.relative_to(self.paths.root)),
            transcript=transcript.strip(),
            duration=round(info.duration, 3),
            sample_rate=self.settings.tts.sample_rate,
            label="default",
            primary=index == 1,
        )

        try:
            profile = VoiceProfile.load(profile_dir)
            profile.references.append(reference)
        except FileNotFoundError:
            profile = VoiceProfile(
                id=profile_id,
                display_name=display_name or profile_id,
                language=self.settings.tts.language,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                references=[reference],
                default_speed=self.settings.tts.default_speed,
            )

        profile.save(profile_dir)
        if on_progress:
            on_progress(1.0, "Voz cadastrada.")
        return profile

    # -- síntese ---------------------------------------------------------

    def _pick_reference(
        self, profile: VoiceProfile, emotion: str
    ) -> VoiceReference:
        """Escolhe a referência mais adequada à emoção pedida.

        O modelo Base não aceita instruções de estilo (isso é exclusivo dos
        modelos CustomVoice/VoiceDesign). O caminho honesto para variar emoção
        preservando a identidade é escolher entre múltiplas amostras do próprio
        usuário, rotuladas por estilo.
        """
        wanted = (emotion or "").strip().lower()
        if wanted:
            for reference in profile.references:
                if reference.label.lower() == wanted:
                    return reference
        primary = profile.primary_reference
        if primary is None:
            raise VoiceEnrollmentError(
                f"O perfil de voz '{profile.id}' não tem nenhuma amostra."
            )
        return primary

    def synthesize(
        self,
        request: SynthesisRequest,
        on_progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> SynthesisResult:
        settings = self.settings
        output = Path(request.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        normalized = normalize_for_tts(request.text)
        if not normalized.strip():
            raise ValueError("O texto ficou vazio depois da normalização.")
        chunks = chunk_for_synthesis(normalized)

        reference = self._pick_reference(request.voice_profile, request.emotion)
        reference_path = (self.paths.root / reference.audio_path).resolve()
        voice_root = self.paths.voice_dir.resolve()
        if voice_root not in reference_path.parents:
            raise VoiceEnrollmentError(
                "A amostra do perfil aponta para fora da biblioteca local de voz."
            )

        variant_count = max(1, request.variants)
        base_seed = request.seed if request.seed is not None else settings.tts.seed

        cache = get_cache("tts")
        entry = cache.entry(
            files=[reference_path],
            params={
                "model": self.model_repo,
                "language": request.language,
                "speed": request.speed,
                "emotion": request.emotion,
                "variants": variant_count,
                "seed": base_seed,
                "ref_text": reference.transcript,
                "max_new_tokens": request.max_new_tokens,
            },
            text=normalized,
            label="synth",
        )

        if entry.hit:
            meta = entry.load_metadata()
            cached = [Path(v["path"]) for v in meta.get("variants", [])]
            if all(p.exists() for p in cached) and cached:
                if on_progress:
                    on_progress(1.0, "Áudio reaproveitado do cache.")
                variants = [
                    SynthesisVariant(
                        path=self._deliver(Path(v["path"]), output, index),
                        duration=v["duration"],
                        sample_rate=v["sample_rate"],
                        seed=v.get("seed"),
                        label=v.get("label", chr(ord("A") + index)),
                    )
                    for index, v in enumerate(meta["variants"])
                ]
                return SynthesisResult(
                    variants=variants,
                    engine=self.name,
                    model=self.model_repo,
                    duration_seconds=0.0,
                )

        entry.directory.mkdir(parents=True, exist_ok=True)
        device, dtype = self._device_and_dtype()

        variant_specs = []
        for index in range(variant_count):
            seed = None if base_seed is None else base_seed + index
            variant_specs.append(
                {
                    "output": str(entry.file(f"variant_{index}.wav")),
                    "seed": seed,
                    "label": chr(ord("A") + index),
                }
            )

        payload = {
            "action": "synthesize",
            "model": self.model_repo,
            "model_dir": str(self._model_dir() or ""),
            "device": device,
            "dtype": dtype,
            "cpu_threads": self._cpu_threads(),
            "texts": chunks,
            "language": request.language or settings.tts.language,
            "ref_audio": str(reference_path),
            "ref_text": reference.transcript,
            "sample_rate": settings.tts.sample_rate,
            "max_new_tokens": request.max_new_tokens,
            "variants": variant_specs,
        }

        result = run_worker(
            env=ENV_NAME,
            module=WORKER_MODULE,
            stage="tts",
            request=payload,
            on_progress=on_progress,
            should_cancel=should_cancel,
            timeout=None,
        )

        produced = result.data.get("variants", [])
        variants: list[SynthesisVariant] = []
        for index, item in enumerate(produced):
            raw_path = Path(item["path"])
            ffmpeg.validate_audio(raw_path, min_duration=0.15)

            final_path = raw_path
            if abs(request.speed - 1.0) > 0.01:
                final_path = raw_path.with_name(f"{raw_path.stem}.speed.wav")
                _apply_speed(raw_path, final_path, request.speed)
                info = ffmpeg.probe(final_path)
                item["duration"] = round(info.duration, 3)
                item["path"] = str(final_path)

            variants.append(
                SynthesisVariant(
                    path=self._deliver(final_path, output, index),
                    duration=item["duration"],
                    sample_rate=item["sample_rate"],
                    seed=item.get("seed"),
                    label=item.get("label", chr(ord("A") + index)),
                )
            )

        entry.commit({"variants": produced, "model": self.model_repo})

        return SynthesisResult(
            variants=variants,
            engine=self.name,
            model=self.model_repo,
            duration_seconds=result.duration_seconds,
            log_file=result.log_file,
        )

    @staticmethod
    def _deliver(source: Path, output: Path, index: int) -> Path:
        """Copia a variante do cache para o destino pedido pelo chamador."""
        target = output if index == 0 else output.with_name(
            f"{output.stem}_{chr(ord('A') + index)}{output.suffix}"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target


def _apply_speed(source: Path, destination: Path, speed: float) -> Path:
    """Ajusta a velocidade preservando o tom (atempo do FFmpeg).

    O modelo Base não expõe controle de velocidade, então o ajuste é feito no
    pós-processamento. `atempo` aceita 0.5–2.0 por instância; valores fora
    disso são encadeados.
    """
    factors: list[float] = []
    remaining = max(0.25, min(4.0, speed))
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)

    chain = ",".join(f"atempo={f:.4f}" for f in factors)
    ffmpeg.run_ffmpeg(
        ["-i", str(source), "-af", chain, "-c:a", "pcm_s16le", str(destination)]
    )
    return destination


def list_voice_profiles() -> list[VoiceProfile]:
    """Todos os perfis de voz cadastrados em data/identity/voice/."""
    paths = get_paths()
    profiles: list[VoiceProfile] = []
    if not paths.voice_dir.exists():
        return profiles
    for directory in sorted(paths.voice_dir.iterdir()):
        if not directory.is_dir():
            continue
        try:
            profiles.append(VoiceProfile.load(directory))
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            continue
    return profiles


def load_voice_profile(profile_id: str) -> VoiceProfile:
    profile_id = safe_path_component(profile_id, label="ID do perfil")
    return VoiceProfile.load(get_paths().voice_dir / profile_id)
