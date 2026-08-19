"""Diagnóstico do sistema.

Camada de domínio única: a página `Diagnostics` da interface e o comando
`clone-studio doctor` chamam exatamente estas funções. Nada de lógica
duplicada entre CLI e API.
"""

from __future__ import annotations

import os
import shutil
import socket
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from core import platform_hints as hints
from core.config.loader import active_profile, get_hardware, load_settings
from core.licensing.registry import get_registry
from core.storage.paths import get_paths
from core.worker.runner import engine_available


class Level(str, Enum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"
    INFO = "info"


# Delegam para `core.platform_hints`, que é a fonte única dos comandos
# sugeridos ao usuário. Duplicar essa lógica foi o que deixou o `models.ps1`
# mandando um usuário de Windows rodar `./scripts/models.sh`.
def _install_hint(name: str, *, musetalk: bool = False) -> str:
    return hints.install(name, musetalk=musetalk)


def _model_hint(model: str) -> str:
    return hints.models("install", model)


@dataclass
class Check:
    name: str
    level: Level
    summary: str
    detail: str = ""
    hint: str = ""
    group: str = "geral"
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["level"] = self.level.value
        return payload


@dataclass
class DiagnosticsReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.ERROR]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.WARN]

    @property
    def healthy(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Grupos de verificação
# ---------------------------------------------------------------------------


def check_system(report: DiagnosticsReport) -> None:
    hardware = get_hardware()
    profile = active_profile()

    report.add(
        Check(
            name="Sistema",
            level=Level.INFO,
            summary=f"{hardware.os_name} · {hardware.arch}",
            detail=f"kernel {hardware.kernel} · Python {hardware.python_version}",
            group="hardware",
        )
    )
    report.add(
        Check(
            name="CPU",
            level=Level.OK if hardware.cpu_flags_avx2 else Level.WARN,
            summary=hardware.cpu_model,
            detail=(
                f"{hardware.cpu_cores_physical} núcleos / "
                f"{hardware.cpu_threads} threads · "
                f"AVX2={hardware.cpu_flags_avx2} AVX512={hardware.cpu_flags_avx512}"
            ),
            hint="" if hardware.cpu_flags_avx2 else
            "Sem AVX2 a inferência em CPU fica muito lenta.",
            group="hardware",
        )
    )

    ram_gb = (hardware.ram_total_mb or 0) / 1024
    report.add(
        Check(
            name="Memória",
            level=Level.OK if ram_gb >= 12 else Level.WARN,
            summary=f"{ram_gb:.1f} GB de RAM",
            detail=(
                f"{(hardware.ram_available_mb or 0) / 1024:.1f} GB disponíveis · "
                f"swap {(hardware.swap_total_mb or 0) / 1024:.1f} GB"
            ),
            hint="" if ram_gb >= 12 else
            "Com menos de 12 GB use o perfil de TTS 0.6B e mantenha swap ativo.",
            group="hardware",
        )
    )

    if hardware.gpus:
        for gpu in hardware.gpus:
            usable = gpu.usable_for_ml
            report.add(
                Check(
                    name=f"GPU ({gpu.vendor})",
                    level=Level.OK if usable else Level.INFO,
                    summary=gpu.name,
                    detail=(
                        f"VRAM {gpu.vram_mb} MB · {gpu.compute or 'sem runtime'}"
                        if gpu.vram_mb
                        else (gpu.compute or "sem runtime de compute")
                    ),
                    hint=gpu.notes,
                    group="hardware",
                    data={"vram_mb": gpu.vram_mb, "vendor": gpu.vendor},
                )
            )
    else:
        report.add(
            Check(
                name="GPU",
                level=Level.INFO,
                summary="nenhuma GPU detectada",
                group="hardware",
            )
        )

    report.add(
        Check(
            name="CUDA",
            level=Level.OK if hardware.cuda_available else Level.INFO,
            summary=(
                f"CUDA {hardware.cuda_version}"
                if hardware.cuda_available
                else "indisponível"
            ),
            group="hardware",
        )
    )

    report.add(
        Check(
            name="Perfil de hardware",
            level=Level.INFO,
            summary=profile,
            detail=hardware.profile_reason,
            group="hardware",
        )
    )

    free = hardware.disk_free_gb or 0
    report.add(
        Check(
            name="Disco",
            level=Level.OK if free >= 30 else Level.WARN,
            summary=f"{free:.0f} GB livres",
            detail=f"de {hardware.disk_total_gb or 0:.0f} GB",
            hint="" if free >= 30 else "Os pesos do MVP ocupam ~8 GB.",
            group="hardware",
        )
    )


def check_tools(report: DiagnosticsReport) -> None:
    required = {
        "ffmpeg": "obrigatório para qualquer render",
        "ffprobe": "obrigatório para validar arquivos",
    }
    optional = {
        "node": "necessário para a interface web",
        "pnpm": "gerenciador de pacotes do frontend",
        "git": "usado para clonar engines externas",
        "uv": "necessário apenas para o ambiente do MuseTalk",
        "ollama": "opcional — geração de roteiros",
        "docker": "opcional",
    }

    for name, why in required.items():
        path = shutil.which(name)
        report.add(
            Check(
                name=name,
                level=Level.OK if path else Level.ERROR,
                summary=path or "não encontrado",
                detail=why,
                hint="" if path else (
                    "winget install Gyan.FFmpeg"
                    if os.name == "nt"
                    else "sudo apt install -y ffmpeg"
                ),
                group="ferramentas",
            )
        )

    for name, why in optional.items():
        path = shutil.which(name)
        report.add(
            Check(
                name=name,
                level=Level.OK if path else Level.INFO,
                summary=path or "não encontrado",
                detail=why,
                group="ferramentas",
            )
        )


ENV_PURPOSE = {
    "backend": ("API e orquestração", True),
    "qwen-tts": ("clonagem de voz", True),
    "whisper": ("legendas", True),
    "musetalk": ("lip-sync", False),
    "comfyui": ("B-roll generativo (opcional)", False),
}


def check_environments(report: DiagnosticsReport) -> None:
    paths = get_paths()
    for name, (purpose, required) in ENV_PURPOSE.items():
        python = paths.env_python(name)
        exists = python.exists()
        if exists:
            level = Level.OK
            summary = "instalado"
        elif required:
            level = Level.ERROR
            summary = "ausente"
        else:
            level = Level.INFO
            summary = "não instalado (opcional)"

        hint = ""
        if not exists and required:
            hint = _install_hint(name)
        elif not exists and name == "musetalk":
            hint = _install_hint(name, musetalk=True)

        report.add(
            Check(
                name=f"env {name}",
                level=level,
                summary=summary,
                detail=f"{purpose} · {python}",
                hint=hint,
                group="ambientes",
            )
        )


def check_models(report: DiagnosticsReport) -> None:
    registry = get_registry()
    hardware = get_hardware()
    settings = load_settings()
    profile = active_profile()

    active_tts = settings.tts.resolve_model(profile)

    for entry in registry:
        installed = (entry.install_dir / ".installed.json").exists()
        is_active = entry.repo == active_tts
        if installed:
            level = Level.OK
        elif is_active:
            level = Level.ERROR
        elif entry.optional:
            level = Level.INFO
        else:
            level = Level.INFO

        verdict = registry.check_hardware(entry, hardware)
        detail = f"{entry.size_gb:.1f} GB · {entry.license}"
        if not verdict.compatible:
            detail += " · incompatível: " + "; ".join(verdict.reasons)

        report.add(
            Check(
                name=entry.key + (" (ativo)" if is_active else ""),
                level=level,
                summary="instalado" if installed else "não instalado",
                detail=detail,
                hint="" if installed else _model_hint(entry.key),
                group="modelos",
                data={
                    "repo": entry.repo,
                    "license": entry.license,
                    "commercial_ok": entry.commercial_ok(),
                    "size_gb": entry.size_gb,
                    "compatible": verdict.compatible,
                },
            )
        )


def _port_free(host: str, port: int) -> bool | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            return sock.connect_ex((host, port)) != 0
    except OSError:
        # Sandboxes e políticas corporativas podem proibir até criar sockets.
        # O diagnóstico deve continuar e explicar a limitação.
        return None


def check_ports(report: DiagnosticsReport) -> None:
    settings = load_settings()
    for label, port in (
        ("API", settings.app.api_port),
        ("Interface", settings.app.web_port),
    ):
        free = _port_free(settings.app.host, port)
        if free is None:
            hint = "Política do sistema impediu testar a porta."
        elif free:
            hint = ""
        else:
            hint = (
                "Se não for uma instância do Clone Studio, mude a porta em "
                "config/local.yaml."
            )
        report.add(
            Check(
                name=f"porta {port}",
                level=Level.OK if free is not None else Level.INFO,
                summary=("não verificada" if free is None else ("livre" if free else "em uso")),
                detail=f"{label} em {settings.app.host}:{port}",
                hint=hint,
                group="rede",
            )
        )

    report.add(
        Check(
            name="bind",
            level=Level.OK if settings.app.host.startswith("127.") else Level.WARN,
            summary=settings.app.host,
            detail="local-first: o backend não deve escutar em 0.0.0.0",
            group="rede",
        )
    )


def check_services(report: DiagnosticsReport) -> None:
    """Serviços externos opcionais (Ollama, ComfyUI)."""
    settings = load_settings()

    for name, url, why in (
        ("Ollama", settings.llm.base_url, "geração de roteiros"),
        ("ComfyUI", settings.broll.generative.comfyui_url, "B-roll generativo"),
    ):
        online, detail = _probe_http(url)
        report.add(
            Check(
                name=name,
                level=Level.OK if online else Level.INFO,
                summary="online" if online else "offline",
                detail=f"{url} · {why}" + (f" · {detail}" if detail else ""),
                hint="" if online else f"{name} é opcional; o MVP funciona sem ele.",
                group="serviços",
            )
        )


def _probe_http(url: str, timeout: float = 1.5) -> tuple[bool, str]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}"
    except Exception:  # noqa: BLE001 - qualquer falha = offline
        return False, ""


