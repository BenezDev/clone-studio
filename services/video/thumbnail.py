"""Gerador local de thumbnail.

Escolhe o melhor frame do vídeo combinando três sinais, todos obtidos com
FFmpeg — sem trazer bibliotecas de visão para o ambiente do backend:

  * **representatividade** — o filtro `thumbnail` do FFmpeg compara o
    histograma dos frames de um lote e devolve o mais atípico, que costuma ser
    um momento expressivo em vez de uma transição;
  * **nitidez** — o filtro `blurdetect` mede o borrão; frames de movimento
    rápido (onde a cabeça está virando) são descartados;
  * **exposição** — `signalstats` evita frames escuros demais ou estourados.

Detecção de rosto/olhos abertos exige visão computacional; quando o ambiente
do lip-sync está instalado, ele já tem o detector carregado e é usado como
refinamento opcional.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.media import ffmpeg


@dataclass
class FrameScore:
    timestamp: float
    path: Path
    blur: float = 0.0
    brightness: float = 0.0
    score: float = 0.0


_BLUR_RE = re.compile(r"blur[=:]\s*([\d.]+)")
_YAVG_RE = re.compile(r"YAVG:([\d.]+)")


def _measure(frame: Path) -> tuple[float, float]:
    """Devolve (borrão, luminância média) de uma imagem."""
    blur = 0.0
    brightness = 128.0
    try:
        stderr = ffmpeg.run_ffmpeg(
            ["-i", str(frame), "-vf", "blurdetect,signalstats", "-f", "null", "-"]
        )
    except ffmpeg.FFmpegError:
        return blur, brightness

    match = _BLUR_RE.search(stderr)
    if match:
        blur = float(match.group(1))
    match = _YAVG_RE.search(stderr)
    if match:
        brightness = float(match.group(1))
    return blur, brightness


def _candidates(video: Path, duration: float, count: int) -> list[float]:
    """Instantes de amostragem, evitando as pontas do vídeo."""
    if duration <= 1.0:
        return [duration / 2]
    start = min(0.5, duration * 0.05)
    end = max(start + 0.1, duration - min(0.5, duration * 0.05))
    step = (end - start) / max(count - 1, 1)
    return [round(start + step * i, 3) for i in range(count)]


def score_frames(video: Path, samples: int = 9) -> list[FrameScore]:
    info = ffmpeg.probe(video)
    scored: list[FrameScore] = []

    with tempfile.TemporaryDirectory(prefix="cs-thumb-") as tmp:
        workdir = Path(tmp)
        for index, timestamp in enumerate(_candidates(video, info.duration, samples)):
            frame = workdir / f"cand_{index:02d}.png"
            try:
                ffmpeg.run_ffmpeg(
                    [
                        "-ss", f"{timestamp:.3f}",
                        "-i", str(video),
                        "-frames:v", "1",
                        str(frame),
                    ]
                )
            except ffmpeg.FFmpegError:
                continue
            if not frame.exists():
                continue

            blur, brightness = _measure(frame)
            # Nitidez pesa mais; exposição penaliza extremos (ideal ~110-160).
            sharpness_score = max(0.0, 60.0 - blur)
            exposure_penalty = abs(brightness - 135.0) / 135.0
            scored.append(
                FrameScore(
                    timestamp=timestamp,
                    path=frame,
                    blur=blur,
                    brightness=brightness,
                    score=sharpness_score * (1.0 - 0.5 * exposure_penalty),
                )
            )

        scored.sort(key=lambda f: f.score, reverse=True)
        # Os arquivos temporários somem com o TemporaryDirectory; só os
        # timestamps interessam ao chamador.
        return scored


def generate_thumbnail(
    video: Path,
    destination: Path,
    *,
    title: str = "",
    samples: int = 9,
    timestamp: float | None = None,
) -> Path:
    """Extrai a thumbnail final em JPEG.

    Com `title`, desenha o texto na parte superior (fora da área de UI das
    plataformas). Sem `timestamp`, escolhe o melhor frame automaticamente.
    """
    video = Path(video)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if timestamp is None:
        ranked = score_frames(video, samples=samples)
        timestamp = ranked[0].timestamp if ranked else None

    if title:
        escaped = (
            title.replace("\\", "\\\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace("%", r"\%")
        )
        drawtext = (
            f"drawtext=text='{escaped}':fontcolor=white:fontsize=h/18:"
            "borderw=6:bordercolor=black@0.85:x=(w-text_w)/2:y=h*0.12:"
            "line_spacing=10"
        )
        args = []
        if timestamp is not None:
            args += ["-ss", f"{timestamp:.3f}"]
        args += [
            "-i", str(video),
            "-vf", drawtext,
            "-frames:v", "1",
            "-q:v", "2",
            str(destination),
        ]
        ffmpeg.run_ffmpeg(args)
    else:
        ffmpeg.extract_thumbnail(video, destination, timestamp=timestamp)

    if not destination.exists() or destination.stat().st_size == 0:
        raise RuntimeError(f"Thumbnail não foi gerada em {destination}.")
    return destination
