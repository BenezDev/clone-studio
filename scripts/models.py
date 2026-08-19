"""Gerenciador de pesos de modelos.

    ./scripts/models.sh list
    ./scripts/models.sh status
    ./scripts/models.sh install qwen3_tts_0_6b_base
    ./scripts/models.sh install --essential
    ./scripts/models.sh remove wan22_ti2v_5b

Regras:
  * nada é baixado sem comando explícito;
  * o tamanho aproximado é mostrado ANTES do download;
  * modelos incompatíveis com o hardware exigem --force;
  * a licença aparece na listagem, sempre.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.loader import active_profile, get_hardware  # noqa: E402
from core.licensing.registry import (  # noqa: E402
    CommercialStatus,
    ModelEntry,
    get_registry,
)
from core.storage.paths import get_paths  # noqa: E402

# Conjunto mínimo para o MVP: texto -> voz -> lip-sync -> legenda -> MP4.
ESSENTIAL_BY_PROFILE = {
    "CPU_ONLY": ["qwen3_tts_0_6b_base", "faster_whisper_small"],
    "LOW_VRAM": ["qwen3_tts_0_6b_base", "faster_whisper_small"],
    "MID_VRAM": ["qwen3_tts_1_7b_base", "faster_whisper_large_v3"],
    "HIGH_VRAM": ["qwen3_tts_1_7b_base", "faster_whisper_large_v3"],
    "EXTREME": ["qwen3_tts_1_7b_base", "faster_whisper_large_v3"],
}

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def _color(text: str, code: str) -> str:
    return text if not sys.stdout.isatty() else f"{code}{text}{RESET}"


def _commercial_label(entry: ModelEntry) -> str:
    if entry.commercial is CommercialStatus.ALLOWED:
        if entry.commercial_ok():
            return _color("comercial ok", GREEN)
        return _color("comercial: verificar deps", YELLOW)
    if entry.commercial is CommercialStatus.FORBIDDEN:
        return _color("não comercial", RED)
    return _color("verificar licença", YELLOW)


def _marker_path(entry: ModelEntry) -> Path:
    return entry.install_dir / ".installed.json"


def cmd_list(args: argparse.Namespace) -> int:
    registry = get_registry()
    hardware = get_hardware()
    profile = active_profile()

    print(f"\n{BOLD}Catálogo de modelos{RESET}  {DIM}(perfil: {profile}){RESET}\n")

    by_purpose: dict[str, list[ModelEntry]] = {}
    for entry in registry:
        by_purpose.setdefault(entry.purpose, []).append(entry)

    essentials = set(ESSENTIAL_BY_PROFILE.get(profile, []))

    for purpose in sorted(by_purpose):
        print(f"{BOLD}{purpose}{RESET}")
        for entry in sorted(by_purpose[purpose], key=lambda e: e.size_gb):
            installed = _marker_path(entry).exists()
            state = _color("instalado", GREEN) if installed else _color("ausente", DIM)
            verdict = registry.check_hardware(entry, hardware)
            compat = "" if verdict.compatible else _color(" incompatível", RED)
            if verdict.compatible and verdict.degraded:
                compat = _color(" (lento neste hardware)", YELLOW)
            tag = _color(" [essencial]", YELLOW) if entry.key in essentials else ""

            print(f"  {entry.key}{tag}")
            print(
                f"    {entry.display_name}  ·  {entry.size_gb:.1f} GB  ·  "
                f"{entry.license}  ·  {_commercial_label(entry)}"
            )
            print(f"    estado: {state}{compat}")
            if args.verbose:
                print(f"    repo:   {entry.repo}")
                print(f"    fonte:  {entry.source}")
                print(f"    local:  {entry.install_dir}")
                print(f"    env:    {entry.env}")
                if entry.notes:
                    print(f"    nota:   {entry.notes.splitlines()[0]}")
            for reason in verdict.reasons:
                print(f"    {_color('!', RED)} {reason}")
        print()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    registry = get_registry()
    paths = get_paths()
    total = 0
    print(f"\n{BOLD}Modelos instalados{RESET}  {DIM}({paths.models}){RESET}\n")
    any_installed = False
    for entry in registry:
        marker = _marker_path(entry)
        if not marker.exists():
            continue
        any_installed = True
        meta = json.loads(marker.read_text(encoding="utf-8"))
        size = sum(
            f.stat().st_size for f in entry.install_dir.rglob("*") if f.is_file()
        )
        total += size
        print(f"  {entry.key}")
        print(f"    {entry.display_name}")
        print(f"    {size / 1e9:.2f} GB  ·  licença {entry.license}")
        print(f"    revisão {meta.get('revision', '?')}  ·  em {meta.get('installed_at', '?')}")
        print(f"    {entry.install_dir}")
        print()
    if not any_installed:
        print("  nenhum modelo instalado ainda.\n")
        print(f"  Comece com: {BOLD}./scripts/models.sh install --essential{RESET}\n")
        return 0
    print(f"  {BOLD}total: {total / 1e9:.2f} GB{RESET}\n")
    return 0


def _clone_code(entry: ModelEntry) -> bool:
    """Clona o repositório de código da engine em external/, no commit fixado."""
    import subprocess

    if not entry.upstream_code or not entry.code_revision:
        return True

    target = get_paths().external / entry.engine
    target.parent.mkdir(parents=True, exist_ok=True)

    if (target / ".git").exists():
        print(f"  código: {target.name} já clonado — atualizando para o commit fixado")
        commands = [
            ["git", "-C", str(target), "fetch", "--depth", "50", "origin"],
            ["git", "-C", str(target), "checkout", "--force", entry.code_revision],
        ]
    else:
        print(f"  código: clonando {entry.upstream_code}")
        commands = [
            ["git", "clone", "--filter=blob:none", entry.upstream_code, str(target)],
            ["git", "-C", str(target), "checkout", "--force", entry.code_revision],
        ]

    for command in commands:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            print(f"  {_color('✗', RED)} {' '.join(command)}")
            print(f"     {proc.stderr.strip().splitlines()[-1] if proc.stderr else ''}")
            return False

    print(f"  {_color('✓', GREEN)} código em {target} @ {entry.code_revision[:8]}")
    return True


def _download_components(entry: ModelEntry, force: bool) -> bool:
    """Baixa um modelo composto por vários repositórios."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import (
        GatedRepoError,
        LocalEntryNotFoundError,
        RepositoryNotFoundError,
    )

    destination = entry.install_dir
    for component in entry.components:
        target = destination / component.dest if component.dest else destination
        target.mkdir(parents=True, exist_ok=True)
        print(
            f"  · {component.repo} → {component.dest or '.'} "
            f"(~{component.size_gb:.2f} GB)"
        )
        try:
            snapshot_download(
                repo_id=component.repo,
                revision=component.revision,
                local_dir=str(target),
                allow_patterns=list(component.include) or None,
                max_workers=4,
            )
        except GatedRepoError:
            print(
                f"  {_color('✗', RED)} {component.repo} exige aceitar termos no "
                "site do HuggingFace."
            )
            return False
        except RepositoryNotFoundError:
            print(f"  {_color('✗', RED)} repositório não encontrado: {component.repo}")
            return False
        except (LocalEntryNotFoundError, OSError) as exc:
            print(f"  {_color('✗', RED)} falha em {component.repo}: {exc}")
            return False

        # `allow_patterns` com caminho preserva a hierarquia do repo remoto;
        # o MuseTalk espera os arquivos direto na pasta de destino.
        if component.strip_prefix:
            nested = target / component.strip_prefix
            if nested.is_dir():
                for item in nested.iterdir():
                    dest_item = target / item.name
                    if dest_item.is_dir():
                        shutil.copytree(item, dest_item, dirs_exist_ok=True)
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        dest_item.unlink(missing_ok=True)
                        shutil.move(str(item), str(dest_item))
                shutil.rmtree(nested, ignore_errors=True)

    return _download_urls(entry, force)