def check_privacy(report: DiagnosticsReport) -> None:
    settings = load_settings()
    issues = []
    if settings.privacy.telemetry:
        issues.append("telemetria")
    if settings.privacy.analytics:
        issues.append("analytics")
    if settings.privacy.allow_outbound_media:
        issues.append("envio de mídia")
    if not settings.app.host.startswith(("127.", "localhost", "::1")):
        issues.append("bind externo")

    report.add(
        Check(
            name="Privacidade",
            level=Level.OK if not issues else Level.ERROR,
            summary="local-first ativo" if not issues else ", ".join(issues),
            detail=(
                "sem telemetria, sem analytics, sem upload automático, "
                "backend em loopback"
            ),
            group="privacidade",
        )
    )


def check_identity(report: DiagnosticsReport) -> None:
    from services.tts.qwen3_tts import list_voice_profiles

    paths = get_paths()
    profiles = list_voice_profiles()
    report.add(
        Check(
            name="Perfis de voz",
            level=Level.OK if profiles else Level.WARN,
            summary=f"{len(profiles)} cadastrado(s)",
            detail=", ".join(p.display_name for p in profiles) or str(paths.voice_dir),
            hint="" if profiles else
            "Cadastre sua voz na página Voice (ou via CLI) antes de gerar áudio.",
            group="identidade",
        )
    )

    templates = (
        [
            f for f in paths.templates_dir.iterdir()
            if f.is_file() and f.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
        ]
        if paths.templates_dir.exists()
        else []
    )
    report.add(
        Check(
            name="Templates de vídeo",
            level=Level.OK if templates else Level.WARN,
            summary=f"{len(templates)} template(s)",
            detail=str(paths.templates_dir),
            hint="" if templates else
            "Adicione vídeos verticais seus em data/identity/templates/.",
            group="identidade",
        )
    )


