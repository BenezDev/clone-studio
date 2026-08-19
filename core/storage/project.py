"""Formato de projeto.

Cada vídeo é um projeto reproduzível: `project.json` guarda tudo que é
necessário para recriar o resultado — roteiro, perfil de voz, template,
presets, modelos e versões usadas.

    projects/2026-08-11-programadores-vendas/
      project.json
      script.json
      voice.wav
      captions.ass
      captions.srt
      edit.json
      renders/
      cache/
      stages/          <- saídas intermediárias validadas (permitem resume)
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.storage.paths import get_paths, safe_path_component

PROJECT_FILE = "project.json"
FORMAT_VERSION = 1


def slugify(text: str, max_length: int = 48) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text.lower()).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "video"


@dataclass
class VoiceSettings:
    profile_id: str = "me"
    emotion: str = "confident"
    speed: float = 1.0
    seed: int | None = None
    variant: str = "A"


@dataclass
class TemplateSettings:
    mode: str = "auto"           # auto | manual
    template_id: str | None = None
    plan: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaptionSettings:
    preset: str = "hormozi"
    uppercase: bool | None = None
    words_per_cue: int | None = None
    enabled: bool = True


@dataclass
class EditSettings:
    preset: str = "clean"
    auto_cut: bool = False
    graphics: bool = False


@dataclass
class RenderSettings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    codec: str = "libx264"
    audio_sample_rate: int = 48000
    crf: int = 18
    preset: str = "medium"
    loudness_lufs: float = -14.0
    loudness_true_peak: float = -1.5


@dataclass
class StageRecord:
    """Registro de uma etapa concluída — a base do resume."""

    name: str
    status: str                       # completed | failed
    output: str = ""
    input_hash: str = ""
    duration_seconds: float = 0.0
    finished_at: str = ""
    error: str = ""


@dataclass
class Project:
    id: str
    title: str
    idea: str = ""
    created_at: str = ""
    updated_at: str = ""
    format_version: int = FORMAT_VERSION

    voice: VoiceSettings = field(default_factory=VoiceSettings)
    template: TemplateSettings = field(default_factory=TemplateSettings)
    captions: CaptionSettings = field(default_factory=CaptionSettings)
    editing: EditSettings = field(default_factory=EditSettings)
    render: RenderSettings = field(default_factory=RenderSettings)

    # Rastreabilidade: qual engine/modelo produziu este vídeo.
    engines: dict[str, str] = field(default_factory=dict)
    stages: dict[str, StageRecord] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)

    # -- caminhos --------------------------------------------------------

    @property
    def directory(self) -> Path:
        return get_paths().project(self.id)

    @property
    def script_file(self) -> Path:
        return self.directory / "script.json"

    @property
    def voice_file(self) -> Path:
        return self.directory / "voice.wav"

    @property
    def captions_ass(self) -> Path:
        return self.directory / "captions.ass"

    @property
    def captions_srt(self) -> Path:
        return self.directory / "captions.srt"

    @property
    def edit_file(self) -> Path:
        return self.directory / "edit.json"

    @property
    def graphics_ass(self) -> Path:
        return self.directory / "graphics.ass"

    @property
    def renders_dir(self) -> Path:
        return self.directory / "renders"

    @property
    def cache_dir(self) -> Path:
        return self.directory / "cache"

    @property
    def stages_dir(self) -> Path:
        return self.directory / "stages"

    @property
    def logs_dir(self) -> Path:
        return self.directory / "logs"

    def ensure_dirs(self) -> None:
        for path in (
            self.directory,
            self.renders_dir,
            self.cache_dir,
            self.stages_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    # -- persistência ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "idea": self.idea,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "format_version": self.format_version,
            "voice": asdict(self.voice),
            "template": asdict(self.template),
            "captions": asdict(self.captions),
            "editing": asdict(self.editing),
            "render": asdict(self.render),
            "engines": self.engines,
            "stages": {k: asdict(v) for k, v in self.stages.items()},
            "outputs": self.outputs,
        }

    def save(self) -> Path:
        self.ensure_dirs()
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target = self.directory / PROJECT_FILE
        target.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return target

    @classmethod
    def load(cls, project_id: str) -> "Project":
        project_id = safe_path_component(project_id, label="ID do projeto")
        directory = get_paths().project(project_id)
        target = directory / PROJECT_FILE
        if not target.exists():
            raise FileNotFoundError(f"Projeto não encontrado: {target}")
        raw = json.loads(target.read_text(encoding="utf-8"))

        version = raw.get("format_version", 1)
        if version > FORMAT_VERSION:
            raise ValueError(
                f"O projeto '{project_id}' foi criado por uma versão mais nova "
                f"do Clone Studio (formato {version} > {FORMAT_VERSION})."
            )

        stored_id = safe_path_component(raw["id"], label="ID armazenado do projeto")
        if stored_id != project_id:
            raise ValueError(
                f"O ID armazenado ({stored_id!r}) diverge do diretório ({project_id!r})."
            )

        return cls(
            id=stored_id,
            title=raw.get("title", raw["id"]),
            idea=raw.get("idea", ""),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            format_version=version,
            voice=VoiceSettings(**raw.get("voice", {})),
            template=TemplateSettings(**raw.get("template", {})),
            captions=CaptionSettings(**raw.get("captions", {})),
            editing=EditSettings(**raw.get("editing", {})),
            render=RenderSettings(**raw.get("render", {})),
            engines=raw.get("engines", {}),
            stages={
                k: StageRecord(**v) for k, v in (raw.get("stages") or {}).items()
            },
            outputs=raw.get("outputs", {}),
        )

    # -- resume ----------------------------------------------------------

    def stage_completed(self, name: str, expected_hash: str = "") -> bool:
        """Uma etapa só conta como concluída se o arquivo dela ainda existe.

        É isso que evita o caso clássico de "retomar" um render cujo arquivo
        intermediário foi apagado.
        """
        record = self.stages.get(name)
        if record is None or record.status != "completed":
            return False
        if expected_hash and record.input_hash and record.input_hash != expected_hash:
            return False
        if record.output:
            output = Path(record.output)
            if not output.is_absolute():
                output = self.directory / output
            if not output.exists():
                return False
        return True

    def record_stage(
        self,
        name: str,
        *,
        output: Path | str = "",
        input_hash: str = "",
        duration_seconds: float = 0.0,
        status: str = "completed",
        error: str = "",
    ) -> None:
        path = Path(output) if output else None
        relative = ""
        if path is not None:
            try:
                relative = str(path.relative_to(self.directory))
            except ValueError:
                relative = str(path)
        self.stages[name] = StageRecord(
            name=name,
            status=status,
            output=relative,
            input_hash=input_hash,
            duration_seconds=round(duration_seconds, 2),
            finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            error=error,
        )

    def stage_output(self, name: str) -> Path | None:
        record = self.stages.get(name)
        if record is None or not record.output:
            return None
        path = Path(record.output)
        return path if path.is_absolute() else self.directory / path

    def invalidate_from(self, stage_name: str, order: list[str]) -> None:
        """Invalida uma etapa e todas as posteriores."""
        if stage_name not in order:
            return
        for name in order[order.index(stage_name):]:
            self.stages.pop(name, None)
            if name == "render":
                self.stages.pop("render_preview", None)
                self.stages.pop("render_final", None)


def create_project(
    title: str,
    idea: str = "",
    *,
    project_id: str | None = None,
) -> Project:
    """Cria um projeto novo com id datado e legível."""
    slug = slugify(title or idea or "video")
    identifier = project_id or f"{date.today().isoformat()}-{slug}"

    paths = get_paths()
    directory = paths.project(identifier)
    if directory.exists():
        suffix = 2
        while paths.project(f"{identifier}-{suffix}").exists():
            suffix += 1
        identifier = f"{identifier}-{suffix}"

    # O projeto guarda um snapshot completo do render. Assim uma alteração
    # posterior em config/local.yaml não muda silenciosamente sua reprodução.
    from core.config.loader import load_settings

    video = load_settings().video
    project = Project(
        id=identifier,
        title=title or idea or identifier,
        idea=idea,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        render=RenderSettings(
            width=video.width,
            height=video.height,
            fps=video.fps,
            codec=video.video_codec,
            audio_sample_rate=video.audio_sample_rate,
            crf=video.crf,
            preset=video.preset,
            loudness_lufs=video.loudness_lufs,
            loudness_true_peak=video.loudness_true_peak,
        ),
    )
    project.save()
    return project


def list_projects() -> list[Project]:
    paths = get_paths()
    if not paths.projects.exists():
        return []
    projects: list[Project] = []
    for directory in sorted(paths.projects.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        try:
            projects.append(Project.load(directory.name))
        except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return projects
