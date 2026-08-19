"""Registro de modelos + gestor de licenças.

Duas responsabilidades:

1. Saber o que existe, de onde vem, qual versão, quanto ocupa e se já está
   instalado no disco.
2. Responder se um modelo pode ser usado em `license_mode: commercial` —
   sem nunca *assumir* uma licença que não foi verificada na fonte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from core.storage.paths import get_paths

LicenseMode = Literal["personal", "commercial"]


class CommercialStatus(str, Enum):
    ALLOWED = "allowed"
    FORBIDDEN = "forbidden"
    NEEDS_REVIEW = "needs_review"

    @classmethod
    def from_raw(cls, raw: Any) -> "CommercialStatus":
        if raw is True:
            return cls.ALLOWED
        if raw is False:
            return cls.FORBIDDEN
        return cls.NEEDS_REVIEW


@dataclass(frozen=True)
class Requirements:
    min_ram_gb: float = 0
    min_vram_gb: float = 0
    recommended_vram_gb: float = 0
    python: str | None = None


@dataclass(frozen=True)
class DependencyLicense:
    name: str
    source: str = ""
    license: str = "desconhecida"
    commercial: CommercialStatus = CommercialStatus.NEEDS_REVIEW
    note: str = ""


@dataclass(frozen=True)
class Component:
    """Um repositório de pesos que compõe um modelo.

    Engines como o MuseTalk montam seus pesos a partir de vários repositórios
    distintos (unet, VAE, whisper, dwpose, face parsing). Declarar isso aqui
    evita espalhar URLs pelo código.
    """

    repo: str
    dest: str
    include: tuple[str, ...] = ()
    strip_prefix: str = ""
    size_gb: float = 0.0
    revision: str = "main"


@dataclass(frozen=True)
class UrlComponent:
    """Peso baixado por URL direta (fora do HuggingFace)."""

    url: str
    dest: str
    filename: str
    size_gb: float = 0.0
    license: str = "desconhecida"
    note: str = ""


@dataclass(frozen=True)
class ModelEntry:
    key: str
    display_name: str
    purpose: str
    engine: str
    repo: str
    source: str
    license: str
    commercial: CommercialStatus
    size_gb: float
    env: str
    revision: str = "main"
    code_revision: str = ""
    components: tuple[Component, ...] = ()
    url_components: tuple[UrlComponent, ...] = ()
    upstream_code: str = ""
    license_url: str = ""
    verified_at: str = ""
    package: str = ""
    optional: bool = False
    notes: str = ""
    requires: Requirements = field(default_factory=Requirements)
    dependencies: tuple[DependencyLicense, ...] = ()

    @property
    def install_dir(self) -> Path:
        return get_paths().models / self.key

    @property
    def installed(self) -> bool:
        marker = self.install_dir / ".installed.json"
        return marker.exists()

    def commercial_ok(self) -> bool:
        """True só quando o modelo E todas as dependências estão liberadas."""
        if self.commercial is not CommercialStatus.ALLOWED:
            return False
        return all(
            dep.commercial is CommercialStatus.ALLOWED
            for dep in self.dependencies
        )

    def commercial_blockers(self) -> list[str]:
        blockers: list[str] = []
        if self.commercial is CommercialStatus.FORBIDDEN:
            blockers.append(
                f"Licença do modelo ({self.license}) proíbe uso comercial."
            )
        elif self.commercial is CommercialStatus.NEEDS_REVIEW:
            blockers.append(
                f"Licença do modelo ({self.license}) exige verificação manual "
                "antes de uso comercial."
            )
        for dep in self.dependencies:
            if dep.commercial is CommercialStatus.FORBIDDEN:
                blockers.append(
                    f"Dependência '{dep.name}' ({dep.license}) proíbe uso comercial."
                )
            elif dep.commercial is CommercialStatus.NEEDS_REVIEW:
                detail = f" {dep.note.strip()}" if dep.note else ""
                blockers.append(
                    f"Dependência '{dep.name}' ({dep.license}) exige verificação "
                    f"manual.{detail}"
                )
        return blockers


@dataclass(frozen=True)
class HardwareVerdict:
    compatible: bool
    reasons: tuple[str, ...] = ()
    degraded: bool = False


class ModelRegistry:
    def __init__(self, entries: dict[str, ModelEntry]) -> None:
        self._entries = entries

    def __iter__(self):
        return iter(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> ModelEntry | None:
        return self._entries.get(key)

    def require(self, key: str) -> ModelEntry:
        entry = self._entries.get(key)
        if entry is None:
            known = ", ".join(sorted(self._entries))
            raise KeyError(f"Modelo '{key}' não está no registro. Conhecidos: {known}")
        return entry

    def by_purpose(self, purpose: str) -> list[ModelEntry]:
        return [e for e in self._entries.values() if e.purpose == purpose]

    def by_repo(self, repo: str) -> ModelEntry | None:
        for entry in self._entries.values():
            if entry.repo == repo:
                return entry
        return None

    def recommended_for(
        self, purpose: str, license_mode: LicenseMode = "personal"
    ) -> list[ModelEntry]:
        """Modelos aptos a aparecer como recomendados para o modo pedido."""
        candidates = self.by_purpose(purpose)
        if license_mode == "commercial":
            candidates = [e for e in candidates if e.commercial_ok()]
        return sorted(candidates, key=lambda e: e.size_gb)

    def check_hardware(self, entry: ModelEntry, hardware) -> HardwareVerdict:
        """Confronta as exigências do modelo com o hardware real."""
        reasons: list[str] = []
        degraded = False

        ram_gb = (hardware.ram_total_mb or 0) / 1024
        if entry.requires.min_ram_gb and ram_gb < entry.requires.min_ram_gb:
            reasons.append(
                f"Requer {entry.requires.min_ram_gb:.0f} GB de RAM; "
                f"a máquina tem {ram_gb:.1f} GB."
            )

        vram_gb = hardware.best_vram_mb / 1024
        if entry.requires.min_vram_gb and vram_gb < entry.requires.min_vram_gb:
            reasons.append(
                f"Requer {entry.requires.min_vram_gb:.0f} GB de VRAM; "
                f"nenhuma GPU compatível encontrada."
                if vram_gb <= 0
                else f"Requer {entry.requires.min_vram_gb:.0f} GB de VRAM; "
                f"disponível {vram_gb:.1f} GB."
            )
        elif entry.requires.recommended_vram_gb and vram_gb < entry.requires.recommended_vram_gb:
            degraded = True

        free_gb = hardware.disk_free_gb or 0
        if free_gb and free_gb < entry.size_gb * 1.3:
            reasons.append(
                f"Precisa de ~{entry.size_gb:.1f} GB em disco; "
                f"livres {free_gb:.0f} GB."
            )

        return HardwareVerdict(
            compatible=not reasons, reasons=tuple(reasons), degraded=degraded
        )


def _parse_entry(key: str, raw: dict[str, Any]) -> ModelEntry:
    requires_raw = raw.get("requires") or {}
    requires = Requirements(
        min_ram_gb=float(requires_raw.get("min_ram_gb", 0) or 0),
        min_vram_gb=float(requires_raw.get("min_vram_gb", 0) or 0),
        recommended_vram_gb=float(requires_raw.get("recommended_vram_gb", 0) or 0),
        python=requires_raw.get("python"),
    )
    dependencies = tuple(
        DependencyLicense(
            name=dep.get("name", "?"),
            source=dep.get("source", ""),
            license=dep.get("license", "desconhecida"),
            commercial=CommercialStatus.from_raw(dep.get("commercial_allowed")),
            note=dep.get("note", ""),
        )
        for dep in (raw.get("dependencies") or [])
    )
    components = tuple(
        Component(
            repo=comp["repo"],
            dest=comp.get("dest", ""),
            include=tuple(comp.get("include") or ()),
            strip_prefix=comp.get("strip_prefix", ""),
            size_gb=float(comp.get("size_gb", 0) or 0),
            revision=str(comp.get("revision", "main")),
        )
        for comp in (raw.get("components") or [])
    )
    url_components = tuple(
        UrlComponent(
            url=item["url"],
            dest=item.get("dest", ""),
            filename=item.get("filename", item["url"].rsplit("/", 1)[-1]),
            size_gb=float(item.get("size_gb", 0) or 0),
            license=item.get("license", "desconhecida"),
            note=(item.get("note") or "").strip(),
        )
        for item in (raw.get("url_components") or [])
    )
    return ModelEntry(
        key=key,
        display_name=raw.get("display_name", key),
        purpose=raw.get("purpose", "unknown"),
        engine=raw.get("engine", ""),
        repo=raw.get("repo", ""),
        source=raw.get("source", ""),
        code_revision=str(raw.get("code_revision", "")),
        components=components,
        url_components=url_components,
        upstream_code=raw.get("upstream_code", ""),
        license=raw.get("license", "desconhecida"),
        license_url=raw.get("license_url", ""),
        commercial=CommercialStatus.from_raw(raw.get("commercial_allowed")),
        verified_at=str(raw.get("verified_at", "")),
        size_gb=float(raw.get("size_gb", 0) or 0),
        env=raw.get("env", "backend"),
        revision=str(raw.get("revision", "main")),
        package=raw.get("package", ""),
        optional=bool(raw.get("optional", False)),
        notes=(raw.get("notes") or "").strip(),
        requires=requires,
        dependencies=dependencies,
    )


@lru_cache(maxsize=1)
def get_registry() -> ModelRegistry:
    path = get_paths().model_registry_file
    if not path.exists():
        raise FileNotFoundError(f"Registro de modelos não encontrado: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models = raw.get("models") or {}
    return ModelRegistry(
        {key: _parse_entry(key, value) for key, value in models.items()}
    )
