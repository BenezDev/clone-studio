"""Auto editor: planejamento da EDL e tradução para filtro.

O valor destes testes está nas regras que produzem edição que *não* parece
automática. Um corte 200 ms depois do anterior, ou dois enquadramentos seguidos
no mesmo zoom, é o que denuncia a máquina.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from services.video.editor import (
    EDIT_PRESETS,
    EditDecisionList,
    Shot,
    build_zoom_filter,
    plan_edit,
)


@dataclass
class FakeWord:
    text: str
    start: float
    end: float


@dataclass
class FakeSegment:
    text: str
    start: float
    end: float


class FakeTranscript:
    def __init__(self, segments, words):
        self.segments = segments
        self.words = words


def transcricao(cortes: list[float], palavras: list[tuple[str, float]] = []):
    segmentos = [FakeSegment(f"f{i}", t, t + 1) for i, t in enumerate(cortes)]
    return FakeTranscript(segmentos, [FakeWord(w, t, t + 0.3) for w, t in palavras])


class TestRitmo:
    def test_respeita_a_distancia_minima(self) -> None:
        """Cortes colados leem como tremor, não como edição."""
        edl = plan_edit(
            20.0,
            transcript=transcricao([0.4, 0.9, 1.5, 2.1, 10.0]),
            preset="sutil",
            min_shot_seconds=3.0,
            max_shot_seconds=30.0,
        )
        inicios = [s.start for s in edl.shots]
        for anterior, atual in zip(inicios, inicios[1:]):
            assert atual - anterior >= 3.0

    def test_quebra_enquadramento_longo_demais(self) -> None:
        edl = plan_edit(
            30.0, transcript=transcricao([]), preset="sutil", max_shot_seconds=8.0
        )
        assert all(s.duration <= 8.01 for s in edl.shots)
        assert any(s.reason == "respiro" for s in edl.shots)

    def test_quebra_em_partes_iguais(self) -> None:
        """Dividir por 8 deixaria um resto curto no fim; partes iguais não."""
        edl = plan_edit(
            30.0, transcript=transcricao([]), preset="sutil", max_shot_seconds=8.0
        )
        duracoes = [round(s.duration, 2) for s in edl.shots]
        assert max(duracoes) - min(duracoes) < 0.01

    def test_zoom_alterna_entre_enquadramentos(self) -> None:
        """Dois cortes seguidos no mesmo zoom não seriam corte nenhum."""
        edl = plan_edit(
            30.0,
            transcript=transcricao([6.0, 12.0, 18.0]),
            preset="sutil",
            min_shot_seconds=2.0,
            punch_in_strength=1.1,
        )
        for anterior, atual in zip(edl.shots, edl.shots[1:]):
            assert anterior.zoom != atual.zoom

    def test_cobre_o_video_inteiro_sem_buraco(self) -> None:
        edl = plan_edit(25.0, transcript=transcricao([5.0, 11.0]), preset="sutil")
        assert edl.shots[0].start == 0.0
        assert edl.shots[-1].end == pytest.approx(25.0)
        for anterior, atual in zip(edl.shots, edl.shots[1:]):
            assert anterior.end == pytest.approx(atual.start)


class TestPresets:
    def test_clean_nao_move_nada(self) -> None:
        """É o padrão histórico do projeto: ligar o editor não pode surpreender."""
        edl = plan_edit(30.0, transcript=transcricao([5.0, 10.0]), preset="clean")
        assert not edl.has_movement
        assert build_zoom_filter(edl, width=1080, height=1920, fps=30) is None

    def test_dinamico_usa_enfase_do_roteiro(self) -> None:
        edl = plan_edit(
            30.0,
            transcript=transcricao([], [("nunca", 9.0)]),
            emphasis=["Nunca"],
            preset="dinamico",
            min_shot_seconds=2.0,
            max_shot_seconds=60.0,
        )
        assert any("ênfase" in s.reason for s in edl.shots)

    def test_sutil_ignora_enfase(self) -> None:
        edl = plan_edit(
            30.0,
            transcript=transcricao([], [("nunca", 9.0)]),
            emphasis=["nunca"],
            preset="sutil",
            max_shot_seconds=60.0,
        )
        assert not any("ênfase" in s.reason for s in edl.shots)

    def test_preset_desconhecido_avisa_e_nao_quebra(self) -> None:
        edl = plan_edit(10.0, transcript=transcricao([]), preset="inventado")
        assert any("inventado" in a for a in edl.warnings)


class TestDegradacaoHonesta:
    def test_sem_transcricao_avisa(self) -> None:
        """Sem legendas não há frase; o usuário precisa saber o que perdeu."""
        edl = plan_edit(30.0, transcript=None, preset="sutil")
        assert any("transcrição" in a for a in edl.warnings)

    def test_video_curto_vira_um_enquadramento_so(self) -> None:
        edl = plan_edit(1.5, transcript=transcricao([]), preset="dinamico")
        assert len(edl.shots) == 1
        assert any("curto" in a for a in edl.warnings)

    def test_duracao_zero_nao_estoura(self) -> None:
        edl = plan_edit(0.0, transcript=transcricao([]), preset="dinamico")
        assert edl.shots == []


class TestFiltro:
    def _edl(self) -> EditDecisionList:
        return EditDecisionList(
            duration=9.0,
            preset="sutil",
            shots=[
                Shot(0.0, 3.0, 1.0, "abertura"),
                Shot(3.0, 6.0, 1.1, "frase nova"),
                Shot(6.0, 9.0, 1.0, "frase nova"),
            ],
        )

    def test_fps_e_obrigatorio_na_expressao(self) -> None:
        """O zoompan assume 25 quando não recebe fps: um render 30 cairia para 25."""
        filtro = build_zoom_filter(self._edl(), width=1080, height=1920, fps=30)
        assert "fps=30" in filtro

    def test_virgula_escapada(self) -> None:
        """Vírgula crua separaria filtros e quebraria a cadeia inteira."""
        filtro = build_zoom_filter(self._edl(), width=1080, height=1920, fps=30)
        assert "clip(" in filtro
        expressao = filtro.split("z='")[1].split("'")[0]
        assert "," in expressao, "o teste precisa de vírgulas para valer algo"
        for posicao, caractere in enumerate(expressao):
            if caractere == ",":
                assert expressao[posicao - 1] == "\\", (
                    f"vírgula crua na posição {posicao}: quebraria a cadeia"
                )

    def test_resolucao_de_saida_fixa(self) -> None:
        filtro = build_zoom_filter(self._edl(), width=1080, height=1920, fps=30)
        assert "s=1080x1920" in filtro

    def test_ancora_acima_do_centro(self) -> None:
        """Ancorar no centro puxa o quadro para o tronco e corta a testa."""
        filtro = build_zoom_filter(self._edl(), width=1080, height=1920, fps=30)
        assert "0.420*ih*(1-1/zoom)" in filtro

    def test_expressao_cresce_linear_e_nao_aninha(self) -> None:
        """`if` encaixado por corte estouraria o parser num vídeo longo."""
        muitos = EditDecisionList(
            duration=100.0,
            preset="sutil",
            shots=[
                Shot(i * 2.0, i * 2.0 + 2.0, 1.0 if i % 2 == 0 else 1.1, "x")
                for i in range(50)
            ],
        )
        filtro = build_zoom_filter(muitos, width=1080, height=1920, fps=30)
        assert filtro.count("clip(") == 49
        assert "if(" not in filtro


class TestPersistencia:
    def test_ida_e_volta(self, tmp_path) -> None:
        original = plan_edit(
            20.0, transcript=transcricao([5.0, 11.0]), preset="sutil"
        )
        caminho = original.save(tmp_path / "edit.json")
        recarregada = EditDecisionList.load(caminho)
        assert recarregada.preset == original.preset
        assert [s.start for s in recarregada.shots] == [
            s.start for s in original.shots
        ]
        assert recarregada.has_movement == original.has_movement


def test_presets_declarados_tem_rotulo() -> None:
    """A UI mostra o rótulo; preset sem rótulo apareceria vazio na tela."""
    for chave, valor in EDIT_PRESETS.items():
        assert valor.get("label"), f"preset {chave} sem rótulo"
