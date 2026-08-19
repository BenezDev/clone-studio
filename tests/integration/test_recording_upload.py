"""Gravação feita dentro do app, atravessando as rotas de verdade.

O caminho novo (gravar no navegador) e o antigo (enviar arquivo) terminam na
mesma rota. Estes testes exercitam a rota inteira com o arquivo que o
``MediaRecorder`` realmente produz — sem duração no cabeçalho — porque é
exatamente aí que a implementação ingênua quebra: o upload seria recusado com
"0.0s", como se a pessoa tivesse gravado nada.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from tests.conftest import has_ffmpeg

pytestmark = [
    pytest.mark.needs_ffmpeg,
    pytest.mark.skipif(not has_ffmpeg(), reason="FFmpeg ausente"),
]


def _upload(path: Path, filename: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(path.read_bytes()), filename=filename)


def _wav(destino: Path, segundos: float) -> Path:
    """WAV bem formado, como o de quem já tem a amostra gravada em disco."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=220:duration={segundos}",
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            str(destino),
        ],
        check=True,
    )
    return destino


def _webm_video(destino: Path, tamanho: str, segundos: float) -> Path:
    """WebM escrito em pipe: o cabeçalho sai incompleto, como no navegador."""
    with destino.open("wb") as saida:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi",
                "-i", f"testsrc=size={tamanho}:rate=25:duration={segundos}",
                "-c:v", "libvpx", "-b:v", "150k", "-f", "webm", "pipe:1",
            ],
            stdout=saida,
            check=True,
        )
    return destino


def _enviar_template(video: UploadFile) -> dict:
    """Chama a rota com os valores que o FastAPI preencheria na requisição."""
    from apps.api.app.routers.templates import upload_template

    return upload_template(
        video=video,
        style="talking_head",
        energy="medium",
        gestures="medium",
        camera="medium_close",
        background="neutral",
    )


class TestVozGravadaNoApp:
    def test_cadastra_gravacao_do_navegador(
        self, temp_root, streamed_webm_audio
    ) -> None:
        from apps.api.app.routers.voice import enroll

        perfil = enroll(
            "me",
            audio=_upload(streamed_webm_audio, "gravacao.webm"),
            transcript="amostra gravada dentro do app",
            display_name="Minha voz",
            label="default",
        )

        assert perfil["references"], "a amostra gravada não virou referência"
        referencia = perfil["references"][0]
        assert referencia["duration"] > 3
        # O cadastro sempre guarda WAV, venha de gravação ou de arquivo.
        assert referencia["audio_path"].endswith(".wav")
        assert (temp_root / referencia["audio_path"]).exists()

    def test_arquivo_enviado_continua_funcionando(
        self, temp_root, tmp_path
    ) -> None:
        """O caminho antigo não pode ter regredido com a chegada do novo."""
        from apps.api.app.routers.voice import enroll

        perfil = enroll(
            "me",
            audio=_upload(_wav(tmp_path / "amostra.wav", 5), "amostra.wav"),
            transcript="amostra enviada como arquivo",
            display_name="Minha voz",
            label="default",
        )
        referencia = perfil["references"][0]
        assert referencia["audio_path"].endswith(".wav")
        assert (temp_root / referencia["audio_path"]).exists()

    def test_formato_desconhecido_ainda_e_recusado(
        self, temp_root, streamed_webm_audio
    ) -> None:
        """Aceitar WebM não pode ter aberto a porta para qualquer extensão."""
        from apps.api.app.routers.voice import enroll

        with pytest.raises(HTTPException) as erro:
            enroll(
                "me",
                audio=_upload(streamed_webm_audio, "gravacao.exe"),
                transcript="qualquer coisa",
                display_name="",
                label="default",
            )
        assert erro.value.status_code == 400


