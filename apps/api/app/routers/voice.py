"""Rotas de voz: cadastro de perfil, síntese e A/B."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from apps.api.app.uploads import (
    MAX_VOICE_UPLOAD_BYTES,
    UploadTooLarge,
    copy_file_limited,
)
from core.config.loader import load_settings
from core.media import ffmpeg
from core.storage.paths import get_paths, safe_path_component
from core.worker.runner import EngineNotInstalled, WorkerError
from services.tts.base import SynthesisRequest
from services.tts.qwen3_tts import (
    Qwen3TTSEngine,
    VoiceEnrollmentError,
    list_voice_profiles,
    load_voice_profile,
)

router = APIRouter(prefix="/voice", tags=["voice"])


class SynthesisPayload(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    profile_id: str = Field(default="me", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    emotion: str = ""
    speed: float = 1.0
    variants: int = Field(default=1, ge=1, le=5)
    seed: int | None = None


@router.get("/profiles")
def get_profiles() -> dict[str, object]:
    profiles = list_voice_profiles()
    return {
        "profiles": [
            {
                **p.to_dict(),
                "total_duration": round(sum(r.duration for r in p.references), 2),
            }
            for p in profiles
        ]
    }


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: str) -> dict[str, object]:
    try:
        return load_voice_profile(profile_id).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/profiles/{profile_id}/enroll")
def enroll(
    profile_id: str,
    audio: UploadFile = File(...),
    transcript: str = Form(..., max_length=10_000),
    display_name: str = Form("", max_length=200),
    label: str = Form("default", max_length=80),
) -> dict[str, object]:
    """Cadastra uma amostra de voz, vinda de arquivo ou do microfone.

    O áudio nunca sai da máquina: é gravado num temporário local, condicionado
    pelo FFmpeg e guardado em data/identity/voice/. A gravação feita dentro do
    app chega por esta mesma rota — o navegador manda o WebM/Opus do
    ``MediaRecorder`` no lugar do arquivo escolhido no disco.
    """
    if not transcript.strip():
        raise HTTPException(
            status_code=400,
            detail="A transcrição é obrigatória: a clonagem fica muito melhor "
                   "quando o modelo sabe exatamente o que é dito na amostra.",
        )

    try:
        profile_id = safe_path_component(profile_id, label="ID do perfil")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raw_name = audio.filename or "sample.wav"
    suffix = Path(raw_name).suffix.lower()
    if not suffix:
        ct = (audio.content_type or "").lower()
        if "webm" in ct:
            suffix = ".webm"
        elif "ogg" in ct:
            suffix = ".ogg"
        elif "mp4" in ct or "m4a" in ct or "aac" in ct:
            suffix = ".m4a"
        elif "wav" in ct:
            suffix = ".wav"
        else:
            suffix = ".wav"

    allowed_suffixes = {
        ".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".oga",
        ".aac", ".opus", ".webm", ".wma", ".mov", ".mkv",
    }
    if suffix not in allowed_suffixes:
        raise HTTPException(
            status_code=400, detail=f"Formato de áudio não suportado: {suffix}"
        )
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        temporary = Path(handle.name)
    temporary.unlink()
    try:
        copy_file_limited(audio.file, temporary, MAX_VOICE_UPLOAD_BYTES)
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    # Gravação de navegador chega sem duração no cabeçalho; sem este
    # reparo o enrollment recusaria uma amostra boa dizendo "0.0s".
    # Para WebM de áudio, forçar conversão para WAV se o remux não funcionar.
    try:
        repaired = ffmpeg.ensure_container_metadata(temporary)
        if not repaired:
            # Se já tem duração, não precisa converter
            info = ffmpeg.probe(temporary)
            if info.duration <= 0 and temporary.suffix.lower() == ".webm":
                # WebM sem duração: converter para WAV
                wav_temp = temporary.with_suffix(".wav")
                try:
                    # 24 kHz é a taxa nativa do speaker encoder e a que o
                    # `to_wav` vai usar adiante. Gravar 16 kHz aqui jogaria
                    # fora a banda de 8–12 kHz antes, sem chance de recuperar.
                    ffmpeg.run_ffmpeg([
                        "-i", str(temporary),
                        "-ar", str(load_settings().tts.sample_rate),
                        "-ac", "1",
                        str(wav_temp),
                    ])
                    temporary.unlink(missing_ok=True)
                    temporary = wav_temp
                except Exception:
                    wav_temp.unlink(missing_ok=True)
                    raise
    except ffmpeg.FFmpegError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Não foi possível ler o áudio enviado.",
                "hint": exc.hint or "Tente outro formato, ou grave novamente.",
                "command": exc.command,
                "exit_code": exc.exit_code,
            },
        ) from exc

    try:
        engine = Qwen3TTSEngine()
        profile = engine.clone_voice(
            audio_path=temporary,
            transcript=transcript,
            profile_id=profile_id,
            display_name=display_name or profile_id,
        )
        if label and label != "default":
            profile.references[-1].label = label
            profile.save(get_paths().voice_dir / profile_id)
    except VoiceEnrollmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        temporary.unlink(missing_ok=True)

    return profile.to_dict()


@router.delete("/profiles/{profile_id}/references/{index}")
def delete_reference(profile_id: str, index: int) -> dict[str, object]:
    paths = get_paths()
    try:
        profile_id = safe_path_component(profile_id, label="ID do perfil")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    directory = paths.voice_dir / profile_id
    try:
        profile = load_voice_profile(profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not 0 <= index < len(profile.references):
        raise HTTPException(status_code=404, detail="Amostra inexistente.")

    removed = profile.references.pop(index)
    audio = (paths.root / removed.audio_path).resolve()
    voice_root = paths.voice_dir.resolve()
    if voice_root not in audio.parents:
        raise HTTPException(
            status_code=409,
            detail="Metadados da voz apontam para fora da biblioteca.",
        )
    audio.unlink(missing_ok=True)

    if profile.references and not any(r.primary for r in profile.references):
        profile.references[0].primary = True
    profile.save(directory)
    return profile.to_dict()


@router.post("/synthesize")
def synthesize(payload: SynthesisPayload) -> dict[str, object]:
    """Gera áudio avulso (usado pelo passo Voice e pelo A/B)."""
    settings = load_settings()
    try:
        profile = load_voice_profile(payload.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    paths = get_paths()
    output = paths.cache / "voice_previews" / f"{payload.profile_id}.wav"

    engine = Qwen3TTSEngine()
    try:
        result = engine.synthesize(
            SynthesisRequest(
                text=payload.text,
                voice_profile=profile,
                output_path=output,
                language=settings.tts.language,
                speed=payload.speed,
                emotion=payload.emotion,
                seed=payload.seed,
                variants=payload.variants,
                max_new_tokens=settings.tts.max_new_tokens,
            )
        )
    except EngineNotInstalled as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WorkerError as exc:
        raise HTTPException(status_code=500, detail=exc.to_dict()) from exc

    return {
        "engine": result.engine,
        "model": result.model,
        "duration_seconds": round(result.duration_seconds, 2),
        "variants": [
            {
                "label": v.label,
                "duration": v.duration,
                "seed": v.seed,
                "url": f"/api/media/file?path={v.path}",
            }
            for v in result.variants
        ],
    }


@router.post("/profiles/{profile_id}/prefer")
def set_preferred(profile_id: str, settings_payload: dict) -> dict[str, object]:
    """Guarda a configuração que o usuário escolheu no A/B."""
    try:
        profile_id = safe_path_component(profile_id, label="ID do perfil")
        profile = load_voice_profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    profile.preferred_settings = settings_payload
    profile.save(get_paths().voice_dir / profile_id)
    return profile.to_dict()


@router.get("/health")
def voice_health() -> dict[str, object]:
    return Qwen3TTSEngine().healthcheck().to_dict()
