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
        id=nome,
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


class FakeTranscript:
    def __init__(self, segments):
        self.segments = segments


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
        assert achado.id == "bitcoin_grafico_queda"
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
        cenas = [cena("primeira", None), cena("segunda", "bitcoin grafico")]
        transcript = FakeTranscript(
            [FakeSegment("primeira", 0.0, 8.0), FakeSegment("segunda", 8.0, 20.0)]
        )
        cortes, avisos = plan_broll(
            cenas,
            library=[asset("bitcoin_grafico")],
            duration=20.0,
            transcript=transcript,
        )
        assert cortes[0].start == pytest.approx(8.0)
        assert not any("transcrição" in a for a in avisos)

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