def _download_urls(entry: ModelEntry, force: bool) -> bool:
    """Baixa pesos servidos por URL direta.

    Pré-baixá-los é o que permite ao pipeline funcionar offline: sem isso, o
    MuseTalk buscaria o detector S3FD na internet no primeiro lip-sync.
    """
    import urllib.error
    import urllib.request

    for item in entry.url_components:
        target_dir = entry.install_dir / item.dest if item.dest else entry.install_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / item.filename

        if target.exists() and not force:
            print(f"  · {item.filename}: já presente")
            continue

        print(f"  · {item.filename} ← {item.url} (~{item.size_gb * 1000:.0f} MB)")
        partial = target.with_suffix(target.suffix + ".part")
        try:
            with urllib.request.urlopen(item.url, timeout=60) as response:
                total = int(response.headers.get("Content-Length", 0))
                downloaded = 0
                with partial.open("wb") as handle:
                    while True:
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if total and sys.stdout.isatty():
                            pct = downloaded / total * 100
                            print(f"\r    {pct:5.1f}%", end="", flush=True)
            if sys.stdout.isatty():
                print()
            partial.replace(target)
        except (urllib.error.URLError, OSError) as exc:
            partial.unlink(missing_ok=True)
            print(f"  {_color('✗', RED)} falha ao baixar {item.filename}: {exc}")
            return False

    return True