# ---------------------------------------------------------------------------
# Testes acionáveis (botões da página Diagnostics)
# ---------------------------------------------------------------------------


def test_ffmpeg() -> Check:
    """Gera e valida um MP4 mínimo de ponta a ponta."""
    from core.media import ffmpeg

    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "probe.mp4"
            ffmpeg.run_ffmpeg(
                [
                    "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest",
                    str(target),
                ]
            )
            info = ffmpeg.validate_video(target, min_duration=0.5, require_audio=True)
        return Check(
            name="Test FFmpeg",
            level=Level.OK,
            summary="encode + probe funcionando",
            detail=(
                f"{info.resolution} · {info.duration:.2f}s · "
                f"{info.video.codec_name}/{info.audio.codec_name}"
            ),
            group="testes",
        )
    except Exception as exc:  # noqa: BLE001 - resultado é o próprio diagnóstico
        return Check(
            name="Test FFmpeg",
            level=Level.ERROR,
            summary="falhou",
            detail=str(exc),
            hint="Verifique se este build do FFmpeg tem libx264 e aac.",
            group="testes",
        )


def test_voice() -> Check:
    """Healthcheck da engine de voz (não gera áudio — é rápido)."""
    try:
        from services.tts.qwen3_tts import Qwen3TTSEngine

        status = Qwen3TTSEngine().healthcheck()
    except Exception as exc:  # noqa: BLE001
        return Check(
            name="Test Voice",
            level=Level.ERROR,
            summary="falhou",
            detail=str(exc),
            group="testes",
        )

    return Check(
        name="Test Voice",
        level=Level.OK if status.ok else Level.ERROR,
        summary=status.model if status.ok else "indisponível",
        detail=status.detail,
        hint="; ".join(status.hints),
        group="testes",
        data=status.to_dict(),
    )


