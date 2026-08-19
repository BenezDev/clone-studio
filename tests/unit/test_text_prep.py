"""Normalização de texto para TTS em pt-BR."""

from __future__ import annotations

import pytest

from core.audio.text_prep import (
    chunk_for_synthesis,
    normalize_for_tts,
    number_to_words,
    split_sentences,
)


class TestNumberToWords:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, "zero"),
            (1, "um"),
            (15, "quinze"),
            (21, "vinte e um"),
            (100, "cem"),
            (101, "cento e um"),
            (1000, "mil"),
            (1500, "mil e quinhentos"),
            (2026, "dois mil e vinte e seis"),
            (1_000_000, "um milhão"),
        ],
    )
    def test_conhecidos(self, value: int, expected: str) -> None:
        assert number_to_words(value) == expected

    def test_negativo(self) -> None:
        assert number_to_words(-5).startswith("menos ")


class TestNormalize:
    def test_dinheiro(self) -> None:
        assert "mil e quinhentos reais" in normalize_for_tts("R$ 1.500,00")

    def test_percentual(self) -> None:
        assert "por cento" in normalize_for_tts("cresceu 45%")

    def test_hora(self) -> None:
        resultado = normalize_for_tts("às 14:30")
        assert "quatorze horas" in resultado and "trinta minutos" in resultado

    def test_url_vira_fala(self) -> None:
        resultado = normalize_for_tts("acesse https://www.exemplo.com.br/curso")
        assert "http" not in resultado
        assert "ponto" in resultado

    def test_sigla_conhecida_e_soletrada(self) -> None:
        assert "a pê i" in normalize_for_tts("use a API")

    def test_sigla_sem_vogal_e_soletrada(self) -> None:
        assert "pê dê éfe" in normalize_for_tts("baixe o PDF")

    def test_caixa_alta_de_enfase_e_preservada(self) -> None:
        """Roteiros virais usam maiúsculas para ênfase — não são siglas."""
        resultado = normalize_for_tts("isso NUNCA funciona")
        assert "NUNCA" in resultado
        assert "ene u ene" not in resultado

    def test_markdown_removido(self) -> None:
        resultado = normalize_for_tts("isso é **muito** importante")
        assert "*" not in resultado
        assert "muito" in resultado

    def test_termo_ingles_adaptado(self) -> None:
        assert "deploi" in normalize_for_tts("fiz o deploy")

    def test_texto_vazio(self) -> None:
        assert normalize_for_tts("   ") == ""

    def test_idempotente_o_bastante(self) -> None:
        """Normalizar duas vezes não deve corromper o texto."""
        original = "A IA muda tudo em 2026."
        uma = normalize_for_tts(original)
        duas = normalize_for_tts(uma)
        assert duas == uma

    def test_versao_pontuada_nao_vira_inteiro(self) -> None:
        resultado = normalize_for_tts("versão 1.2.3")
        assert "um ponto dois ponto três" in resultado
        assert "cento e vinte e três" not in resultado

    def test_siglas_mistas_e_digitais(self) -> None:
        resultado = normalize_for_tts("SaaS B2B")
        assert "ésse a a ésse" in resultado
        assert "bê 2 bê" in resultado


class TestChunking:
    def test_frases_separadas(self) -> None:
        assert len(split_sentences("Um. Dois. Três.")) == 3

    def test_abreviacao_nao_quebra_frase(self) -> None:
        assert len(split_sentences("Falei com o Dr. Silva ontem.")) == 1

    def test_blocos_respeitam_limite(self) -> None:
        texto = " ".join(f"Frase número {i}." for i in range(30))
        blocos = chunk_for_synthesis(texto, max_chars=100)
        assert blocos
        assert all(len(b) <= 170 for b in blocos)

    def test_nada_se_perde(self) -> None:
        texto = "Primeira frase. Segunda frase. Terceira frase."
        blocos = chunk_for_synthesis(texto, max_chars=20)
        assert "Terceira" in " ".join(blocos)

    def test_frase_longa_sem_virgula_e_quebrada(self) -> None:
        texto = " ".join(["palavra"] * 100)
        blocos = chunk_for_synthesis(texto, max_chars=80)
        assert len(blocos) > 1
        assert all(len(bloco) <= 80 for bloco in blocos)
        assert " ".join(blocos).split() == texto.split()
