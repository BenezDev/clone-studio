"""Worker do Qwen3-TTS.

Roda dentro de `.envs/qwen-tts`, que é o único ambiente com `qwen-tts` e o
`transformers==4.57.3` que ele exige. O backend nunca importa este módulo.

Ações suportadas:
  healthcheck  — verifica se o pacote e os pesos estão utilizáveis
  synthesize   — gera N variantes de áudio a partir de blocos de texto
"""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path
from typing import Any

# `core.worker.protocol` é stdlib-only justamente para poder ser importado aqui.
sys.path.insert(0, os.environ.get("CLONE_STUDIO_ROOT", "."))

from core.worker.protocol import (  # noqa: E402
    WorkerFailure,
    emit_log,
    emit_progress,
    run_worker,
)

_MODEL_CACHE: dict[str, Any] = {}


def _configure_threads(request: dict[str, Any]) -> None:
    import torch

    threads = int(request.get("cpu_threads") or 0)
    if threads > 0:
        torch.set_num_threads(threads)
        os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    emit_log(f"torch threads = {torch.get_num_threads()}")


def _resolve_dtype(name: str):
    import torch

    return {
        "float32": torch.float32,
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
    }.get(name, torch.float32)


def _load_model(request: dict[str, Any]):
    """Carrega o modelo, escolhendo a implementação de atenção viável."""
    import torch
    from qwen_tts import Qwen3TTSModel

    source = request.get("model_dir") or request["model"]
    device = request.get("device", "cpu")
    dtype = _resolve_dtype(request.get("dtype", "float32"))

    cache_key = f"{source}|{device}|{dtype}"
    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]

    emit_progress(0.05, f"Carregando modelo ({Path(str(source)).name})…")

    # flash_attention_2 só existe em GPU NVIDIA com o pacote compilado.
    # Em CPU, sdpa é o melhor disponível; eager é o fallback universal.
    attn_candidates = (
        ["flash_attention_2", "sdpa", "eager"]
        if device.startswith("cuda")
        else ["sdpa", "eager"]
    )

    last_error: Exception | None = None
    for attn in attn_candidates:
        try:
            model = Qwen3TTSModel.from_pretrained(
                source,
                device_map=device,
                dtype=dtype,
                attn_implementation=attn,
            )
            emit_log(f"modelo carregado (attn_implementation={attn})")
            _MODEL_CACHE[cache_key] = model
            return model
        except (ImportError, ValueError, RuntimeError) as exc:
            last_error = exc
            emit_log(f"attn '{attn}' indisponível: {exc}", level="warning")
            continue
        except OSError as exc:
            raise WorkerFailure(
                f"Não foi possível carregar os pesos de '{source}': {exc}",
                hint=(
                    "Os pesos podem não estar baixados. Rode "
                    "`./scripts/models.sh install qwen3-tts` e tente de novo."
                ),
                error_type="weights_missing",
            ) from exc

    raise WorkerFailure(
        f"Falha ao instanciar o modelo Qwen3-TTS: {last_error}",
        hint=(
            "Verifique se o ambiente .envs/qwen-tts está completo "
            "(torch + transformers==4.57.3 + qwen-tts)."
        ),
        error_type="model_load_failed",
    )


def _build_prompt(model, request: dict[str, Any]):
    """Cria o prompt de clonagem uma única vez e reutiliza entre as variantes."""
    ref_audio = request["ref_audio"]
    ref_text = (request.get("ref_text") or "").strip()

    if not Path(ref_audio).exists():
        raise WorkerFailure(
            f"Áudio de referência não encontrado: {ref_audio}",
            hint="Cadastre novamente a voz na página Voice.",
            error_type="missing_reference",
        )

    x_vector_only = not bool(ref_text)
    if x_vector_only:
        emit_log(
            "Sem transcrição da referência: usando x_vector_only_mode "
            "(qualidade de clonagem reduzida).",
            level="warning",
        )

    try:
        return model.create_voice_clone_prompt(
            ref_audio=ref_audio,
            ref_text=ref_text,
            x_vector_only_mode=x_vector_only,
        )
    except AttributeError:
        # Versão do pacote sem prompt reutilizável: o caller passa ref direto.
        return None


def _concat(chunks: list, sample_rate: int, gap_seconds: float = 0.12):
    """Junta os blocos com um respiro curto entre eles."""
    import numpy as np

    if not chunks:
        raise WorkerFailure("O modelo não produziu áudio.", error_type="empty_audio")
    if len(chunks) == 1:
        return np.asarray(chunks[0], dtype="float32")

    gap = np.zeros(int(sample_rate * gap_seconds), dtype="float32")
    pieces: list = []
    for index, chunk in enumerate(chunks):
        pieces.append(np.asarray(chunk, dtype="float32").reshape(-1))
        if index < len(chunks) - 1:
            pieces.append(gap)
    return np.concatenate(pieces)


