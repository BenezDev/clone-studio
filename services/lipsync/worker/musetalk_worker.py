"""Worker do MuseTalk 1.5.

Roda em `.envs/musetalk` (Python 3.10 + torch 2.0.1 + mmcv 2.0.1).

Este worker não chama `scripts/inference.py` do upstream: ele importa os
módulos do MuseTalk e reimplementa o laço de inferência. Três motivos:

  1. **Cache real de template.** O passo caro é detectar rosto + landmarks
     (DWPose, um forward por frame) e codificar os recortes no VAE. Nada disso
     depende do áudio. O script oficial refaz tudo a cada execução; aqui o
     resultado é gravado por template e reutilizado.
  2. **Progresso.** O upstream imprime barras de tqdm; precisamos de eventos
     estruturados para a fila de jobs.
  3. **Erros.** O upstream engole exceções num `except Exception: print(...)`
     e devolve exit 0. Isso produziria exatamente o "Generation failed" que o
     projeto proíbe.

O MuseTalk usa caminhos relativos ao diretório do repositório, então o worker
faz chdir para lá e espera `models/` apontando para os pesos.
"""

from __future__ import annotations

import gc
import json
import os
import pickle
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

sys.path.insert(0, os.environ.get("CLONE_STUDIO_ROOT", "."))

from core.worker.protocol import (  # noqa: E402
    WorkerFailure,
    emit_log,
    emit_progress,
    run_worker,
)

PREPROCESS_VERSION = 3  # latentes agora mantêm alinhamento 1:1 com os frames


def _ping_pong_cycle(values: list[Any]) -> list[Any]:
    """Ciclo compatível com o upstream, inclusive para listas de 1 item."""
    if not values:
        raise ValueError("Não é possível criar ciclo de uma lista vazia.")
    return values + values[::-1]


def _valid_cycle_targets(
    total: int,
    coord_cycle: list[Any],
    latent_cycle: list[Any],
    placeholder: Any,
) -> list[int]:
    """Índices que precisam de inferência sem perder a linha do tempo."""
    if len(coord_cycle) != len(latent_cycle):
        raise ValueError("Coordenadas e latentes estão desalinhados.")
    return [
        index
        for index in range(total)
        if (
            coord_cycle[index % len(coord_cycle)] != placeholder
            and latent_cycle[index % len(latent_cycle)] is not None
        )
    ]


# ---------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------


def _prepare_repo(request: dict[str, Any]) -> Path:
    """Prepara o repositório do MuseTalk e devolve sua raiz.

    Cria `<repo>/models` apontando para o diretório de pesos que o nosso
    gerenciador baixou, sem duplicar 4 GB no disco.
    """
    repo = Path(request["repo_dir"]).resolve()
    weights = Path(request["weights_dir"]).resolve()

    if not (repo / "musetalk").is_dir():
        raise WorkerFailure(
            f"Código do MuseTalk não encontrado em {repo}.",
            hint="Rode ./scripts/models.sh install musetalk_15 "
                 "(ele clona o repositório no commit fixado).",
            error_type="missing_code",
        )

    unet = weights / "musetalkV15" / "unet.pth"
    if not unet.exists():
        raise WorkerFailure(
            f"Pesos do MuseTalk ausentes ({unet}).",
            hint="Rode ./scripts/models.sh install musetalk_15.",
            error_type="weights_missing",
        )

    link = repo / "models"
    if link.is_symlink():
        if link.resolve() != weights:
            link.unlink()
            link.symlink_to(weights, target_is_directory=True)
    elif not link.exists():
        try:
            link.symlink_to(weights, target_is_directory=True)
        except OSError as exc:
            # No Windows, symlinks podem exigir Developer Mode. Uma junction
            # de diretório oferece a mesma semântica sem copiar 4 GB de pesos.
            if os.name != "nt":
                raise
            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.path.normpath(str(link)), os.path.normpath(str(weights))],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise WorkerFailure(
                    "Não foi possível ligar o MuseTalk ao diretório de pesos.",
                    hint=(
                        "Ative o Developer Mode do Windows ou crie manualmente "
                        f"uma junction de {link} para {weights}."
                    ),
                    detail=(proc.stderr or str(exc))[-2000:],
                    error_type="weights_link_failed",
                ) from exc

    # `from face_detection import ...` só resolve com musetalk/utils no path.
    for entry in (str(repo), str(repo / "musetalk" / "utils")):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    os.chdir(repo)
    return repo


