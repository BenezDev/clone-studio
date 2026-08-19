"""Camada de mídia: FFmpeg/ffprobe.

FFmpeg é a engine de mídia principal do projeto (encode/decode/concat/crop/
scale/áudio/legendas). Todo acesso passa por aqui para que os presets fiquem
num lugar só e todo comando executado seja registrado.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from core.config.schema import PreviewConfig, VideoConfig


class FFmpegError(RuntimeError):
    """Falha de FFmpeg com comando, exit code e stderr preservados."""

    def __init__(
        self, message: str, command: Sequence[str], exit_code: int, stderr: str
    ) -> None:
        self.command = list(command)
        self.exit_code = exit_code
        self.stderr = stderr
        hint = _ffmpeg_hint(stderr)
        detail = [message, f"exit code: {exit_code}", "comando: " + " ".join(command)]
        if hint:
            detail.insert(1, f"Provável solução: {hint}")
        tail = "\n".join(stderr.strip().splitlines()[-25:])
        if tail:
            detail.append("stderr (final):\n" + tail)
        super().__init__("\n".join(detail))
        self.hint = hint


def _ffmpeg_hint(stderr: str) -> str:
    lowered = stderr.lower()
    if "no such file or directory" in lowered:
        return "Arquivo de entrada não existe. Confira o caminho."
    if "invalid data found" in lowered:
        return "Arquivo de entrada corrompido ou em formato não suportado."
    if "unknown encoder" in lowered:
        return (
            "Este build do FFmpeg não tem o encoder pedido. Verifique com "
            "`ffmpeg -encoders | grep x264`."
        )
    if "height not divisible by 2" in lowered or "width not divisible by 2" in lowered:
        return "Dimensões ímpares não são válidas em yuv420p; use valores pares."
    if "permission denied" in lowered:
        return "Sem permissão de escrita no diretório de saída."
    if "no space left" in lowered:
        return "Disco cheio."
    return ""


def ffmpeg_binary() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FileNotFoundError(
            "FFmpeg não encontrado no PATH. Instale com "
            "`sudo apt install ffmpeg` e rode ./install.sh novamente."
        )
    return path


def ffprobe_binary() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise FileNotFoundError(
            "ffprobe não encontrado no PATH (vem junto com o pacote ffmpeg)."
        )
    return path


def run_ffmpeg(args: Sequence[str], *, timeout: int | None = None) -> str:
    """Executa ffmpeg com os argumentos dados (sem o binário nem -y)."""
    command = [ffmpeg_binary(), "-hide_banner", "-nostdin", "-y", *args]
    proc = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        raise FFmpegError(
            "Falha ao executar FFmpeg.", command, proc.returncode, proc.stderr
        )
    return proc.stderr


# ---------------------------------------------------------------------------
# Sondagem
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    nb_frames: int | None = None


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration: float
    size_bytes: int
    format_name: str
    streams: tuple[StreamInfo, ...]

    @property
    def video(self) -> StreamInfo | None:
        return next((s for s in self.streams if s.codec_type == "video"), None)

    @property
    def audio(self) -> StreamInfo | None:
        return next((s for s in self.streams if s.codec_type == "audio"), None)

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    @property
    def resolution(self) -> tuple[int, int] | None:
        stream = self.video
        if stream and stream.width and stream.height:
            return stream.width, stream.height
        return None


def _parse_fraction(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            denominator = float(den)
            if denominator == 0:
                return None
            return float(num) / denominator
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def probe(path: Path | str, *, timeout: int = 30) -> MediaInfo:
    """Lê metadados reais do arquivo via ffprobe."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"Arquivo não encontrado para probe: {target}")

    command = [
        ffprobe_binary(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(target),
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(
            f"ffprobe excedeu o limite de {timeout}s ao ler {target.name}.",
            command,
            -1,
            str(exc),
        ) from exc
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffprobe não conseguiu decodificar {target.name}.",
            command,
            proc.returncode,
            proc.stderr,
        )

    data: dict[str, Any] = json.loads(proc.stdout or "{}")
    fmt = data.get("format", {})
    streams: list[StreamInfo] = []
    for raw in data.get("streams", []):
        nb_frames = raw.get("nb_frames")
        streams.append(
            StreamInfo(
                index=int(raw.get("index", 0)),
                codec_type=raw.get("codec_type", "unknown"),
                codec_name=raw.get("codec_name", "unknown"),
                width=raw.get("width"),
                height=raw.get("height"),
                fps=_parse_fraction(raw.get("avg_frame_rate"))
                or _parse_fraction(raw.get("r_frame_rate")),
                sample_rate=int(raw["sample_rate"]) if raw.get("sample_rate") else None,
                channels=raw.get("channels"),
                nb_frames=int(nb_frames) if nb_frames and nb_frames.isdigit() else None,
            )
        )

    duration = 0.0
    if fmt.get("duration") not in (None, "N/A"):
        try:
            duration = float(fmt["duration"])
        except ValueError:
            duration = 0.0
    if duration <= 0:
        for raw in data.get("streams", []):
            if raw.get("duration") not in (None, "N/A"):
                try:
                    duration = max(duration, float(raw["duration"]))
                except ValueError:
                    pass
            tags = raw.get("tags") or {}
            for k, v in tags.items():
                if "duration" in k.lower() and isinstance(v, str):
                    parts = v.split(":")
                    if len(parts) == 3:
                        try:
                            h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
                            duration = max(duration, h * 3600 + m * 60 + s)
                        except ValueError:
                            pass

    return MediaInfo(
        path=target,
        duration=duration,
        size_bytes=int(fmt.get("size", target.stat().st_size)),
        format_name=fmt.get("format_name", ""),
        streams=tuple(streams),
    )


# ---------------------------------------------------------------------------
# Validação de estágio
# ---------------------------------------------------------------------------


class ValidationError(RuntimeError):
    pass


def validate_audio(
    path: Path,
    *,
    min_duration: float = 0.15,
    expected_sample_rate: int | None = None,
) -> MediaInfo:
    info = probe(path)
    if not info.has_audio:
        raise ValidationError(f"{path.name} não contém stream de áudio.")
    if info.duration < min_duration:
        raise ValidationError(
            f"{path.name} tem duração de {info.duration:.3f}s "
            f"(mínimo {min_duration}s). O TTS provavelmente falhou em silêncio."
        )
    audio = info.audio
    if expected_sample_rate and audio and audio.sample_rate != expected_sample_rate:
        raise ValidationError(
            f"{path.name} está a {audio.sample_rate} Hz; "
            f"esperado {expected_sample_rate} Hz."
        )
    return info


def validate_video(
    path: Path,
    *,
    min_duration: float = 0.2,
    expected_resolution: tuple[int, int] | None = None,
    require_audio: bool = False,
) -> MediaInfo:
    info = probe(path)
    if not info.has_video:
        raise ValidationError(f"{path.name} não contém stream de vídeo.")
    if info.duration < min_duration:
        raise ValidationError(
            f"{path.name} tem duração de {info.duration:.3f}s "
            f"(mínimo {min_duration}s)."
        )
    if expected_resolution and info.resolution != expected_resolution:
        raise ValidationError(
            f"{path.name} está em {info.resolution}; "
            f"esperado {expected_resolution}."
        )
    if require_audio and not info.has_audio:
        raise ValidationError(f"{path.name} não contém stream de áudio.")

    video = info.video
    if video and video.fps and info.duration:
        expected_frames = video.fps * info.duration
        if expected_frames < 1:
            raise ValidationError(
                f"{path.name} tem menos de um frame ({expected_frames:.2f})."
            )
    return info


# ---------------------------------------------------------------------------
# Operações de alto nível
# ---------------------------------------------------------------------------


def ensure_container_metadata(path: Path, *, timeout: int = 300) -> bool:
    """Repara no lugar um arquivo gravado em stream. Devolve True se reparou.

    Existe por causa do ``MediaRecorder`` do navegador, usado pela gravação
    dentro do app. Ele escreve direto num stream, e quem grava WebM/Matroska
    numa saída não-buscável não pode voltar ao início para preencher o
    cabeçalho: o arquivo sai **sem duração** e sem índice de busca (Cues).

    O sintoma é traiçoeiro. O ffprobe devolve ``duration: N/A``, ``probe()``
    converte para ``0.0`` e toda validação do projeto rejeita o arquivo — com
    razão, porque duração zero é exatamente o que um arquivo truncado mostra.
    Sem este reparo, uma gravação perfeita seria recusada com "0.0s".

    O conserto é um remux: ``-fflags +genpts`` reconstrói os timestamps que o
    navegador não escreveu, o ffmpeg percorre todos os pacotes, calcula a
    duração real e grava um cabeçalho completo. ``-c copy`` mantém os bits de
    vídeo e áudio intactos — não há reencode, não há perda de qualidade e o
    custo é de I/O, o que importa numa máquina sem GPU.

    Arquivo já íntegro não é tocado, e o reparo é idempotente.

    **Não existe fallback de reencode aqui, e isso é deliberado.** O reparo tem
    que devolver o arquivo no mesmo caminho, logo no mesmo contêiner — e um
    contêiner não aceita qualquer codec. Reencodar para H.264 dentro de um
    ``.webm`` falha na hora ("Only VP8 or VP9 or AV1 video ... are supported for
    WebM"), trocando um erro de validação claro por um erro de ffmpeg
    incompreensível. Reencodar para VP9 custaria minutos de CPU. Quando o remux
    não basta, quem escala é a camada de cima, que pode trocar o contêiner:
    ``templates.py`` converte para MP4, ``voice.py`` converte para WAV. Se nem
    isso resolver, o arquivo está realmente quebrado e a validação seguinte dá
    o erro específico ao usuário.
    """
    if probe(path).duration > 0:
        return False

    repaired = path.parent / f"{path.stem}.remux{path.suffix}"
    repaired.unlink(missing_ok=True)
    try:
        run_ffmpeg(
            [
                "-fflags", "+genpts",
                "-avoid_negative_ts", "make_zero",
                "-i", str(path),
                "-c", "copy",
                str(repaired),
            ],
            timeout=timeout,
        )
        if probe(repaired).duration <= 0:
            return False
        repaired.replace(path)
        return True
    finally:
        repaired.unlink(missing_ok=True)


def to_wav(
    source: Path,
    destination: Path,
    *,
    sample_rate: int = 24000,
    mono: bool = True,
    normalize: bool = False,
    trim_silence: bool = False,
) -> Path:
    """Converte qualquer entrada de áudio para WAV PCM 16-bit limpo."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    filters: list[str] = []
    if trim_silence:
        filters.append(
            "silenceremove=start_periods=1:start_threshold=-50dB:"
            "start_silence=0.1:stop_periods=-1:stop_threshold=-50dB:"
            "stop_silence=0.3"
        )
    if normalize:
        # Normalização de pico com margem — preserva a dinâmica da voz, ao
        # contrário de um loudnorm agressivo numa amostra de referência.
        filters.append("dynaudnorm=f=200:g=5:p=0.9")

    args = ["-i", str(source)]
    if filters:
        args += ["-af", ",".join(filters)]
    args += [
        "-ac",
        "1" if mono else "2",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        "-vn",
        str(destination),
    ]
    run_ffmpeg(args)
    return destination


def loudnorm(
    source: Path,
    destination: Path,
    *,
    lufs: float = -14.0,
    true_peak: float = -1.5,
    sample_rate: int = 48000,
) -> Path:
    """Loudness de redes sociais (EBU R128), em dois passos para precisão."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    measure_filter = (
        f"loudnorm=I={lufs}:TP={true_peak}:LRA=11:print_format=json"
    )
    stderr = run_ffmpeg(
        ["-i", str(source), "-af", measure_filter, "-f", "null", "-"]
    )

    measured: dict[str, Any] = {}
    start = stderr.rfind("{")
    if start != -1:
        try:
            measured = json.loads(stderr[start : stderr.rfind("}") + 1])
        except json.JSONDecodeError:
            measured = {}

    if measured:
        apply_filter = (
            f"loudnorm=I={lufs}:TP={true_peak}:LRA=11"
            f":measured_I={measured.get('input_i')}"
            f":measured_TP={measured.get('input_tp')}"
            f":measured_LRA={measured.get('input_lra')}"
            f":measured_thresh={measured.get('input_thresh')}"
            f":offset={measured.get('target_offset', 0)}"
            ":linear=true:print_format=summary"
        )
    else:
        apply_filter = f"loudnorm=I={lufs}:TP={true_peak}:LRA=11"

    run_ffmpeg(
        [
            "-i",
            str(source),
            "-af",
            apply_filter,
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
    )
    return destination


def build_vertical_filter(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> str:
    """Filtro que leva qualquer entrada a 9:16 preenchendo o quadro.

    Escala cobrindo o quadro e corta o excesso (sem barras pretas), que é o
    comportamento esperado em Reels/TikTok/Shorts.
    """
    return (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
        f"crop={target_width}:{target_height},setsar=1"
    )


def _escape_subtitle_path(path: Path | str) -> str:
    """Escapa o caminho do arquivo de legendas para uso no filtro FFmpeg.

    No Windows e POSIX, converte para forward slashes e escapa ':' e '\''.
    """
    posix_path = Path(path).resolve().as_posix()
    return posix_path.replace(":", r"\:").replace("'", r"\'")


def render_final(
    video_source: Path,
    audio_source: Path | None,
    destination: Path,
    config: VideoConfig,
    *,
    subtitles: Path | None = None,
    extra_filters: Sequence[str] = (),
) -> Path:
    """Render final no preset TikTok/Reels/Shorts."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    filters = [
        build_vertical_filter(0, 0, config.width, config.height),
        f"fps={config.fps}",
    ]
    filters.extend(extra_filters)
    if subtitles is not None:
        filters.append(f"subtitles='{_escape_subtitle_path(subtitles)}'")

    args = ["-i", str(video_source)]
    if audio_source is not None:
        args += ["-i", str(audio_source)]

    args += [
        "-vf",
        ",".join(filters),
        "-c:v",
        config.video_codec,
        "-preset",
        config.preset,
        "-crf",
        str(config.crf),
        "-pix_fmt",
        config.pixel_format,
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-movflags",
        "+faststart",
    ]

    if audio_source is not None:
        args += [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:a",
            config.audio_codec,
            "-b:a",
            "192k",
            "-ar",
            str(config.audio_sample_rate),
            "-shortest",
        ]
    else:
        args += ["-c:a", config.audio_codec, "-b:a", "192k", "-ar",
                 str(config.audio_sample_rate)]

    args.append(str(destination))
    run_ffmpeg(args)
    return destination


def render_preview(
    video_source: Path,
    audio_source: Path | None,
    destination: Path,
    config: PreviewConfig,
    *,
    subtitles: Path | None = None,
) -> Path:
    """Preview rápido e barato — para descobrir erros antes do render final."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    filters = [
        build_vertical_filter(0, 0, config.width, config.height),
    ]
    if subtitles is not None:
        filters.append(f"subtitles='{_escape_subtitle_path(subtitles)}'")

    args: list[str] = []
    if config.max_seconds > 0:
        args += ["-t", str(config.max_seconds)]
    args += ["-i", str(video_source)]
    if audio_source is not None:
        if config.max_seconds > 0:
            args += ["-t", str(config.max_seconds)]
        args += ["-i", str(audio_source)]

    args += [
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-preset",
        config.preset,
        "-crf",
        str(config.crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if audio_source is not None:
        args += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "96k",
                 "-shortest"]
    else:
        args += ["-c:a", "aac", "-b:a", "96k"]

    args.append(str(destination))
    run_ffmpeg(args)
    return destination


def extract_thumbnail(
    video_source: Path, destination: Path, *, timestamp: float | None = None
) -> Path:
    """Extrai um frame como JPEG.

    Sem timestamp, usa o filtro `thumbnail` do FFmpeg, que escolhe o frame mais
    representativo de um lote em vez de um instante arbitrário.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if timestamp is None:
        args = [
            "-i",
            str(video_source),
            "-vf",
            "thumbnail=100",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(destination),
        ]
    else:
        args = [
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_source),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(destination),
        ]
    run_ffmpeg(args)
    return destination


def concat_videos(sources: Sequence[Path], destination: Path) -> Path:
    """Concatena vídeos já normalizados (mesmo codec/resolução/fps)."""
    if not sources:
        raise ValueError("Nenhum vídeo para concatenar.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    list_file = destination.with_suffix(".concat.txt")
    list_file.write_text(
        "\n".join(f"file '{Path(s).resolve().as_posix()}'" for s in sources) + "\n",
        encoding="utf-8",
    )
    try:
        run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                str(destination),
            ]
        )
    finally:
        list_file.unlink(missing_ok=True)
    return destination


def silence(destination: Path, duration: float, sample_rate: int = 24000) -> Path:
    """Gera um WAV de silêncio (usado em testes e preenchimento)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={sample_rate}:cl=mono",
            "-t",
            str(duration),
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
    )
    return destination
