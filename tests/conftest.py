"""Configuração compartilhada dos testes.

Os testes NUNCA tocam nos dados reais do usuário: cada sessão roda contra uma
raiz temporária, com a mesma estrutura de diretórios do projeto.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: leva mais de alguns segundos")
    config.addinivalue_line("markers", "needs_ffmpeg: exige FFmpeg no PATH")
    config.addinivalue_line("markers", "needs_tts: exige o ambiente e os pesos do TTS")
    config.addinivalue_line(
        "markers", "needs_lipsync: exige o ambiente e os pesos do MuseTalk"
    )


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture
def temp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Raiz isolada do Clone Studio para um teste."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "config" / "default.yaml", tmp_path / "config" / "default.yaml")
    shutil.copy(
        ROOT / "config" / "model_registry.yaml",
        tmp_path / "config" / "model_registry.yaml",
    )

    monkeypatch.setenv("CLONE_STUDIO_ROOT", str(tmp_path))

    from core.config import loader
    from core.licensing import registry
    from core.storage import paths as paths_module

    paths_module.get_paths.cache_clear()
    loader._cached_settings.cache_clear()
    loader.get_hardware.cache_clear()
    registry.get_registry.cache_clear()

    paths_module.get_paths().ensure_runtime_dirs()

    yield tmp_path

    paths_module.get_paths.cache_clear()
    loader._cached_settings.cache_clear()
    loader.get_hardware.cache_clear()
    registry.get_registry.cache_clear()


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def env_ready(name: str) -> bool:
    return (ROOT / ".envs" / name / "bin" / "python").exists()


def weights_ready(key: str) -> bool:
    return (ROOT / "models" / key / ".installed.json").exists()


@pytest.fixture
def sample_audio(tmp_path: Path) -> Path:
    """WAV curto e sintético (tom senoidal) — não é fala, mas é áudio válido."""
    if not has_ffmpeg():
        pytest.skip("FFmpeg ausente")
    target = tmp_path / "sample.wav"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            str(target),
        ],
        check=True,
    )
    return target


@pytest.fixture
def streamed_webm(tmp_path: Path) -> Path:
    """WebM sem duração no cabeçalho — o que o navegador realmente entrega.

    O ``MediaRecorder`` grava num stream, e um WebM escrito em saída
    não-buscável não pode voltar ao início para preencher o cabeçalho: sai sem
    duração e sem índice de busca. Reproduzir isso aqui é simples — basta
    mandar o ffmpeg escrever num pipe em vez de num arquivo.

    Sem um fixture assim, o teste passaria contra um arquivo bem formado e o
    bug só apareceria na primeira gravação de verdade.
    """
    if not has_ffmpeg():
        pytest.skip("FFmpeg ausente")
    target = tmp_path / "gravacao.webm"
    with target.open("wb") as saida:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=180x320:rate=25:duration=3",
                "-c:v", "libvpx", "-b:v", "150k", "-f", "webm", "pipe:1",
            ],
            stdout=saida,
            check=True,
        )
    return target


@pytest.fixture
def streamed_webm_audio(tmp_path: Path) -> Path:
    """Áudio no mesmo formato que a gravação de voz do app entrega."""
    if not has_ffmpeg():
        pytest.skip("FFmpeg ausente")
    target = tmp_path / "voz.webm"
    with target.open("wb") as saida:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=5",
                "-c:a", "libopus", "-b:a", "48k", "-f", "webm", "pipe:1",
            ],
            stdout=saida,
            check=True,
        )
    return target


@pytest.fixture
def sample_video(tmp_path: Path) -> Path:
    """MP4 vertical curto e sintético."""
    if not has_ffmpeg():
        pytest.skip("FFmpeg ausente")
    target = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=270x480:rate=25:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(target),
        ],
        check=True,
    )
    return target