class TestMensagemDeErroDaVoz:
    """A mensagem de erro tem que dizer a verdade sobre a duração.

    Uma regressão real: o tratador lia a duração **depois** de apagar o
    arquivo, então o `exists()` era sempre falso e toda amostra curta virava
    "A amostra tem 0.0s". 0.0s é o sintoma de arquivo vazio ou truncado — a
    pessoa iria procurar problema no microfone, quando o que faltava era falar
    mais tempo.
    """

    def test_amostra_curta_relata_a_duracao_real(self, temp_root, tmp_path) -> None:
        from apps.api.app.routers.voice import enroll

        with pytest.raises(HTTPException) as erro:
            enroll(
                "me",
                audio=_upload(_wav(tmp_path / "curta.wav", 1.5), "curta.wav"),
                transcript="curta demais",
                display_name="",
                label="default",
            )
        detalhe = str(erro.value.detail)
        assert "0.0s" not in detalhe, "voltou a mentir a duração"
        assert "1.5s" in detalhe

    def test_amostra_longa_demais_e_recusada_antes_de_decodificar(
        self, temp_root, tmp_path
    ) -> None:
        """O teto de origem evita normalizar um arquivo de horas à toa."""
        from apps.api.app.routers.voice import enroll

        with pytest.raises(HTTPException) as erro:
            enroll(
                "me",
                audio=_upload(_wav(tmp_path / "longa.wav", 130), "longa.wav"),
                transcript="longa demais",
                display_name="",
                label="default",
            )
        assert "no máximo" in str(erro.value.detail)

    def test_arquivo_sem_audio_diz_isso(self, temp_root, sample_video) -> None:
        from apps.api.app.routers.voice import enroll

        with pytest.raises(HTTPException) as erro:
            enroll(
                "me",
                audio=_upload(sample_video, "video.mp4"),
                transcript="isto e um video",
                display_name="",
                label="default",
            )
        assert "áudio" in str(erro.value.detail)


class TestTemplateGravadoNoApp:
    def test_envia_gravacao_do_navegador(self, temp_root, streamed_webm) -> None:
        resposta = _enviar_template(_upload(streamed_webm, "gravacao.webm"))

        template = resposta["template"]
        assert template["duration"] > 1, "a duração precisa ter sido recuperada"
        assert (temp_root / template["video_path"]).exists()

    def test_arquivo_enviado_continua_funcionando(
        self, temp_root, sample_video
    ) -> None:
        resposta = _enviar_template(_upload(sample_video, "meu-video.mp4"))
        assert resposta["template"]["duration"] > 1
        assert resposta["warnings"] == [], "vídeo vertical não devia gerar aviso"

    def test_gravacao_ruim_nao_apaga_template_existente(
        self, temp_root, tmp_path, sample_video
    ) -> None:
        """Perda de dados real: a conversão de resgate escrevia por cima.

        Quando a validação falha e o arquivo é WebM, a rota tenta salvar
        convertendo para MP4. Com `with_suffix('.mp4')` o destino era o nome do
        template **já existente** — a unicidade tinha sido garantida só para o
        `.webm`. Se a conversão também falhasse, o `unlink` de limpeza levava
        junto o vídeo bom que já estava lá.
        """
        _enviar_template(_upload(sample_video, "gravacao.mp4"))
        bom = temp_root / "data" / "identity" / "templates" / "gravacao.mp4"
        assert bom.exists()
        antes = bom.read_bytes()

        # 0,5s: remuxa bem, mas fica abaixo do mínimo de 1s da validação.
        curta = _webm_video(tmp_path / "curta.webm", "180x320", 0.5)
        with pytest.raises(HTTPException):
            _enviar_template(_upload(curta, "gravacao.webm"))

        assert bom.exists(), "o template existente foi apagado"
        assert bom.read_bytes() == antes, "o template existente foi sobrescrito"

    def test_gravacao_horizontal_avisa_sobre_o_corte(
        self, temp_root, tmp_path
    ) -> None:
        """Webcam entrega horizontal; o aviso é o que evita o vídeo mal cortado."""
        deitado = _webm_video(tmp_path / "deitado.webm", "320x180", 3)
        resposta = _enviar_template(_upload(deitado, "gravacao.webm"))
        assert any("horizontal" in aviso for aviso in resposta["warnings"])