def test_captions() -> Check:
    if not engine_available("whisper"):
        return Check(
            name="Test Captions",
            level=Level.ERROR,
            summary="ambiente 'whisper' não instalado",
            hint=_install_hint("whisper"),
            group="testes",
        )
    try:
        from services.transcription.faster_whisper import FasterWhisperEngine

        status = FasterWhisperEngine().healthcheck()
    except Exception as exc:  # noqa: BLE001
        return Check(
            name="Test Captions",
            level=Level.ERROR,
            summary="falhou",
            detail=str(exc),
            group="testes",
        )
    return Check(
        name="Test Captions",
        level=Level.OK if status.ok else Level.ERROR,
        summary=status.model if status.ok else "indisponível",
        detail=status.detail,
        hint="; ".join(status.hints),
        group="testes",
    )


def test_lipsync() -> Check:
    if not engine_available("musetalk"):
        return Check(
            name="Test Lip Sync",
            level=Level.WARN,
            summary="ambiente 'musetalk' não instalado",
            detail="O lip-sync é opcional até a FASE 3.",
            hint=_install_hint("musetalk", musetalk=True),
            group="testes",
        )
    try:
        from services.lipsync.musetalk import MuseTalkEngine

        status = MuseTalkEngine().healthcheck()
    except Exception as exc:  # noqa: BLE001
        return Check(
            name="Test Lip Sync",
            level=Level.ERROR,
            summary="falhou",
            detail=str(exc),
            group="testes",
        )
    return Check(
        name="Test Lip Sync",
        level=Level.OK if status.ok else Level.ERROR,
        summary=status.model if status.ok else "indisponível",
        detail=status.detail,
        hint="; ".join(status.hints),
        group="testes",
    )


def test_ollama() -> Check:
    settings = load_settings()
    online, detail = _probe_http(f"{settings.llm.base_url}/api/tags")
    if not online:
        return Check(
            name="Test Ollama",
            level=Level.INFO,
            summary="offline",
            detail=settings.llm.base_url,
            hint="Opcional. Instale em https://ollama.com e rode `ollama serve`.",
            group="testes",
        )
    try:
        from services.llm.ollama import OllamaClient

        models = OllamaClient().list_models()
        return Check(
            name="Test Ollama",
            level=Level.OK,
            summary=f"{len(models)} modelo(s)",
            detail=", ".join(m.name for m in models[:8]) or "nenhum modelo baixado",
            hint="" if models else "Baixe um modelo: ollama pull qwen3:8b",
            group="testes",
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            name="Test Ollama",
            level=Level.WARN,
            summary="respondeu, mas houve erro",
            detail=f"{detail} · {exc}",
            group="testes",
        )


def test_comfyui() -> Check:
    settings = load_settings()
    online, detail = _probe_http(f"{settings.broll.generative.comfyui_url}/system_stats")
    return Check(
        name="Test ComfyUI",
        level=Level.OK if online else Level.INFO,
        summary="online" if online else "offline",
        detail=settings.broll.generative.comfyui_url + (f" · {detail}" if detail else ""),
        hint="" if online else "Opcional — só é usado para B-roll generativo.",
        group="testes",
    )


TESTS: dict[str, Callable[[], Check]] = {
    "ffmpeg": test_ffmpeg,
    "voice": test_voice,
    "captions": test_captions,
    "lipsync": test_lipsync,
    "ollama": test_ollama,
    "comfyui": test_comfyui,
}


# ---------------------------------------------------------------------------
# Entrada principal
# ---------------------------------------------------------------------------


def run_diagnostics(include_services: bool = True) -> DiagnosticsReport:
    report = DiagnosticsReport()
    check_system(report)
    check_tools(report)
    check_environments(report)
    check_models(report)
    check_ports(report)
    check_privacy(report)
    check_identity(report)
    if include_services:
        check_services(report)
    return report
