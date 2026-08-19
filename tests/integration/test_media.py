"""Integração com FFmpeg: encode, probe e validação de estágio."""

from __future__ import annotations

import pytest

from core.config.schema import PreviewConfig, VideoConfig
from core.media import ffmpeg
from tests.conftest import has_ffmpeg

pytestmark = [
    pytest.mark.needs_ffmpeg,
    pytest.mark.skipif(not has_ffmpeg(), reason="FFmpeg ausente"),
]


class TestProbe:
    def test_le_video(self, sample_video) -> None:
        info = ffmpeg.probe(sample_video)
        assert info.has_video
        assert info.duration > 2.5
        assert info.resolution == (270, 480)

    def test_le_audio(self, sample_audio) -> None:
        info = ffmpeg.probe(sample_audio)
        assert info.has_audio
        assert info.audio.sample_rate == 24000
        assert abs(info.duration - 2.0) < 0.1

    def test_arquivo_ausente(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            ffmpeg.probe(tmp_path / "nao_existe.mp4")

    def test_arquivo_invalido(self, tmp_path) -> None:
        ruim = tmp_path / "ruim.mp4"
        ruim.write_bytes(b"isto nao e um video")
        with pytest.raises(ffmpeg.FFmpegError) as excinfo:
            ffmpeg.probe(ruim)
        # O erro precisa carregar contexto, não só "falhou".
        assert "comando:" in str(excinfo.value)
        assert "exit code:" in str(excinfo.value)


class TestValidacao:
    def test_audio_valido(self, sample_audio) -> None:
        info = ffmpeg.validate_audio(sample_audio, expected_sample_rate=24000)
        assert info.duration > 0

    def test_audio_curto_demais(self, sample_audio) -> None:
        with pytest.raises(ffmpeg.ValidationError, match="duração"):
            ffmpeg.validate_audio(sample_audio, min_duration=10.0)

    def test_taxa_de_amostragem_errada(self, sample_audio) -> None:
        with pytest.raises(ffmpeg.ValidationError, match="Hz"):
            ffmpeg.validate_audio(sample_audio, expected_sample_rate=48000)

    def test_video_sem_audio_quando_exigido(self, sample_video) -> None:
        with pytest.raises(ffmpeg.ValidationError, match="áudio"):
            ffmpeg.validate_video(sample_video, require_audio=True)

    def test_resolucao_errada(self, sample_video) -> None:
        with pytest.raises(ffmpeg.ValidationError, match="esperado"):
            ffmpeg.validate_video(sample_video, expected_resolution=(1080, 1920))

    def test_video_no_lugar_de_audio(self, sample_video) -> None:
        with pytest.raises(ffmpeg.ValidationError, match="stream de áudio"):
            ffmpeg.validate_audio(sample_video)


class TestConversao:
    def test_para_wav_normalizado(self, sample_audio, tmp_path) -> None:
        destino = tmp_path / "saida.wav"
        ffmpeg.to_wav(sample_audio, destino, sample_rate=24000, mono=True)
        info = ffmpeg.probe(destino)
        assert info.audio.sample_rate == 24000
        assert info.audio.channels == 1
        assert info.audio.codec_name == "pcm_s16le"

    def test_remocao_de_silencio(self, tmp_path) -> None:
        com_silencio = tmp_path / "com_silencio.wav"
        ffmpeg.run_ffmpeg(
            [
                "-f", "lavfi",
                "-i", "aevalsrc=0:d=1[a];sine=frequency=300:d=1[b];[a][b]concat=n=2:v=0:a=1",
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                str(com_silencio),
            ]
        )
        limpo = tmp_path / "limpo.wav"
        ffmpeg.to_wav(com_silencio, limpo, trim_silence=True)
        assert ffmpeg.probe(limpo).duration < ffmpeg.probe(com_silencio).duration

    def test_loudness(self, sample_audio, tmp_path) -> None:
        destino = tmp_path / "loud.wav"
        ffmpeg.loudnorm(sample_audio, destino, lufs=-14.0)
        info = ffmpeg.probe(destino)
        assert info.has_audio
        assert abs(info.duration - 2.0) < 0.3


class TestRender:
    def test_render_final_em_9_16(self, sample_video, sample_audio, tmp_path) -> None:
        destino = tmp_path / "final.mp4"
        ffmpeg.render_final(sample_video, sample_audio, destino, VideoConfig())
        info = ffmpeg.validate_video(
            destino, expected_resolution=(1080, 1920), require_audio=True
        )
        assert info.video.codec_name == "h264"
        assert info.audio.codec_name == "aac"
        assert info.audio.sample_rate == 48000

    def test_preview_menor_e_mais_leve(self, sample_video, sample_audio, tmp_path) -> None:
        final = tmp_path / "final.mp4"
        preview = tmp_path / "preview.mp4"
        ffmpeg.render_final(sample_video, sample_audio, final, VideoConfig())
        ffmpeg.render_preview(sample_video, sample_audio, preview, PreviewConfig())

        info = ffmpeg.validate_video(preview, expected_resolution=(540, 960))
        assert info.size_bytes < final.stat().st_size

    def test_preview_limita_duracao(self, tmp_path) -> None:
        longo = tmp_path / "longo.mp4"
        ffmpeg.run_ffmpeg(
            [
                "-f", "lavfi", "-i", "testsrc=size=270x480:rate=25:duration=20",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(longo),
            ]
        )
        preview = tmp_path / "preview.mp4"
        ffmpeg.render_preview(
            longo, None, preview, PreviewConfig(max_seconds=5)
        )
        assert ffmpeg.probe(preview).duration <= 6.0

    def test_legendas_queimadas(self, sample_video, sample_audio, tmp_path) -> None:
        legenda = tmp_path / "captions.ass"
        legenda.write_text(
            """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Sans,90,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,80,80,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,TESTE
""",
            encoding="utf-8",
        )
        destino = tmp_path / "com_legenda.mp4"
        ffmpeg.render_final(
            sample_video, sample_audio, destino, VideoConfig(), subtitles=legenda
        )
        assert ffmpeg.validate_video(destino, expected_resolution=(1080, 1920))

    def test_concat(self, sample_video, tmp_path) -> None:
        destino = tmp_path / "juntos.mp4"
        ffmpeg.concat_videos([sample_video, sample_video], destino)
        assert ffmpeg.probe(destino).duration > ffmpeg.probe(sample_video).duration * 1.8

    def test_thumbnail(self, sample_video, tmp_path) -> None:
        destino = tmp_path / "thumb.jpg"
        ffmpeg.extract_thumbnail(sample_video, destino)
        assert destino.exists() and destino.stat().st_size > 500


class TestErros:
    def test_erro_traz_contexto(self, tmp_path) -> None:
        with pytest.raises(ffmpeg.FFmpegError) as excinfo:
            ffmpeg.run_ffmpeg(["-i", str(tmp_path / "inexistente.mp4"),
                               str(tmp_path / "saida.mp4")])
        mensagem = str(excinfo.value)
        assert "comando:" in mensagem
        assert "exit code:" in mensagem
        assert "stderr" in mensagem
        assert excinfo.value.hint


class TestGravacaoDoNavegador:
    """Reparo do arquivo que a gravação dentro do app produz.

    Estes testes existem por um problema concreto: o WebM do ``MediaRecorder``
    chega sem duração no cabeçalho, `probe()` devolve 0.0 e toda validação do
    projeto recusaria uma gravação perfeita dizendo "0.0s" — o mesmo sintoma de
    um arquivo truncado.
    """

    def test_gravacao_chega_sem_duracao(self, streamed_webm) -> None:
        """Confirma a premissa. Se um dia deixar de valer, o resto é inútil."""
        assert ffmpeg.probe(streamed_webm).duration == 0.0

    def test_reparo_recupera_a_duracao(self, streamed_webm) -> None:
        assert ffmpeg.ensure_container_metadata(streamed_webm) is True
        info = ffmpeg.probe(streamed_webm)
        assert abs(info.duration - 3.0) < 0.2
        assert info.has_video

    def test_reparo_destrava_a_validacao(self, streamed_webm) -> None:
        """É este o ponto: antes do reparo o upload seria recusado."""
        with pytest.raises(ffmpeg.ValidationError, match="duração"):
            ffmpeg.validate_video(streamed_webm, min_duration=1.0)
        ffmpeg.ensure_container_metadata(streamed_webm)
        assert ffmpeg.validate_video(streamed_webm, min_duration=1.0).duration > 1

    def test_reparo_nao_reencoda(self, streamed_webm) -> None:
        """`-c copy` mantém o codec: reencodar custaria CPU e qualidade."""
        antes = ffmpeg.probe(streamed_webm).video
        assert antes is not None
        ffmpeg.ensure_container_metadata(streamed_webm)
        depois = ffmpeg.probe(streamed_webm).video
        assert depois is not None
        assert depois.codec_name == antes.codec_name

    def test_arquivo_integro_nao_e_tocado(self, sample_video) -> None:
        antes = sample_video.read_bytes()
        assert ffmpeg.ensure_container_metadata(sample_video) is False
        assert sample_video.read_bytes() == antes

    def test_e_idempotente(self, streamed_webm) -> None:
        assert ffmpeg.ensure_container_metadata(streamed_webm) is True
        assert ffmpeg.ensure_container_metadata(streamed_webm) is False

    def test_nao_deixa_arquivo_temporario(self, streamed_webm) -> None:
        ffmpeg.ensure_container_metadata(streamed_webm)
        assert list(streamed_webm.parent.glob("*.remux.*")) == []

    def test_arquivo_ilegivel_propaga_erro(self, tmp_path) -> None:
        """Falha de leitura tem que chegar ao usuário, não sumir no reparo."""
        ruim = tmp_path / "ruim.webm"
        ruim.write_bytes(b"isto nao e um video")
        with pytest.raises(ffmpeg.FFmpegError):
            ffmpeg.ensure_container_metadata(ruim)


class TestAutoEditor:
    """A EDL vira filtro e o filtro tem que sobreviver ao FFmpeg.

    Testar só a string gerada não pega os dois erros que realmente acontecem:
    vírgula não escapada (que separa filtros e quebra a cadeia) e o `zoompan`
    assumindo 25 fps quando ninguém passa `fps`, o que baixaria a cadência do
    render inteiro em silêncio.
    """

    @staticmethod
    def _renderiza(origem, destino, filtros):
        ffmpeg.run_ffmpeg(
            [
                "-i", str(origem),
                "-vf", ",".join(["fps=30", *filtros]),
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                str(destino),
            ]
        )
        return destino

    @pytest.fixture
    def fonte(self, tmp_path):
        alvo = tmp_path / "fonte.mp4"
        ffmpeg.run_ffmpeg(
            [
                "-f", "lavfi",
                "-i", "testsrc=size=270x480:rate=30:duration=6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(alvo),
            ]
        )
        return alvo

    def _filtro(self):
        from services.video.editor import Shot, EditDecisionList, build_zoom_filter

        edl = EditDecisionList(
            duration=6.0,
            preset="sutil",
            shots=[
                Shot(0.0, 2.0, 1.0, "abertura"),
                Shot(2.0, 4.0, 1.12, "frase nova"),
                Shot(4.0, 6.0, 1.0, "frase nova"),
            ],
        )
        return build_zoom_filter(edl, width=270, height=480, fps=30)

    def test_filtro_roda_sem_erro(self, fonte, tmp_path) -> None:
        saida = self._renderiza(fonte, tmp_path / "com_zoom.mp4", [self._filtro()])
        assert ffmpeg.validate_video(saida, min_duration=5.0).has_video

    def test_preserva_frames_resolucao_e_fps(self, fonte, tmp_path) -> None:
        saida = self._renderiza(fonte, tmp_path / "com_zoom.mp4", [self._filtro()])
        info = ffmpeg.probe(saida)
        assert info.resolution == (270, 480)
        assert info.video is not None
        assert round(info.video.fps) == 30, "o zoompan derrubou a cadência para 25"
        assert info.video.nb_frames == 180, "frames perdidos ou duplicados"

    def test_cadencia_da_saida_manda_e_nao_a_da_origem(self, tmp_path) -> None:
        """O bug real: origem a 25 fps derrubou um render configurado para 30.

        O `zoompan` reescreve a cadência. Passar o fps do lip-sync (25) num
        render de 30 baixa o vídeo inteiro em silêncio — nenhum erro, nenhum
        aviso, só um arquivo com 20% menos quadros. O teste antigo não pegava
        porque origem e destino tinham o mesmo fps.
        """
        from services.video.editor import Shot, EditDecisionList, build_zoom_filter

        origem = tmp_path / "origem25.mp4"
        ffmpeg.run_ffmpeg(
            [
                "-f", "lavfi",
                "-i", "testsrc=size=270x480:rate=25:duration=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(origem),
            ]
        )
        assert round(ffmpeg.probe(origem).video.fps) == 25

        edl = EditDecisionList(
            duration=4.0,
            preset="sutil",
            shots=[Shot(0.0, 2.0, 1.0, "a"), Shot(2.0, 4.0, 1.1, "b")],
        )
        filtro = build_zoom_filter(edl, width=270, height=480, fps=30)
        saida = self._renderiza(origem, tmp_path / "saida30.mp4", [filtro])

        info = ffmpeg.probe(saida)
        assert round(info.video.fps) == 30, "a origem 25 fps venceu o destino 30 fps"
        assert info.video.nb_frames == 120

    def test_o_zoom_realmente_altera_a_imagem(self, fonte, tmp_path) -> None:
        """Sem isto, um filtro que virasse no-op passaria nos outros testes."""
        com = self._renderiza(fonte, tmp_path / "com.mp4", [self._filtro()])
        sem = self._renderiza(fonte, tmp_path / "sem.mp4", [])
        assert com.read_bytes() != sem.read_bytes()

    def test_preset_sem_movimento_nao_gera_filtro(self) -> None:
        """`clean` não pode custar um passo de reencode por nada."""
        from services.video.editor import build_zoom_filter, plan_edit

        edl = plan_edit(30.0, transcript=None, preset="clean")
        assert build_zoom_filter(edl, width=270, height=480, fps=30) is None
