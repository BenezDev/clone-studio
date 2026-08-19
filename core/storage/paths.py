"""Resolução central de caminhos do Local Clone Studio.

Regra: nenhum módulo do projeto deve construir caminhos com strings soltas.
Tudo passa por aqui, para que o layout no disco possa mudar num único lugar.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ENV_ROOT = "CLONE_STUDIO_ROOT"

# Underscore inicial é permitido (`_smoke`, `_rascunho`): não é risco de
# travessia nem nome reservado. O ponto inicial continua barrado, porque
# cria arquivo oculto e abre caminho para `.` e `..`.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_path_component(value: str, *, label: str = "identificador") -> str:
    """Valida um único componente de caminho, de forma portátil.

    IDs chegam também pela API e nunca podem virar caminhos relativos, nomes
    reservados do Windows ou componentes especiais como ``..``.
    """
    if not isinstance(value, str) or not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(
            f"{label} inválido: use apenas letras, números, ponto, hífen e "
            "underscore (máximo de 128 caracteres)."
        )
    normalized = value.rstrip(". ")
    windows_stem = normalized.partition(".")[0].lower()
    if (
        value in {".", ".."}
        or normalized != value
        or windows_stem in _WINDOWS_RESERVED
    ):
        raise ValueError(f"{label} inválido ou reservado pelo sistema: {value!r}.")
    return value


def _detect_root() -> Path:
    """Descobre a raiz do projeto.

    Prioridade: variável de ambiente > marcador no disco > 3 níveis acima deste
    arquivo (core/storage/paths.py -> raiz).
    """
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env).expanduser().resolve()

    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "config" / "default.yaml").exists():
            return candidate
    return here.parents[2]


@dataclass(frozen=True)
class Paths:
    """Todos os diretórios conhecidos da aplicação."""

    root: Path

    # --- código / configuração ---
    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def default_config_file(self) -> Path:
        return self.config / "default.yaml"

    @property
    def user_config_file(self) -> Path:
        return self.config / "local.yaml"

    @property
    def model_registry_file(self) -> Path:
        return self.config / "model_registry.yaml"

    @property
    def hardware_profile_file(self) -> Path:
        return self.config / "hardware.json"

    # --- ambientes python isolados ---
    @property
    def envs(self) -> Path:
        return self.root / ".envs"

    def env(self, name: str) -> Path:
        return self.envs / name

    def env_python(self, name: str) -> Path:
        environment = self.envs / name
        if os.name == "nt":
            return environment / "Scripts" / "python.exe"
        return environment / "bin" / "python"

    # --- dados do usuário (nunca versionados) ---
    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def identity(self) -> Path:
        return self.data / "identity"

    @property
    def voice_dir(self) -> Path:
        return self.identity / "voice"

    @property
    def photos_dir(self) -> Path:
        return self.identity / "photos"

    @property
    def videos_dir(self) -> Path:
        return self.identity / "videos"

    @property
    def templates_dir(self) -> Path:
        return self.identity / "templates"

    @property
    def identity_metadata(self) -> Path:
        return self.identity / "metadata"

    @property
    def assets(self) -> Path:
        return self.data / "assets"

    @property
    def broll_dir(self) -> Path:
        return self.assets / "broll"

    # --- artefatos gerados ---
    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def template_cache(self) -> Path:
        return self.cache / "templates"

    @property
    def voice_cache(self) -> Path:
        return self.cache / "voice"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def runtime(self) -> Path:
        """PIDs, sockets e estado efêmero do process manager."""
        return self.root / ".runtime"

    @property
    def jobs_db(self) -> Path:
        return self.root / "cache" / "jobs.sqlite3"

    @property
    def external(self) -> Path:
        return self.root / "external"

    @property
    def workflows(self) -> Path:
        return self.root / "workflows"

    def project(self, project_id: str) -> Path:
        return self.projects / safe_path_component(project_id, label="ID do projeto")

    def ensure_runtime_dirs(self) -> None:
        """Cria os diretórios que a aplicação escreve em tempo de execução."""
        for path in (
            self.voice_dir,
            self.photos_dir,
            self.videos_dir,
            self.templates_dir,
            self.identity_metadata,
            self.broll_dir,
            self.models,
            self.cache,
            self.template_cache,
            self.voice_cache,
            self.projects,
            self.exports,
            self.logs,
            self.runtime,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_paths() -> Paths:
    return Paths(root=_detect_root())