def _download(entry: ModelEntry, force: bool) -> bool:
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import (
        GatedRepoError,
        LocalEntryNotFoundError,
        RepositoryNotFoundError,
    )

    destination = entry.install_dir
    marker = _marker_path(entry)

    if marker.exists() and not force:
        print(f"  {entry.key}: já instalado (use --force para refazer)")
        return True

    print(f"\n{BOLD}Baixando {entry.display_name}{RESET}")
    print(f"  repositório : {entry.repo}")
    print(f"  revisão     : {entry.revision}")
    print(f"  tamanho     : ~{entry.size_gb:.1f} GB")
    print(f"  licença     : {entry.license} ({_commercial_label(entry)})")
    print(f"  destino     : {destination}")
    if entry.dependencies:
        print("  dependências com licença própria:")
        for dep in entry.dependencies:
            print(f"    - {dep.name}: {dep.license}")
    print()

    destination.mkdir(parents=True, exist_ok=True)

    if not _clone_code(entry):
        return False

    if entry.components:
        if not _download_components(entry, force):
            return False
    else:
        try:
            snapshot_download(
                repo_id=entry.repo,
                revision=entry.revision,
                local_dir=str(destination),
                max_workers=4,
            )
        except GatedRepoError:
            print(
                f"  {_color('✗', RED)} O repositório {entry.repo} exige aceitar os "
                "termos no site do HuggingFace e um token de acesso."
            )
            return False
        except RepositoryNotFoundError:
            print(f"  {_color('✗', RED)} Repositório não encontrado: {entry.repo}")
            return False
        except (LocalEntryNotFoundError, OSError) as exc:
            print(f"  {_color('✗', RED)} Falha de rede ou disco: {exc}")
            return False

    marker.write_text(
        json.dumps(
            {
                "key": entry.key,
                "repo": entry.repo,
                "revision": entry.revision,
                "code_revision": entry.code_revision,
                "components": [
                    {"repo": c.repo, "dest": c.dest} for c in entry.components
                ],
                "license": entry.license,
                "source": entry.source,
                "installed_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    actual = sum(f.stat().st_size for f in destination.rglob("*") if f.is_file())
    print(f"  {_color('✓', GREEN)} {entry.key} instalado ({actual / 1e9:.2f} GB)")
    return True


def cmd_install(args: argparse.Namespace) -> int:
    registry = get_registry()
    hardware = get_hardware()
    profile = active_profile()

    if args.essential:
        keys = ESSENTIAL_BY_PROFILE.get(profile, ESSENTIAL_BY_PROFILE["CPU_ONLY"])
        print(f"Conjunto essencial para o perfil {BOLD}{profile}{RESET}: {', '.join(keys)}")
    else:
        keys = args.models
    if not keys:
        print("Nada a instalar. Use --essential ou informe um ou mais IDs.")
        return 1

    total_gb = 0.0
    resolved: list[ModelEntry] = []
    for key in keys:
        entry = registry.get(key)
        if entry is None:
            print(f"{_color('✗', RED)} modelo desconhecido: {key}")
            print(f"  disponíveis: {', '.join(sorted(e.key for e in registry))}")
            return 1
        verdict = registry.check_hardware(entry, hardware)
        if not verdict.compatible and not args.force:
            print(f"{_color('✗', RED)} {key} é incompatível com este hardware:")
            for reason in verdict.reasons:
                print(f"    - {reason}")
            print("  Use --force se quiser baixar mesmo assim.")
            return 1
        resolved.append(entry)
        if not _marker_path(entry).exists() or args.force:
            total_gb += entry.size_gb

    free_gb = hardware.disk_free_gb or 0
    print(f"\nTotal a baixar: ~{total_gb:.1f} GB  ·  livre em disco: {free_gb:.0f} GB")
    if free_gb and total_gb > free_gb * 0.9:
        print(f"{_color('✗', RED)} Espaço em disco insuficiente.")
        return 1

    if not args.yes and sys.stdin.isatty():
        answer = input("Continuar? [s/N] ").strip().lower()
        if answer not in {"s", "y", "sim"}:
            print("Cancelado.")
            return 1

    failures = 0
    for entry in resolved:
        if not _download(entry, args.force):
            failures += 1
    print()
    return 1 if failures else 0


def cmd_remove(args: argparse.Namespace) -> int:
    registry = get_registry()
    for key in args.models:
        entry = registry.get(key)
        if entry is None:
            print(f"{_color('✗', RED)} modelo desconhecido: {key}")
            return 1
        if not entry.install_dir.exists():
            print(f"  {key}: não estava instalado")
            continue
        size = sum(f.stat().st_size for f in entry.install_dir.rglob("*") if f.is_file())
        if not args.yes and sys.stdin.isatty():
            answer = input(
                f"Remover {key} ({size / 1e9:.2f} GB) de {entry.install_dir}? [s/N] "
            ).strip().lower()
            if answer not in {"s", "y", "sim"}:
                print("  cancelado")
                continue
        shutil.rmtree(entry.install_dir, ignore_errors=True)
        print(f"  {_color('✓', GREEN)} {key} removido")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="models", description="Gerenciador de pesos do Local Clone Studio"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="mostra o catálogo completo")
    p_list.add_argument("-v", "--verbose", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="mostra o que já está instalado")
    p_status.set_defaults(func=cmd_status)

    p_install = sub.add_parser("install", help="baixa pesos")
    p_install.add_argument("models", nargs="*")
    p_install.add_argument("--essential", action="store_true",
                           help="conjunto mínimo para o MVP neste hardware")
    p_install.add_argument("--force", action="store_true",
                           help="rebaixa e ignora incompatibilidade de hardware")
    p_install.add_argument("-y", "--yes", action="store_true")
    p_install.set_defaults(func=cmd_install)

    p_remove = sub.add_parser("remove", help="apaga pesos do disco")
    p_remove.add_argument("models", nargs="+")
    p_remove.add_argument("-y", "--yes", action="store_true")
    p_remove.set_defaults(func=cmd_remove)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