class _Timeline:
    """Cronômetro por fase do lip-sync.

    Existe porque a etapa leva vinte minutos em CPU e, até aqui, só duas fases
    eram medidas: landmarks e inferência. Elas somavam 857s de 1206s — quase
    um terço do tempo ficava invisível, e otimizar o que não se mede é chute.

    Os tempos voltam no resultado do worker, então a diferença entre uma
    execução e outra fica registrada no projeto em vez de sumir no terminal.
    """

    def __init__(self) -> None:
        self.phases: dict[str, float] = {}

    @contextmanager
    def phase(self, name: str):
        started = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - started
            self.phases[name] = round(self.phases.get(name, 0.0) + elapsed, 2)
            emit_log(f"fase '{name}': {elapsed:.1f}s")

    def summary(self) -> str:
        total = sum(self.phases.values()) or 1.0
        partes = sorted(self.phases.items(), key=lambda kv: kv[1], reverse=True)
        return " · ".join(
            f"{nome} {valor:.0f}s ({valor / total * 100:.0f}%)"
            for nome, valor in partes
        )


def _configure_torch(request: dict[str, Any]):
    import torch

    threads = int(request.get("cpu_threads") or 0)
    if threads > 0:
        torch.set_num_threads(threads)

    device_name = request.get("device", "cpu")
    device = torch.device(device_name if torch.cuda.is_available() or
                          device_name == "cpu" else "cpu")
    emit_log(f"device={device} · threads={torch.get_num_threads()}")
    return device


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------


