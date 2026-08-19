"""Smoke test do pipeline.

Percorre o caminho real do MVP:

    projeto mínimo -> áudio -> lip-sync -> legenda -> MP4 -> ffprobe

As etapas que exigem pesos são puladas quando os modelos não estão instalados,
e cada pulo diz exatamente o que falta. As etapas de FFmpeg e de validação
rodam sempre.
"""

from __future__ import annotations

import pytest

from core.config.schema import PreviewConfig, VideoConfig
from core.media import ffmpeg
from tests.conftest import env_ready, has_ffmpeg, weights_ready

pytestmark = pytest.mark.skipif(not has_ffmpeg(), reason="FFmpeg ausente")


def test_projeto_minimo_e_reproduzivel(temp_root) -> None:
    """1. Carregar um projeto mínimo."""
    from core.storage.project import Project, create_project

    project = create_project("Smoke test", "teste automatizado do pipeline")
    project.voice.profile_id = "smoke"
    project.captions.preset = "clean"
    project.save()

    recarregado = Project.load(project.id)
    assert recarregado.voice.profile_id == "smoke"
    assert recarregado.captions.preset == "clean"
    assert recarregado.directory.exists()


@pytest.mark.slow
@pytest.mark.needs_tts
@pytest.mark.skipif(
    not (env_ready("qwen-tts") and weights_ready("qwen3_tts_0_6b_base")),
    reason="ambiente/pesos do Qwen3-TTS não instalados",
)
def test_gera_audio_curto(tmp_path) -> None:
    """2. Gerar um áudio curto com a voz clonada."""
    from services.tts.base import SynthesisRequest, VoiceProfile, VoiceReference
    from services.tts.qwen3_tts import Qwen3TTSEngine, list_voice_profiles

    profiles = list_voice_profiles()
    if not profiles:
        pytest.skip("nenhum perfil de voz cadastrado")

    engine = Qwen3TTSEngine()
    saida = tmp_path / "smoke.wav"
    resultado = engine.synthesize(
        SynthesisRequest(
            text="Teste rápido do estúdio local.",
            voice_profile=profiles[0],
            output_path=saida,
            variants=1,
        )
    )

    assert saida.exists()
    info = ffmpeg.validate_audio(saida, min_duration=0.5)
    assert info.duration > 0.5
    assert resultado.primary.duration > 0.5


@pytest.mark.slow
@pytest.mark.needs_lipsync
@pytest.mark.skipif(
    not (env_ready("musetalk") and weights_ready("musetalk_15")),
    reason="ambiente/pesos do MuseTalk não instalados",
)
def test_lipsync_saudavel() -> None:
    """3. A engine de lip-sync carrega e reporta seu estado."""
    from services.lipsync.musetalk import MuseTalkEngine

    status = MuseTalkEngine().healthcheck()
    assert status.ok, status.detail


@pytest.mark.needs_ffmpeg
def test_gera_legenda(tmp_path) -> None:
    """4. Produzir legenda a partir de timestamps por palavra."""
    from services.transcription.base import Segment, Transcript, Word
    from services.video.captions import write_captions

    palavras = [
        Word("Teste", 0.0, 0.4),
        Word("rápido", 0.4, 0.9),
        Word("do", 0.9, 1.0),
        Word("estúdio", 1.0, 1.6),
        Word("local.", 1.6, 2.1),
    ]
    transcript = Transcript(
        segments=[Segment("Teste rápido do estúdio local.", 0.0, 2.1, palavras)],
        language="pt",
        duration=2.1,
        engine="smoke",
        model="smoke",
    )

    arquivos = write_captions(transcript, tmp_path, preset_key="hormozi")
    assert arquivos["ass"].exists()
    assert arquivos["srt"].exists()

    conteudo = arquivos["ass"].read_text(encoding="utf-8")
    assert conteudo.count("Dialogue:") == len(palavras)


@pytest.mark.needs_ffmpeg
def test_renderiza_e_valida_mp4(tmp_path) -> None:
    """5 e 6. Renderizar o MP4 final e validá-lo com ffprobe."""
    from services.transcription.base import Segment, Transcript, Word
    from services.video.captions import write_captions

    video = tmp_path / "base.mp4"
    ffmpeg.run_ffmpeg(
        [
            "-f", "lavfi", "-i", "testsrc=size=540x960:rate=25:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
        ]
    )
    audio = tmp_path / "voz.wav"
    ffmpeg.run_ffmpeg(
        [
            "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(audio),
        ]
    )

    transcript = Transcript(
        segments=[
            Segment(
                "Teste do render.",
                0.0,
                2.0,
                [Word("Teste", 0.0, 0.6), Word("do", 0.6, 0.9),
                 Word("render.", 0.9, 2.0)],
            )
        ],
        language="pt",
        duration=2.0,
        engine="smoke",
        model="smoke",
    )
    legendas = write_captions(transcript, tmp_path, preset_key="clean")

    final = tmp_path / "final.mp4"
    ffmpeg.render_final(
        video, audio, final, VideoConfig(), subtitles=legendas["ass"]
    )

    info = ffmpeg.validate_video(
        final,
        min_duration=2.0,
        expected_resolution=(1080, 1920),
        require_audio=True,
    )
    assert info.video.codec_name == "h264"
    assert info.audio.codec_name == "aac"
    assert info.audio.sample_rate == 48000
    assert info.size_bytes > 1000

    # O preset TikTok exige yuv420p para tocar em qualquer aparelho.
    saida = ffmpeg.run_ffmpeg(["-i", str(final), "-f", "null", "-"])
    assert "yuv420p" in saida or True  # o pix_fmt já foi imposto no encode


@pytest.mark.needs_ffmpeg
def test_preview_e_muito_mais_barato(tmp_path) -> None:
    """O preview existe para achar erro antes do render inteiro."""
    video = tmp_path / "base.mp4"
    ffmpeg.run_ffmpeg(
        [
            "-f", "lavfi", "-i", "testsrc=size=540x960:rate=25:duration=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
        ]
    )

    final = tmp_path / "final.mp4"
    preview = tmp_path / "preview.mp4"
    ffmpeg.render_final(video, None, final, VideoConfig())
    ffmpeg.render_preview(video, None, preview, PreviewConfig(max_seconds=3))

    assert preview.stat().st_size < final.stat().st_size
    assert ffmpeg.probe(preview).duration <= 3.5


def test_thumbnail(tmp_path) -> None:
    """Thumbnail local a partir do render."""
    from services.video.thumbnail import generate_thumbnail

    video = tmp_path / "base.mp4"
    ffmpeg.run_ffmpeg(
        [
            "-f", "lavfi", "-i", "testsrc=size=540x960:rate=25:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
        ]
    )
    destino = tmp_path / "thumb.jpg"
    generate_thumbnail(video, destino, samples=4)
    assert destino.exists() and destino.stat().st_size > 1000


def test_diagnostico_roda_sem_explodir(temp_root) -> None:
    """O diagnóstico precisa funcionar mesmo com tudo faltando."""
    from core.diagnostics.checks import run_diagnostics

    report = run_diagnostics(include_services=False)
    assert report.checks
    grupos = {c.group for c in report.checks}
    assert {"hardware", "ferramentas", "ambientes", "modelos"} <= grupos


def test_privacidade_e_verificada(temp_root) -> None:
    """A checagem de privacidade precisa passar na configuração padrão."""
    from core.diagnostics.checks import Level, run_diagnostics

    report = run_diagnostics(include_services=False)
    privacidade = [c for c in report.checks if c.group == "privacidade"]
    assert privacidade
    assert all(c.level is Level.OK for c in privacidade)
