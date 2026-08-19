"""Biblioteca de templates: indexação, seleção e composição.

O Production Mode escolhe automaticamente qual (ou quais) vídeos seus serão
usados como base, com dois problemas a resolver:

  1. **Adequação**: um roteiro agressivo pede um template de energia alta;
     um roteiro sério pede gestos contidos.
  2. **Duração**: se o áudio for mais longo que o template, é preciso compor
     vários trechos sem que a emenda seja perceptível — e sem esticar o vídeo,
     o que destruiria o realismo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.media import ffmpeg
from core.storage.paths import get_paths
from services.templates.models import (
    Camera,
    Energy,
    Gestures,
    Style,
    TemplateMetadata,
)

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


class TemplateError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Indexação
# ---------------------------------------------------------------------------


def index_templates(directory: Path | None = None) -> list[TemplateMetadata]:
    """Varre o diretório de templates e cria/atualiza os metadados.

    Vídeos novos ganham um `.json` com valores padrão que o usuário pode
    ajustar depois na página Templates. Campos técnicos (resolução, fps,
    duração) são sempre relidos do arquivo — nunca confiamos no que está
    escrito no JSON.
    """
    paths = get_paths()
    target = directory or paths.templates_dir
    target.mkdir(parents=True, exist_ok=True)

    results: list[TemplateMetadata] = []
    for video in sorted(target.iterdir()):
        if video.suffix.lower() not in VIDEO_SUFFIXES or not video.is_file():
            continue

        metadata_path = video.with_suffix(".json")
        try:
            info = ffmpeg.probe(video)
        except (ffmpeg.FFmpegError, FileNotFoundError):
            continue
        if not info.has_video:
            continue

        if metadata_path.exists():
            metadata = TemplateMetadata.load(metadata_path)
        else:
            metadata = TemplateMetadata(
                id=_slugify(video.stem),
                duration=0.0,
            )

        stream = info.video
        metadata.duration = round(info.duration, 2)
        metadata.width = stream.width or 0
        metadata.height = stream.height or 0
        metadata.fps = round(stream.fps or 0.0, 3)
        metadata.has_audio = info.has_audio
        metadata.video_path = str(video.relative_to(paths.root))
        metadata.orientation = "vertical" if metadata.is_vertical else "horizontal"

        metadata.save(metadata_path)
        results.append(metadata)

    return results


def load_templates(only_enabled: bool = True) -> list[TemplateMetadata]:
    paths = get_paths()
    if not paths.templates_dir.exists():
        return []
    templates: list[TemplateMetadata] = []
    for metadata_path in sorted(paths.templates_dir.glob("*.json")):
        try:
            metadata = TemplateMetadata.load(metadata_path)
        except (ValueError, TypeError):
            continue
        if only_enabled and not metadata.enabled:
            continue
        video = paths.root / metadata.video_path if metadata.video_path else None
        if video is None or not video.exists():
            continue
        templates.append(metadata)
    return templates


def template_path(metadata: TemplateMetadata) -> Path:
    return get_paths().root / metadata.video_path


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    return slug.strip("_") or "template"


# ---------------------------------------------------------------------------
# Seleção
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionCriteria:
    """O que o roteiro pede de um template."""

    energy: str = Energy.MEDIUM.value
    gestures: str = Gestures.MEDIUM.value
    style: str = Style.TALKING_HEAD.value
    camera: str | None = None
    background: str | None = None
    min_duration: float = 0.0
    exclude: tuple[str, ...] = ()


_ENERGY_ORDER = {Energy.LOW.value: 0, Energy.MEDIUM.value: 1, Energy.HIGH.value: 2}
_GESTURE_ORDER = {
    Gestures.NONE.value: 0,
    Gestures.LOW.value: 1,
    Gestures.MEDIUM.value: 2,
    Gestures.HIGH.value: 3,
}


def score_template(
    metadata: TemplateMetadata, criteria: SelectionCriteria
) -> float:
    """Pontua o quão bem um template atende ao critério (maior = melhor)."""
    score = 0.0

    wanted = _ENERGY_ORDER.get(criteria.energy, 1)
    actual = _ENERGY_ORDER.get(metadata.energy, 1)
    score += 3.0 - abs(wanted - actual) * 1.5

    wanted_g = _GESTURE_ORDER.get(criteria.gestures, 2)
    actual_g = _GESTURE_ORDER.get(metadata.gestures, 2)
    score += 2.0 - abs(wanted_g - actual_g) * 0.8

    if metadata.style == criteria.style:
        score += 2.0
    if criteria.camera and metadata.camera == criteria.camera:
        score += 1.0
    if criteria.background and metadata.background == criteria.background:
        score += 1.0

    # Templates verticais são fortemente preferidos: recortar horizontal para
    # 9:16 corta o enquadramento e costuma decapitar o rosto.
    if metadata.orientation == "vertical":
        score += 2.5

    # Cobrir o áudio com um único template evita emendas.
    if criteria.min_duration and metadata.duration >= criteria.min_duration:
        score += 2.0
    elif criteria.min_duration:
        score += metadata.duration / criteria.min_duration

    return score


def choose_template(
    criteria: SelectionCriteria,
    templates: list[TemplateMetadata] | None = None,
) -> TemplateMetadata:
    candidates = [
        t
        for t in (templates if templates is not None else load_templates())
        if t.id not in criteria.exclude
    ]
    if not candidates:
        raise TemplateError(
            "Nenhum template disponível. Adicione vídeos verticais seus em "
            "data/identity/templates/ e rode a indexação."
        )
    return max(candidates, key=lambda t: score_template(t, criteria))


def criteria_from_script(
    *,
    emotion: str = "",
    style_hint: str = "",
    audio_duration: float = 0.0,
) -> SelectionCriteria:
    """Traduz o tom do roteiro em critérios de template."""
    emotion = (emotion or "").lower()

    if emotion in {"excited", "urgent", "angry", "empolgado", "urgente"}:
        energy, gestures = Energy.HIGH.value, Gestures.HIGH.value
    elif emotion in {"serious", "calm", "sério", "serio", "calmo", "sad"}:
        energy, gestures = Energy.LOW.value, Gestures.LOW.value
    elif emotion in {"confident", "confiante", "authoritative"}:
        energy, gestures = Energy.MEDIUM.value, Gestures.MEDIUM.value
    else:
        energy, gestures = Energy.MEDIUM.value, Gestures.MEDIUM.value

    style = style_hint or Style.TALKING_HEAD.value
    return SelectionCriteria(
        energy=energy,
        gestures=gestures,
        style=style,
        min_duration=audio_duration,
    )


# ---------------------------------------------------------------------------
# Composição por duração
# ---------------------------------------------------------------------------


@dataclass
class TemplateSegment:
    """Um trecho de template a ser usado na composição."""

    template_id: str
    source: Path
    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class TemplatePlan:
    """Como cobrir `target_duration` com os templates disponíveis."""

    segments: list[TemplateSegment]
    target_duration: float
    strategy: str
    warnings: list[str]

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self.segments)

    @property
    def needs_composition(self) -> bool:
        return len(self.segments) > 1


# Margem de corte nas pontas: evita começar/terminar num piscar ou num gesto
# interrompido, que é onde as emendas ficam óbvias.
_EDGE_TRIM = 0.35
# Trecho mínimo aproveitável.
_MIN_SEGMENT = 1.5


def plan_composition(
    target_duration: float,
    criteria: SelectionCriteria,
    templates: list[TemplateMetadata] | None = None,
) -> TemplatePlan:
    """Monta o plano de cobertura do áudio.

    Estratégias, em ordem de preferência:
      1. `single`   — um template cobre tudo (melhor resultado);
      2. `multi`    — encadeia templates diferentes, o que disfarça o corte
                      porque o enquadramento muda junto;
      3. `revisit`  — reaproveita trechos distintos do mesmo template quando
                      não há material suficiente.
    """
    pool = templates if templates is not None else load_templates()
    if not pool:
        raise TemplateError(
            "Nenhum template disponível. Adicione vídeos verticais seus em "
            "data/identity/templates/."
        )

    ranked = sorted(pool, key=lambda t: score_template(t, criteria), reverse=True)
    warnings: list[str] = []

    # 1. Cobertura por um único template.
    for metadata in ranked:
        usable = metadata.duration - 2 * _EDGE_TRIM
        if usable >= target_duration:
            # Centraliza o trecho: o miolo costuma ter a fala mais estável.
            start = _EDGE_TRIM + max(0.0, (usable - target_duration) / 2)
            return TemplatePlan(
                segments=[
                    TemplateSegment(
                        template_id=metadata.id,
                        source=template_path(metadata),
                        start=round(start, 3),
                        duration=round(target_duration, 3),
                    )
                ],
                target_duration=target_duration,
                strategy="single",
                warnings=warnings,
            )

    # 2. Encadeamento de templates distintos.
    segments: list[TemplateSegment] = []
    remaining = target_duration
    used: set[str] = set()

    for metadata in ranked:
        if remaining <= 0.05:
            break
        usable = metadata.duration - 2 * _EDGE_TRIM
        if usable < _MIN_SEGMENT:
            continue
        take = min(usable, remaining)
        segments.append(
            TemplateSegment(
                template_id=metadata.id,
                source=template_path(metadata),
                start=round(_EDGE_TRIM, 3),
                duration=round(take, 3),
            )
        )
        used.add(metadata.id)
        remaining -= take

    if remaining <= 0.05:
        return TemplatePlan(
            segments=segments,
            target_duration=target_duration,
            strategy="multi",
            warnings=warnings,
        )

    # 3. Revisitar trechos ainda não usados dos mesmos templates.
    warnings.append(
        f"O material disponível ({sum(t.duration for t in ranked):.1f}s) é menor "
        f"que o áudio ({target_duration:.1f}s). Trechos serão reaproveitados — "
        "grave mais templates para evitar repetição perceptível."
    )

    guard = 0
    while remaining > 0.05 and guard < 200:
        guard += 1
        progressed = False
        for metadata in ranked:
            if remaining <= 0.05:
                break
            usable = metadata.duration - 2 * _EDGE_TRIM
            if usable < _MIN_SEGMENT:
                continue
            already = sum(
                s.duration for s in segments if s.template_id == metadata.id
            )
            # Começa de um ponto diferente a cada revisita para não repetir
            # exatamente o mesmo gesto.
            offset = _EDGE_TRIM + (already % max(usable - _MIN_SEGMENT, 0.1))
            available = metadata.duration - _EDGE_TRIM - offset
            if available < _MIN_SEGMENT:
                offset = _EDGE_TRIM
                available = usable
            take = min(available, remaining)
            if take < 0.4:
                continue
            segments.append(
                TemplateSegment(
                    template_id=metadata.id,
                    source=template_path(metadata),
                    start=round(offset, 3),
                    duration=round(take, 3),
                )
            )
            remaining -= take
            progressed = True
        if not progressed:
            break

    if remaining > 0.05:
        warnings.append(
            f"Faltaram {remaining:.1f}s de cobertura; o último trecho será "
            "estendido pelo congelamento do frame final."
        )

    return TemplatePlan(
        segments=segments,
        target_duration=target_duration,
        strategy="revisit",
        warnings=warnings,
    )


def render_plan(plan: TemplatePlan, destination: Path, fps: int = 25) -> Path:
    """Materializa o plano num único vídeo silencioso, pronto para lip-sync.

    Normaliza fps e resolução antes de concatenar; o MuseTalk foi treinado a
    25 fps e degrada visivelmente fora disso.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not plan.segments:
        raise TemplateError("Plano de composição vazio.")

    work_dir = destination.parent / f"{destination.stem}_segments"
    work_dir.mkdir(parents=True, exist_ok=True)

    rendered: list[Path] = []
    for index, segment in enumerate(plan.segments):
        piece = work_dir / f"seg_{index:03d}.mp4"
        ffmpeg.run_ffmpeg(
            [
                "-ss", f"{segment.start:.3f}",
                "-t", f"{segment.duration:.3f}",
                "-i", str(segment.source),
                "-an",
                "-vf", f"fps={fps},setsar=1",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "16",
                "-pix_fmt", "yuv420p",
                str(piece),
            ]
        )
        rendered.append(piece)

    if len(rendered) == 1:
        rendered[0].replace(destination)
    else:
        ffmpeg.concat_videos(rendered, destination)

    ffmpeg.validate_video(destination, min_duration=0.2)
    return destination