def _extract_frames(video: Path, destination: Path) -> list[str]:
    """Extrai os frames com FFmpeg (barato — não vale a pena cachear)."""
    # Um worker interrompido pode deixar frames de uma entrada anterior. Como
    # a extração é barata, nunca os reutilizamos sem uma chave de conteúdo.
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video),
            "-start_number", "0",
            str(destination / "%08d.png"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise WorkerFailure(
            f"FFmpeg não conseguiu extrair frames de {video.name}.",
            hint="O vídeo do template pode estar corrompido.",
            error_type="ffmpeg_failed",

        )
    frames = sorted(destination.glob("*.png"))
    if not frames:
        raise WorkerFailure(
            f"Nenhum frame extraído de {video.name}.",
            error_type="empty_video",
        )
    return [str(p) for p in frames]


# ---------------------------------------------------------------------------
# Preprocessamento cacheado
# ---------------------------------------------------------------------------


def _preprocess_template(
    frames: list[str],
    cache_dir: Path,
    *,
    bbox_shift: int,
    extra_margin: int,
    vae,
    version: str,
) -> tuple[list, list]:
    """Landmarks/bboxes + latentes do VAE, cacheados por template.

    Esta é a parte cara e independente do áudio: um forward de DWPose e um
    encode de VAE por frame. Reaproveitá-la é o que torna viável gerar vários
    vídeos a partir do mesmo template.
    """
    import cv2
    import numpy as np
    import torch

    cache_dir.mkdir(parents=True, exist_ok=True)
    coords_file = cache_dir / "coords.pkl"
    latents_file = cache_dir / "latents.pt"
    meta_file = cache_dir / "meta.json"

    if coords_file.exists() and latents_file.exists() and meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if (
                meta.get("version") == PREPROCESS_VERSION
                and meta.get("frames") == len(frames)
                and meta.get("bbox_shift") == bbox_shift
                and meta.get("extra_margin") == extra_margin
            ):
                emit_progress(0.30, "Preprocessamento do template reaproveitado.")
                coord_list = pickle.loads(coords_file.read_bytes())
                latents = list(torch.load(latents_file, map_location="cpu"))
                if len(coord_list) != len(frames) or len(latents) != len(frames):
                    raise ValueError("quantidade de frames divergente")
                emit_log(f"cache hit: {cache_dir.name}")
                return coord_list, latents
        except (
            EOFError, OSError, TypeError, ValueError,
            json.JSONDecodeError, pickle.UnpicklingError, RuntimeError,
        ) as exc:
            emit_log(f"cache inválido ({exc}); refazendo", level="warning")

    from musetalk.utils.preprocessing import coord_placeholder, get_landmark_and_bbox

    emit_progress(0.12, f"Detectando rosto em {len(frames)} frames…")
    started = time.monotonic()

    # Em lotes, por dois motivos:
    #   * o upstream carrega TODOS os frames na RAM de uma vez — um vídeo de
    #     60 s a 25 fps em 1080p passaria de 3 GB;
    #   * sem isso o usuário fica sem nenhum sinal de progresso por vários
    #     minutos, que é justamente o passo mais lento em CPU.
    # Cada frame é processado de forma independente pelo upstream, então o
    # resultado em lotes é idêntico ao da chamada única.
    coord_list: list = []
    frame_list: list = []
    batch = max(1, int(os.environ.get("CLONE_STUDIO_LANDMARK_BATCH", "16")))

    for offset in range(0, len(frames), batch):
        chunk = frames[offset : offset + batch]
        chunk_coords, chunk_frames = get_landmark_and_bbox(chunk, bbox_shift)
        coord_list.extend(chunk_coords)
        frame_list.extend(chunk_frames)

        done = min(offset + batch, len(frames))
        elapsed = time.monotonic() - started
        rate = done / elapsed if elapsed > 0 else 0
        remaining = (len(frames) - done) / rate if rate > 0 else 0
        emit_progress(
            0.12 + 0.10 * (done / len(frames)),
            f"rosto {done}/{len(frames)}"
            + (f" · ~{remaining / 60:.1f} min restantes" if remaining > 60 else ""),
        )

    emit_log(
        f"landmarks em {time.monotonic() - started:.1f}s "
        f"({(time.monotonic() - started) / max(len(frames), 1):.2f}s/frame)"
    )

    detected = sum(1 for c in coord_list if c != coord_placeholder)
    if detected == 0:
        raise WorkerFailure(
            "Nenhum rosto detectado no template.",
            hint=(
                "O MuseTalk precisa do rosto visível e razoavelmente frontal. "
                "Use um template em que você olhe para a câmera, bem iluminado "
                "e com o rosto ocupando parte significativa do quadro."
            ),
            error_type="no_face",
        )
    if detected < len(coord_list) * 0.6:
        emit_log(
            f"rosto detectado em apenas {detected}/{len(coord_list)} frames — "
            "a qualidade do lip-sync pode oscilar",
            level="warning",
        )

    emit_progress(0.22, "Codificando recortes no VAE…")
    # Mantém um slot por frame. Remover placeholders aqui deslocaria todos os
    # latentes posteriores em relação a frame_cycle/coord_cycle.
    latent_list: list[Any | None] = []
    total = len(coord_list)
    for index, (bbox, frame) in enumerate(zip(coord_list, frame_list)):
        if bbox == coord_placeholder:
            latent_list.append(None)
            continue
        x1, y1, x2, y2 = bbox
        if version == "v15":
            y2 = min(y2 + extra_margin, frame.shape[0])
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            latent_list.append(None)
            continue
        crop = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_LANCZOS4)
        latent_list.append(vae.get_latents_for_unet(crop))
        if index % 25 == 0:
            emit_progress(
                0.22 + 0.08 * (index / max(total, 1)),
                f"VAE {index}/{total}",
            )

    if not any(latent is not None for latent in latent_list):
        raise WorkerFailure(
            "Nenhum recorte de rosto válido foi produzido.",
            error_type="no_face",
        )

    coords_tmp = cache_dir / "coords.pkl.tmp"
    latents_tmp = cache_dir / "latents.pt.tmp"
    meta_tmp = cache_dir / "meta.json.tmp"
    coords_tmp.write_bytes(pickle.dumps(coord_list))
    torch.save(latent_list, latents_tmp)
    meta_tmp.write_text(
        json.dumps(
            {
                "version": PREPROCESS_VERSION,
                "frames": len(frames),
                "bbox_shift": bbox_shift,
                "extra_margin": extra_margin,
                "detected": detected,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    coords_tmp.replace(coords_file)
    latents_tmp.replace(latents_file)
    meta_tmp.replace(meta_file)
    emit_log(f"preprocessamento cacheado em {cache_dir}")
    return coord_list, latent_list


# ---------------------------------------------------------------------------
# Ações
# ---------------------------------------------------------------------------


def _action_healthcheck(request: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda"] = torch.cuda.is_available()
        import mmcv
        import mmpose

        info["mmcv"] = mmcv.__version__
        info["mmpose"] = mmpose.__version__
    except ImportError as exc:
        raise WorkerFailure(
            f"Stack do MuseTalk incompleto: {exc}",
            hint="./install.sh --only musetalk",
            error_type="missing_dependency",
        ) from exc

    repo = Path(request.get("repo_dir", ""))
    weights = Path(request.get("weights_dir", ""))
    info["code"] = (repo / "musetalk").is_dir()
    info["weights"] = (weights / "musetalkV15" / "unet.pth").exists()

    if info["code"] and info["weights"]:
        _prepare_repo(request)
        from musetalk.utils.blending import get_image  # noqa: F401
        from musetalk.utils.utils import load_all_model  # noqa: F401

        info["imports"] = True
    return info


def _action_process(request: dict[str, Any]) -> dict[str, Any]:
    import copy

    import cv2
    import numpy as np
    import torch

    # Resolve antes do chdir obrigatório do upstream. Assim até uma execução
    # manual com caminhos relativos continua reproduzível.
    request_base = Path.cwd()
    for key in ("video", "audio", "output", "work_dir", "cache_dir", "weights_dir", "repo_dir"):
        path = Path(request[key]).expanduser()
        request[key] = str(path if path.is_absolute() else (request_base / path).resolve())

    repo = _prepare_repo(request)
    device = _configure_torch(request)

    from musetalk.utils.audio_processor import AudioProcessor
    from musetalk.utils.blending import get_image
    from musetalk.utils.face_parsing import FaceParsing
    from musetalk.utils.preprocessing import coord_placeholder
    from musetalk.utils.utils import datagen, load_all_model
    from transformers import WhisperModel

    video = Path(request["video"])
    audio = Path(request["audio"])
    output = Path(request["output"])
    work_dir = Path(request["work_dir"])
    cache_dir = Path(request["cache_dir"])
    version = request.get("version", "v15")
    fps = float(request.get("fps", 25))
    batch_size = int(request.get("batch_size", 1))
    extra_margin = int(request.get("extra_margin", 10))
    bbox_shift = int(request.get("bbox_shift", 0))
    parsing_mode = request.get("parsing_mode", "jaw")

    for path in (video, audio):
        if not path.exists():
            raise WorkerFailure(
                f"Arquivo de entrada não encontrado: {path}",
                error_type="missing_input",
            )

    work_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = work_dir / "frames"
    result_dir = work_dir / "result"
    generated_dir = work_dir / "generated"
    shutil.rmtree(result_dir, ignore_errors=True)
    shutil.rmtree(generated_dir, ignore_errors=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)

    timeline = _Timeline()

    # -- modelos ---------------------------------------------------------
    emit_progress(0.02, "Carregando modelos do MuseTalk…")
    weights = Path(request["weights_dir"])
    with timeline.phase("modelos"):
        vae, unet, pe = load_all_model(
            unet_model_path=str(weights / "musetalkV15" / "unet.pth"),
            vae_type="sd-vae",
            unet_config=str(weights / "musetalkV15" / "musetalk.json"),
            device=device,
        )
        timesteps = torch.tensor([0], device=device)

        pe = pe.to(device)
        vae.vae = vae.vae.to(device)
        unet.model = unet.model.to(device)
        weight_dtype = unet.model.dtype

        whisper_dir = str(weights / "whisper")
        audio_processor = AudioProcessor(feature_extractor_path=whisper_dir)
        whisper = WhisperModel.from_pretrained(whisper_dir)
        whisper = whisper.to(device=device, dtype=weight_dtype).eval()
        whisper.requires_grad_(False)

        face_parser = (
            FaceParsing(
                left_cheek_width=int(request.get("left_cheek_width", 90)),
                right_cheek_width=int(request.get("right_cheek_width", 90)),
            )
            if version == "v15"
            else FaceParsing()
        )

    # -- áudio -----------------------------------------------------------
    emit_progress(0.08, "Extraindo features de áudio (Whisper)…")
    with timeline.phase("audio"):
        features, librosa_length = audio_processor.get_audio_feature(str(audio))
        whisper_chunks = audio_processor.get_whisper_chunk(
            features,
            device,
            weight_dtype,
            whisper,
            librosa_length,
            fps=fps,
            audio_padding_length_left=int(request.get("audio_padding_left", 2)),
            audio_padding_length_right=int(request.get("audio_padding_right", 2)),
        )
    emit_log(f"{len(whisper_chunks)} chunks de áudio a {fps} fps")

    # Libera o Whisper: em CPU cada modelo residente custa RAM cara.
    del whisper
    del features, audio_processor
    gc.collect()

    # -- template --------------------------------------------------------
    with timeline.phase("template"):
        frames = _extract_frames(video, frames_dir)
        coord_list, latent_list = _preprocess_template(
            frames,
            cache_dir,
            bbox_shift=bbox_shift,
            extra_margin=extra_margin,
            vae=vae,
            version=version,
        )
        frame_list = [cv2.imread(f) for f in frames]

    # Ida e volta: se o áudio for mais longo que o template, o vídeo reflete
    # em vez de dar um salto brusco ao voltar para o primeiro frame.
    frame_cycle = _ping_pong_cycle(frame_list)
    coord_cycle = _ping_pong_cycle(coord_list)
    latent_cycle = _ping_pong_cycle(latent_list)

    # -- inferência ------------------------------------------------------
    total_frames = len(whisper_chunks)
    emit_progress(0.32, f"Gerando {total_frames} frames de fala…")
    started = time.monotonic()

    valid_target_indices = _valid_cycle_targets(
        total_frames, coord_cycle, latent_cycle, coord_placeholder
    )
    valid_whisper_chunks = [whisper_chunks[index] for index in valid_target_indices]
    valid_latents = [latent_cycle[index % len(latent_cycle)] for index in valid_target_indices]

    generator = datagen(
        whisper_chunks=valid_whisper_chunks,
        vae_encode_latents=valid_latents,
        batch_size=batch_size,
        delay_frame=0,
        device=device,
    )

    batches = int(np.ceil(len(valid_target_indices) / batch_size))
    generated = 0

    with timeline.phase("inferencia"), torch.inference_mode():
        for index, (whisper_batch, latent_batch) in enumerate(generator):
            audio_features = pe(whisper_batch)
            latent_batch = latent_batch.to(dtype=unet.model.dtype)
            predicted = unet.model(
                latent_batch, timesteps, encoder_hidden_states=audio_features
            ).sample
            recon = vae.decode_latents(predicted)
            for reconstructed in recon:
                image = np.ascontiguousarray(reconstructed, dtype=np.uint8)
                dest_file = generated_dir / f"{generated:08d}.png"
                if not cv2.imwrite(str(dest_file), image):
                    raise WorkerFailure(
                        "Não foi possível gravar um frame intermediário do lip-sync.",
                        hint="Verifique espaço livre e permissão no diretório cache/.",
                        error_type="write_failed",
                    )
                generated += 1

            done = index + 1
            elapsed = time.monotonic() - started
            rate = done / elapsed if elapsed > 0 else 0
            remaining = (batches - done) / rate if rate > 0 else 0
            emit_progress(
                0.32 + 0.48 * (done / max(batches, 1)),
                f"frame {min(done * batch_size, len(valid_target_indices))}/"
                f"{len(valid_target_indices)}"
                + (f" · ~{remaining / 60:.1f} min restantes" if remaining > 90 else ""),
            )

    emit_log(
        f"inferência: {time.monotonic() - started:.1f}s "
        f"({(time.monotonic() - started) / max(total_frames, 1):.2f}s/frame)"
    )

    # Modelos fora da memória antes da etapa de composição.
    del unet, pe, vae, valid_whisper_chunks, valid_latents
    gc.collect()

    # -- composição ------------------------------------------------------
    emit_progress(0.82, "Recompondo os frames no vídeo original…")
    # `__enter__`/`__exit__` na mão em vez de `with`: o bloco cronometrado tem
    # dezenas de linhas e reindentá-lo só para medir tempo traria mais risco de
    # erro do que valor. Se algo estourar no meio, a fase não é registrada — e
    # nesse caminho o relatório de tempos não é emitido de qualquer forma.
    recompose_timer = timeline.phase("recomposicao")
    recompose_timer.__enter__()
    written = 0
    generated_index = 0
    valid_targets = set(valid_target_indices)
    for index in range(total_frames):
        bbox = coord_cycle[index % len(coord_cycle)]
        original = copy.deepcopy(frame_cycle[index % len(frame_cycle)])
        combined = original
        if index in valid_targets:
            res_frame = cv2.imread(str(generated_dir / f"{generated_index:08d}.png"))
            generated_index += 1
            x1, y1, x2, y2 = bbox
            if version == "v15":
                y2 = min(y2 + extra_margin, original.shape[0])
            try:
                resized = cv2.resize(res_frame, (x2 - x1, y2 - y1))
                if version == "v15":
                    combined = get_image(
                        original, resized, [x1, y1, x2, y2],
                        mode=parsing_mode, fp=face_parser,
                    )
                else:
                    combined = get_image(
                        original, resized, [x1, y1, x2, y2], fp=face_parser
                    )
            except (cv2.error, TypeError):
                emit_log(f"frame {index}: composição inválida; preservando original", level="warning")

        combined_img = np.ascontiguousarray(combined, dtype=np.uint8)
        if not cv2.imwrite(str(result_dir / f"{written:08d}.png"), combined_img):
            raise WorkerFailure(
                "Não foi possível gravar os frames compostos.",
                error_type="write_failed",
            )
        written += 1

        if index % 30 == 0:
            emit_progress(
                0.82 + 0.12 * (index / max(total_frames, 1)),
                f"composição {index}/{total_frames}",
            )

    if written == 0:
        raise WorkerFailure(
            "Nenhum frame foi composto.",
            hint="Todos os frames ficaram sem detecção de rosto válida.",
            error_type="no_output_frames",
        )

    recompose_timer.__exit__(None, None, None)

    # -- montagem --------------------------------------------------------
    emit_progress(0.95, "Montando o vídeo…")
    montagem = timeline.phase("montagem")
    montagem.__enter__()
    output.parent.mkdir(parents=True, exist_ok=True)
    silent = work_dir / "silent.mp4"

    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-r", str(fps),
            "-f", "image2",
            "-i", str(result_dir / "%08d.png"),
            "-c:v", "libx264",
            "-crf", "16",
            "-pix_fmt", "yuv420p",
            str(silent),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise WorkerFailure(
            "FFmpeg falhou ao montar os frames gerados.",
            detail=proc.stderr[-2000:],
            error_type="ffmpeg_failed",
        )

    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(silent),
            "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise WorkerFailure(
            "FFmpeg falhou ao juntar áudio e vídeo.",
            detail=proc.stderr[-2000:],
            error_type="ffmpeg_failed",
        )

    montagem.__exit__(None, None, None)

    if request.get("cleanup", True):
        shutil.rmtree(frames_dir, ignore_errors=True)
        shutil.rmtree(result_dir, ignore_errors=True)
        shutil.rmtree(generated_dir, ignore_errors=True)
        silent.unlink(missing_ok=True)

    emit_log(f"tempos: {timeline.summary()}")
    emit_progress(1.0, "Lip-sync concluído.")
    return {
        "output": str(output),
        "frames": written,
        "fps": fps,
        "batch_size": batch_size,
        "seconds_per_frame": round(
            (time.monotonic() - started) / max(total_frames, 1), 3
        ),
        # Os tempos por fase voltam para o host: comparar duas execuções deixa
        # de depender de alguém ter guardado o log do terminal.
        "phase_seconds": timeline.phases,
    }


_ACTIONS = {
    "healthcheck": _action_healthcheck,
    "process": _action_process,
}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    action = request.get("action", "process")
    handler = _ACTIONS.get(action)
    if handler is None:
        raise WorkerFailure(f"Ação desconhecida: {action}", error_type="bad_request")
    return handler(request)


if __name__ == "__main__":
    raise SystemExit(run_worker(handle))