def _generate_variant(
    model,
    request: dict[str, Any],
    prompt_items,
    texts: list[str],
    seed: int | None,
    progress_base: float,
    progress_span: float,
):
    import torch

    if seed is not None:
        torch.manual_seed(seed)

    language = request.get("language", "Portuguese")
    max_new_tokens = int(request.get("max_new_tokens", 4096))

    audio_chunks = []
    sample_rate = int(request.get("sample_rate", 24000))

    for index, text in enumerate(texts):
        emit_progress(
            progress_base + progress_span * (index / max(len(texts), 1)),
            f"Sintetizando bloco {index + 1}/{len(texts)}…",
        )
        kwargs: dict[str, Any] = {
            "text": text,
            "language": language,
            "max_new_tokens": max_new_tokens,
        }
        if prompt_items is not None:
            kwargs["voice_clone_prompt"] = prompt_items
        else:
            kwargs["ref_audio"] = request["ref_audio"]
            kwargs["ref_text"] = request.get("ref_text", "")

        wavs, returned_sr = model.generate_voice_clone(**kwargs)
        if not wavs:
            raise WorkerFailure(
                f"O modelo devolveu áudio vazio para o bloco {index + 1}.",
                hint=(
                    "Texto muito curto, apenas pontuação, ou max_new_tokens "
                    "insuficiente para o tamanho do bloco."
                ),
                error_type="empty_audio",
            )
        sample_rate = int(returned_sr)
        audio_chunks.append(wavs[0])

    return _concat(audio_chunks, sample_rate), sample_rate


# ---------------------------------------------------------------------------
# Ações
# ---------------------------------------------------------------------------


def _action_healthcheck(request: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {"package": None, "torch": None, "weights": False}
    try:
        import qwen_tts  # noqa: F401

        info["package"] = getattr(qwen_tts, "__version__", "instalado")
    except ImportError as exc:
        raise WorkerFailure(
            f"Pacote qwen-tts indisponível: {exc}",
            hint="Rode ./install.sh --only qwen-tts.",
            error_type="missing_dependency",
        ) from exc

    import torch

    info["torch"] = torch.__version__
    info["cuda"] = torch.cuda.is_available()
    info["threads"] = torch.get_num_threads()

    model_dir = request.get("model_dir")
    if model_dir:
        directory = Path(model_dir)
        info["weights"] = (directory / "config.json").exists()
        info["model_dir"] = str(directory)
    return info


def _action_synthesize(request: dict[str, Any]) -> dict[str, Any]:
    import soundfile as sf

    _configure_threads(request)

    texts: list[str] = [t for t in request.get("texts", []) if t.strip()]
    if not texts:
        raise WorkerFailure(
            "Nenhum texto para sintetizar.", error_type="empty_input"
        )

    variants: list[dict[str, Any]] = request.get("variants") or []
    if not variants:
        raise WorkerFailure(
            "Nenhuma variante de saída definida.", error_type="empty_input"
        )

    model = _load_model(request)
    emit_progress(0.15, "Extraindo características da voz de referência…")
    prompt_items = _build_prompt(model, request)

    produced: list[dict[str, Any]] = []
    span = 0.8 / len(variants)

    for index, variant in enumerate(variants):
        base = 0.2 + span * index
        seed = variant.get("seed")
        output = Path(variant["output"])
        output.parent.mkdir(parents=True, exist_ok=True)

        audio, sample_rate = _generate_variant(
            model, request, prompt_items, texts, seed, base, span * 0.9
        )
        sf.write(str(output), audio, sample_rate)

        produced.append(
            {
                "path": str(output),
                "duration": round(len(audio) / sample_rate, 3),
                "sample_rate": sample_rate,
                "seed": seed,
                "label": variant.get("label", chr(ord("A") + index)),
            }
        )
        emit_log(
            f"variante {produced[-1]['label']}: {produced[-1]['duration']}s "
            f"-> {output.name}"
        )

    # Libera a RAM antes de encerrar; o processo morre logo em seguida, mas
    # isso mantém o pico baixo quando várias variantes são geradas.
    _MODEL_CACHE.clear()
    del model
    gc.collect()

    emit_progress(1.0, "Síntese concluída.")
    return {"variants": produced, "model": request.get("model")}


_ACTIONS = {
    "healthcheck": _action_healthcheck,
    "synthesize": _action_synthesize,
}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    action = request.get("action", "synthesize")
    handler = _ACTIONS.get(action)
    if handler is None:
        raise WorkerFailure(
            f"Ação desconhecida: {action}. Disponíveis: {', '.join(_ACTIONS)}",
            error_type="bad_request",
        )
    return handler(request)


if __name__ == "__main__":
    raise SystemExit(run_worker(handle))
