"""Worker do faster-whisper.

Roda em `.envs/whisper`. CTranslate2 não depende de torch, então este ambiente
é leve (~250 MB) e a transcrição em CPU roda acima do tempo real com int8.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.environ.get("CLONE_STUDIO_ROOT", "."))

from core.worker.protocol import (  # noqa: E402
    WorkerFailure,
    emit_log,
    emit_progress,
    run_worker,
)


def _action_healthcheck(request: dict[str, Any]) -> dict[str, Any]:
    try:
        import ctranslate2
        import faster_whisper
    except ImportError as exc:
        raise WorkerFailure(
            f"faster-whisper indisponível: {exc}",
            hint="./install.sh --only whisper",
            error_type="missing_dependency",
        ) from exc

    return {
        "faster_whisper": getattr(faster_whisper, "__version__", "instalado"),
        "ctranslate2": ctranslate2.__version__,
        "cuda_devices": ctranslate2.get_cuda_device_count(),
        "model": request.get("model"),
    }


def _action_transcribe(request: dict[str, Any]) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    audio = Path(request["audio"])
    if not audio.exists():
        raise WorkerFailure(
            f"Áudio não encontrado: {audio}", error_type="missing_input"
        )

    model_name = request.get("model", "small")
    device = request.get("device", "cpu")
    compute_type = request.get("compute_type", "int8")
    threads = int(request.get("cpu_threads") or 0)
    download_root = request.get("download_root") or None

    emit_progress(0.05, f"Carregando modelo {model_name}…")
    try:
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=threads,
            download_root=download_root,
        )
    except Exception as exc:  # noqa: BLE001
        raise WorkerFailure(
            f"Não foi possível carregar o modelo '{model_name}': {exc}",
            hint=(
                "Se for a primeira execução, o modelo precisa ser baixado. "
                "Rode `./scripts/models.sh install faster_whisper_small`."
            ),
            error_type="model_load_failed",
        ) from exc

    emit_progress(0.15, "Transcrevendo…")
    segments_iter, info = model.transcribe(
        str(audio),
        language=request.get("language") or None,
        word_timestamps=bool(request.get("word_timestamps", True)),
        initial_prompt=request.get("initial_prompt") or None,
        vad_filter=bool(request.get("vad_filter", True)),
        beam_size=int(request.get("beam_size", 5)),
    )

    total = float(info.duration or 0.0)
    segments: list[dict[str, Any]] = []

    for segment in segments_iter:
        words = [
            {
                "text": w.word,
                "start": round(float(w.start), 3),
                "end": round(float(w.end), 3),
                "probability": round(float(w.probability), 4),
            }
            for w in (segment.words or [])
        ]
        segments.append(
            {
                "text": segment.text,
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "words": words,
            }
        )
        if total > 0:
            emit_progress(
                0.15 + 0.8 * min(1.0, segment.end / total),
                f"{segment.end:.1f}s / {total:.1f}s",
            )

    emit_log(f"idioma detectado: {info.language} (p={info.language_probability:.2f})")
    emit_progress(1.0, "Transcrição concluída.")

    return {
        "segments": segments,
        "language": info.language,
        "duration": round(total, 3),
        "model": model_name,
    }


_ACTIONS = {
    "healthcheck": _action_healthcheck,
    "transcribe": _action_transcribe,
}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    action = request.get("action", "transcribe")
    handler = _ACTIONS.get(action)
    if handler is None:
        raise WorkerFailure(f"Ação desconhecida: {action}", error_type="bad_request")
    return handler(request)


if __name__ == "__main__":
    raise SystemExit(run_worker(handle))
