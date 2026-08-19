"""Biblioteca de B-roll: normalização, escolha e planejamento.

O risco dominante aqui é o **falso positivo**. Um asset escolhido por engano
cobre o rosto do usuário com a imagem errada no meio da frase, e ele só
descobre assistindo ao render pronto. Por isso boa parte destes testes é sobre
o que NÃO pode casar.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from services.video.broll import (
    MAX_CUTS,
    MAX_COVERAGE,
    MIN_CUT_SECONDS,
    BrollAsset,
    find_asset,
    normalize,
    plan_broll,
    score_asset,
)


def asset(nome: str, duracao: float = 8.0, kind: str = "video") -> BrollAsset:
    return BrollAsset(
        id=f"{nome}.mp4",
        path=f"data/assets/broll/{nome}.mp4",
        kind=kind,
        duration=duracao,
        width=1080,
        height=1920,
        keywords=tuple(normalize(nome)),
    )


@dataclass
class FakeSegment:
    text: str
    start: float
    end: float


@dataclass
class FakeWord:
    text: str
    start: float
    end: float


class FakeTranscript:
    def __init__(self, segments, words=None):
        self.segments = segments
        self.words = words or []


def fala(texto: str, inicio: float, fim: float) -> FakeTranscript:
    """Transcrição com palavras distribuídas por igual no intervalo."""
    palavras = texto.split()
    passo = (fim - inicio) / max(1, len(palavras))
    return FakeTranscript(
        segments=[FakeSegment(texto, inicio, fim)],
        words=[
            FakeWord(p, inicio + i * passo, inicio + (i + 1) * passo)
            for i, p in enumerate(palavras)
        ],
    )


def cena(texto: str, prompt: str | None = None) -> dict:
    return {"text": texto, "broll_prompt": prompt}


class TestNormalizacao:
    def test_tira_acento(self) -> None:
        """O usuário digita 'gráfico' e nomeia o arquivo 'grafico'."""
        assert normalize("Gráfico") == normalize("grafico")

    def test_descarta_palavra_vazia(self) -> None:
        """'de', 'do', 'video' casariam com tudo e destruiriam o ranking."""
        assert normalize("o gráfico do bitcoin") == ["grafico", "bitcoin"]

    def test_descarta_token_curto(self) -> None:
        assert "ab" not in normalize("ab bitcoin")

    def test_separa_por_pontuacao_e_underscore(self) -> None:
        assert normalize("bitcoin_grafico-queda.mp4") == [
            "bitcoin", "grafico", "queda", "mp4",
        ]


class TestEscolha:
    def test_casa_pelo_nome(self) -> None:
        biblioteca = [asset("bitcoin_grafico_queda"), asset("gato_dormindo")]
        achado, nota = find_asset("gráfico do bitcoin caindo", biblioteca)
        assert achado.id == "bitcoin_grafico_queda.mp4"
        assert nota > 0.5

    def test_nao_inventa_correspondencia(self) -> None:
        """Falso positivo cobre o rosto com a imagem errada."""
        biblioteca = [asset("bitcoin_grafico_queda"), asset("gato_dormindo")]
        achado, nota = find_asset("foguete espacial decolando", biblioteca)
        assert achado is None or nota == 0.0

    def test_biblioteca_vazia(self) -> None:
        assert find_asset("qualquer coisa", []) == (None, 0.0)

    def test_pedido_vazio_nao_casa(self) -> None:
        assert score_asset(asset("bitcoin_grafico"), []) == 0.0


class TestPlanejamento:
    def test_so_entra_onde_o_autor_pediu(self) -> None:
        """Enfiar imagem sem pedido é adivinhação cara de desfazer."""
        cenas = [cena("primeira frase"), cena("segunda frase")]
        cortes, _ = plan_broll(
            cenas, library=[asset("bitcoin_grafico")], duration=30.0
        )
        assert cortes == []

    def test_respeita_o_teto_de_cobertura(self) -> None:
        cenas = [cena(f"frase {i}", "bitcoin grafico") for i in range(6)]
        cortes, _ = plan_broll(
            cenas, library=[asset("bitcoin_grafico")], duration=60.0
        )
        assert sum(c.duration for c in cortes) <= 60.0 * MAX_COVERAGE + 0.01

    def test_apara_em_vez_de_descartar(self) -> None:
        """Num vídeo curto o primeiro corte já estouraria o teto de cobertura.

        A duração é escolhida para o orçamento (40% = 3,2s) ficar ABAIXO do
        limite por corte (3,5s) — senão quem manda é o outro limite e o teste
        não exercitaria a aparagem.
        """
        cenas = [cena("frase única", "bitcoin grafico")]
        cortes, _ = plan_broll(
            cenas, library=[asset("bitcoin_grafico")], duration=8.0
        )
        assert len(cortes) == 1
        assert cortes[0].duration == pytest.approx(8.0 * MAX_COVERAGE)

    def test_limita_a_quantidade_de_cortes(self) -> None:
        cenas = [cena(f"f{i}", "bitcoin grafico") for i in range(12)]
        cortes, _ = plan_broll(
            cenas, library=[asset("bitcoin_grafico")], duration=600.0
        )
        assert len(cortes) <= MAX_CUTS

    def test_nunca_gera_corte_curto_demais(self) -> None:
        cenas = [cena(f"f{i}", "bitcoin grafico") for i in range(4)]
        cortes, _ = plan_broll(
            cenas, library=[asset("bitcoin_grafico")], duration=40.0
        )
        assert all(c.duration >= MIN_CUT_SECONDS for c in cortes)

    def test_nao_passa_da_duracao_do_asset(self) -> None:
        """Estender um clipe de 2s por 3,5s congelaria o último quadro."""
        cenas = [cena("frase", "bitcoin grafico")]
        cortes, _ = plan_broll(
            cenas, library=[asset("bitcoin_grafico", duracao=2.0)], duration=60.0
        )
        assert cortes[0].duration <= 2.0

    def test_avisa_quando_nada_combina(self) -> None:
        cenas = [cena("frase", "foguete espacial")]
        cortes, avisos = plan_broll(
            cenas, library=[asset("gato_dormindo")], duration=30.0
        )
        assert cortes == []
        assert any("foguete" in a for a in avisos)

    def test_usa_a_transcricao_para_posicionar(self) -> None:
        cenas = [cena("aaaa bbbb", None), cena("cccc dddd", "bitcoin grafico")]
        cortes, avisos = plan_broll(
            cenas,
            library=[asset("bitcoin_grafico")],
            duration=20.0,
            transcript=fala("aaaa bbbb cccc dddd", 0.0, 20.0),
        )
        # A segunda cena começa na metade do texto, logo na metade do tempo.
        assert cortes[0].start == pytest.approx(10.0, abs=0.6)
        assert not any("transcrição" in a for a in avisos)

    def test_mais_segmentos_que_cenas_nao_trunca(self) -> None:
        """Bug real: o Whisper segmenta por pausa, não por cena do roteiro.

        Parear `segmento[i]` com `cena[i]` truncava a última cena no início do
        segundo segmento — numa cena só, a janela ia até 3,26s de um vídeo de
        4,88s, e todo o resto ficava sem cobertura possível.
        """
        from services.video.broll import _scene_windows

        uma_cena = [cena("frase inteira dita em duas partes", "bitcoin grafico")]
        transcript = FakeTranscript(
            segments=[
                FakeSegment("frase inteira", 0.0, 3.06),
                FakeSegment("dita em duas partes", 3.26, 4.44),
            ],
            words=[
                FakeWord(p, i * 0.6, i * 0.6 + 0.5)
                for i, p in enumerate("frase inteira dita em duas partes".split())
            ],
        )
        (inicio, fim), = _scene_windows(uma_cena, 4.88, transcript)
        assert inicio == 0.0
        assert fim == pytest.approx(4.88), "a janela da cena foi truncada"

    def test_janelas_cobrem_o_video_sem_buraco(self) -> None:
        from services.video.broll import _scene_windows

        cenas = [cena("aaa"), cena("bbbbbb"), cena("ccc")]
        janelas = _scene_windows(cenas, 30.0, fala("aaa bbbbbb ccc", 0.0, 30.0))
        assert janelas[0][0] == 0.0
        assert janelas[-1][1] == pytest.approx(30.0)
        for anterior, atual in zip(janelas, janelas[1:]):
            assert anterior[1] == pytest.approx(atual[0])

    def test_sem_transcricao_avisa(self) -> None:
        cenas = [cena("frase", "bitcoin grafico")]
        _, avisos = plan_broll(
            cenas, library=[asset("bitcoin_grafico")], duration=30.0
        )
        assert any("transcrição" in a for a in avisos)

    def test_duracao_zero_nao_estoura(self) -> None:
        cortes, _ = plan_broll(
            [cena("f", "bitcoin")], library=[asset("bitcoin_grafico")], duration=0.0
        )
        assert cortes == []


class TestIndiceCacheado:
    """Cada arquivo custa um subprocesso de `ffprobe`.

    A busca da página de Assets dispara uma reindexação por tecla digitada.
    Numa biblioteca de cinquenta arquivos, sem cache são cinquenta processos
    por caractere.
    """

    def _asset(self, directory, nome: str) -> None:
        from core.media import ffmpeg

        ffmpeg.run_ffmpeg(
            [
                "-f", "lavfi",
                "-i", "color=c=blue:size=64x64:rate=5:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(directory / nome),
            ]
        )

    def test_segunda_chamada_nao_reindexá(self, temp_root, monkeypatch) -> None:
        from core.media import ffmpeg
        from services.video import broll

        broll.clear_index_cache()
        pasta = temp_root / "data" / "assets" / "broll"
        pasta.mkdir(parents=True, exist_ok=True)
        self._asset(pasta, "um.mp4")
        self._asset(pasta, "dois.mp4")

        chamadas = {"n": 0}
        original = ffmpeg.probe

        def contando(*args, **kwargs):
            chamadas["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(broll.ffmpeg, "probe", contando)

        broll.index_broll()
        primeira = chamadas["n"]
        broll.index_broll()
        assert primeira == 2, "esperado um probe por arquivo"
        assert chamadas["n"] == primeira, "a segunda chamada reindexou"

    def test_arquivo_novo_invalida_o_cache(self, temp_root) -> None:
        from services.video import broll

        broll.clear_index_cache()
        pasta = temp_root / "data" / "assets" / "broll"
        pasta.mkdir(parents=True, exist_ok=True)
        self._asset(pasta, "um.mp4")
        assert len(broll.index_broll()) == 1

        self._asset(pasta, "dois.mp4")
        assert len(broll.index_broll()) == 2, "o cache não notou o arquivo novo"


class TestIdentificadorUnico:
    def test_mesmo_nome_base_com_extensoes_diferentes(self, temp_root) -> None:
        """Ids iguais fariam `plan_broll` escolher um e `compose` usar o outro."""
        from core.media import ffmpeg
        from services.video import broll

        broll.clear_index_cache()
        pasta = temp_root / "data" / "assets" / "broll"
        pasta.mkdir(parents=True, exist_ok=True)
        ffmpeg.run_ffmpeg(
            ["-f", "lavfi", "-i", "color=c=blue:size=64x64:rate=5:duration=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(pasta / "logo.mp4")]
        )
        ffmpeg.run_ffmpeg(
            ["-f", "lavfi", "-i", "color=c=red:size=64x64", "-frames:v", "1",
             str(pasta / "logo.png")]
        )

        biblioteca = broll.index_broll()
        ids = [a.id for a in biblioteca]
        assert len(ids) == len(set(ids)), f"ids duplicados: {ids}"

    def test_extensao_nao_vira_palavra_chave(self, temp_root) -> None:
        """Senão 'mp4' casaria com qualquer pedido que mencionasse formato."""
        from core.media import ffmpeg
        from services.video import broll

        broll.clear_index_cache()
        pasta = temp_root / "data" / "assets" / "broll"
        pasta.mkdir(parents=True, exist_ok=True)
        ffmpeg.run_ffmpeg(
            ["-f", "lavfi", "-i", "color=c=blue:size=64x64:rate=5:duration=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(pasta / "bitcoin.mp4")]
        )
        asset = broll.index_broll()[0]
        assert "mp4" not in asset.keywords
        assert "bitcoin" in asset.keywords
