"""Carregamento e merge da configuração.

`config/default.yaml` (versionado) + `config/local.yaml` (do usuário, ignorado
pelo Git). O merge é recursivo; escalares e listas são substituídos, dicts são
mesclados.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from core.config.schema import Settings
from core.hardware.detect import HardwareReport, detect, load_report
from core.storage.paths import get_paths


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} deve conter um mapeamento YAML no topo.")
    return data


def load_settings(reload: bool = False) -> Settings:
    if reload:
        _cached_settings.cache_clear()
    return _cached_settings()


@lru_cache(maxsize=1)
def _cached_settings() -> Settings:
    paths = get_paths()
    merged = deep_merge(
        _read_yaml(paths.default_config_file),
        _read_yaml(paths.user_config_file),
    )
    return Settings.model_validate(merged)


def save_user_overrides(overrides: dict[str, Any]) -> Settings:
    """Escreve/mescla `config/local.yaml` e revalida a configuração inteira."""
    paths = get_paths()
    current = _read_yaml(paths.user_config_file)
    updated = deep_merge(current, overrides)

    # Valida ANTES de gravar: nunca deixar o disco num estado inválido.
    candidate = deep_merge(_read_yaml(paths.default_config_file), updated)
    Settings.model_validate(candidate)

    paths.config.mkdir(parents=True, exist_ok=True)
    paths.user_config_file.write_text(
        yaml.safe_dump(updated, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return load_settings(reload=True)


@lru_cache(maxsize=1)
def get_hardware() -> HardwareReport:
    """Relatório de hardware, lido do cache em disco quando disponível."""
    paths = get_paths()
    cached = load_report(paths.hardware_profile_file)
    if cached is not None:
        return cached
    return detect(disk_target=paths.root)


def refresh_hardware() -> HardwareReport:
    from core.hardware.detect import save_report

    paths = get_paths()
    report = detect(disk_target=paths.root)
    save_report(report, paths.hardware_profile_file)
    get_hardware.cache_clear()
    return report


def active_profile() -> str:
    """Perfil de hardware efetivo (config sobrepõe detecção)."""
    settings = load_settings()
    if settings.hardware.profile != "auto":
        return settings.hardware.profile
    return get_hardware().profile
