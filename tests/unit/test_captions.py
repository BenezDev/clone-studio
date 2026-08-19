"""Geração de legendas ASS/SRT."""

from __future__ import annotations

import pytest

from services.transcription.base import Segment, Transcript, Word
from services.video.captions import (
    PRESETS,
    build_ass,
    build_srt,
    group_words,
    write_captions,
)


def make_transcript(words: list[tuple[str, float, float]]) -> Transcript:
    parsed = [Word(text=t, start=s, end=e) for t, s, e in words]
    return Transcript(
        segments=[
            Segment(
                text=" ".join(w.text for w in parsed),
                start=parsed[0].start,
                end=parsed[-1].end,
                words=parsed,
            )
        ],
        language="pt",
        duration=parsed[-1].end,
        engine="teste",
        model="teste",
    )


FRASE = [
    ("A", 0.0, 0.2),
    ("inteligência", 0.2, 0.8),
    ("artificial", 0.8, 1.4),
    ("não", 1.4, 1.6),
    ("vai", 1.6, 1.8),
    ("substituir", 1.8, 2.5),
    ("programadores.", 2.5, 3.2),
]


class TestAgrupamento:
    def test_respeita_palavras_por_cue(self) -> None:
        cues = group_words([Word(t, s, e) for t, s, e in FRASE], words_per_cue=3)
        assert all(len(c.words) <= 3 for c in cues)

    def test_pontuacao_forte_fecha_o_cue(self) -> None:
        cues = group_words([Word(t, s, e) for t, s, e in FRASE], words_per_cue=10)
        assert cues[-1].text.endswith(".")

    def test_pausa_longa_quebra(self) -> None:
        palavras = [
            Word("primeira", 0.0, 0.5),
            Word("parte", 0.5, 1.0),
            Word("segunda", 3.0, 3.5),  # 2s de pausa
        ]
        cues = group_words(palavras, words_per_cue=10)
        assert len(cues) >= 2

    def test_nenhuma_palavra_se_perde(self) -> None:
        palavras = [Word(t, s, e) for t, s, e in FRASE]
        cues = group_words(palavras, words_per_cue=2)
        total = sum(len(c.words) for c in cues)
        assert total == len(palavras)


class TestASS:
    def test_estrutura_minima(self) -> None:
        ass = build_ass(
            make_transcript(FRASE),
            preset=PRESETS["hormozi"],
            width=1080,
            height=1920,
        )
        assert "[Script Info]" in ass
        assert "PlayResX: 1080" in ass
        assert "PlayResY: 1920" in ass
        assert "[V4+ Styles]" in ass
        assert "[Events]" in ass
        assert "Dialogue:" in ass

    def test_um_evento_por_palavra_ativa(self) -> None:
        """O destaque palavra a palavra vira um Dialogue por estado."""
        ass = build_ass(
            make_transcript(FRASE),
            preset=PRESETS["hormozi"],
            width=1080,
            height=1920,
        )
        eventos = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert len(eventos) == len(FRASE)

    def test_eventos_nao_se_sobrepoem(self) -> None:
        ass = build_ass(
            make_transcript(FRASE),
            preset=PRESETS["clean"],
            width=1080,
            height=1920,
        )
        tempos = []
        for linha in ass.splitlines():
            if not linha.startswith("Dialogue:"):
                continue
            partes = linha.split(",")
            tempos.append((partes[1], partes[2]))
        assert all(inicio < fim for inicio, fim in tempos)

    def test_caixa_alta_do_preset(self) -> None:
        ass = build_ass(
            make_transcript(FRASE),
            preset=PRESETS["hormozi"],
            width=1080,
            height=1920,
        )
        assert "INTELIGÊNCIA" in ass

    def test_tamanho_escala_com_a_altura(self) -> None:
        pequeno = build_ass(
            make_transcript(FRASE), preset=PRESETS["clean"], width=540, height=960
        )
        grande = build_ass(
            make_transcript(FRASE), preset=PRESETS["clean"], width=1080, height=1920
        )

        def fonte(ass: str) -> int:
            linha = next(l for l in ass.splitlines() if l.startswith("Style:"))
            return int(linha.split(",")[2])

        assert fonte(grande) > fonte(pequeno)

    def test_sem_timestamps_falha_claramente(self) -> None:
        vazio = Transcript(
            segments=[Segment(text="oi", start=0, end=1, words=[])],
            language="pt",
            duration=1,
            engine="t",
            model="t",
        )
        with pytest.raises(ValueError, match="timestamps"):
            build_ass(vazio, preset=PRESETS["clean"], width=1080, height=1920)

    def test_todos_os_presets_geram(self) -> None:
        for key, preset in PRESETS.items():
            ass = build_ass(
                make_transcript(FRASE), preset=preset, width=1080, height=1920
            )
            assert "Dialogue:" in ass, f"preset {key} não gerou eventos"


class TestSRT:
    def test_formato(self) -> None:
        srt = build_srt(make_transcript(FRASE))
        assert "1\n" in srt
        assert " --> " in srt
        assert "00:00:00," in srt


class TestEscrita:
    def test_gera_os_dois_arquivos(self, tmp_path) -> None:
        arquivos = write_captions(
            make_transcript(FRASE), tmp_path, preset_key="minimal"
        )
        assert arquivos["ass"].exists()
        assert arquivos["srt"].exists()
        assert arquivos["ass"].stat().st_size > 0

    def test_preset_desconhecido(self, tmp_path) -> None:
        with pytest.raises(KeyError):
            write_captions(
                make_transcript(FRASE), tmp_path, preset_key="inexistente"
            )
