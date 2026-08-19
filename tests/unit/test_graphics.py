r"""Animações gráficas em ASS.

Dois riscos dominam este módulo e os testes existem por causa deles.

O primeiro é o libass **descartar linha malformada em silêncio**: nenhum erro,
nenhum aviso, só um gráfico que não aparece. Por isso a integração renderiza de
verdade e confere pixel.

O segundo é o Python comer as tags: ``\a`` é BEL, ``\b`` é backspace e ``\f`` é
formfeed, então ``\an5``, ``\bord`` e ``\fscx`` viram bytes de controle se
alguém tirar o ``r`` de uma string.
"""

from __future__ import annotations

import pytest

from services.video.captions import PRESETS
from services.video.graphics import (
    GraphicsPlan,
    Overlay,
    _color,
    _fit,
    build_graphics_ass,
    plan_overlays,
)

PRESET = PRESETS["hormozi"]


def montar(plan: GraphicsPlan, width: int = 1080, height: int = 1920) -> str:
    return build_graphics_ass(plan, width=width, height=height, preset=PRESET)


class TestPlano:
    def test_barra_de_progresso_cobre_o_video_inteiro(self) -> None:
        plano = plan_overlays(12.0)
        barra = next(o for o in plano.overlays if o.kind == "progress")
        assert (barra.start, barra.end) == (0.0, 12.0)

    def test_nao_inventa_texto(self) -> None:
        """Card com texto genérico é pior que card nenhum."""
        plano = plan_overlays(12.0, hook="", cta="", handle="")
        assert {o.kind for o in plano.overlays} == {"progress"}

    def test_hook_nao_ocupa_o_video_todo(self) -> None:
        plano = plan_overlays(4.0, hook="Isto muda tudo")
        hook = next(o for o in plano.overlays if o.kind == "hook")
        assert hook.duration <= 4.0 * 0.4 + 0.01

    def test_lower_third_entra_depois_do_hook(self) -> None:
        """Os dois ao mesmo tempo disputam a atenção."""
        plano = plan_overlays(20.0, hook="Olha isso", handle="@benez")
        hook = next(o for o in plano.overlays if o.kind == "hook")
        faixa = next(o for o in plano.overlays if o.kind == "lower_third")
        assert faixa.start >= hook.end

    def test_end_card_termina_com_o_video(self) -> None:
        plano = plan_overlays(20.0, cta="Segue lá")
        card = next(o for o in plano.overlays if o.kind == "end_card")
        assert card.end == pytest.approx(20.0)

    def test_video_curto_avisa_em_vez_de_espremer(self) -> None:
        plano = plan_overlays(1.0, hook="Olha", cta="Segue")
        assert plano.warnings
        assert not any(o.kind == "end_card" for o in plano.overlays)

    def test_duracao_zero_nao_estoura(self) -> None:
        assert plan_overlays(0.0, hook="x").overlays == []


class TestQuebraDeTexto:
    def test_escapa_antes_de_quebrar(self) -> None:
        r"""Na ordem inversa, o próprio ``\N`` aparecia escrito no vídeo."""
        texto = _fit("uma frase bem longa que precisa quebrar em varias linhas",
                     width=1080, font_size=111)
        assert r"\N" in texto
        assert "＼N" not in texto, "a quebra foi escapada e virará texto visível"

    def test_quebra_acompanha_o_tamanho_da_fonte(self) -> None:
        """`WrapStyle: 2` desliga a quebra automática: se a conta errar, estoura."""
        frase = "palavra " * 20
        curta = _fit(frase, width=1080, font_size=40)
        larga = _fit(frase, width=1080, font_size=140)
        assert larga.count(r"\N") > curta.count(r"\N")

    def test_linha_cabe_na_largura_util(self) -> None:
        texto = _fit("AGORA DÁ PARA GRAVAR SUA VOZ E SEU ROSTO", width=1080, font_size=111)
        for linha in texto.split(r"\N"):
            assert len(linha) * 111 * 0.54 <= 1080

    def test_chave_do_ass_e_neutralizada(self) -> None:
        """`{` abriria um bloco de tags e comeria o resto da frase."""
        assert "{" not in _fit("texto {com chave}", width=1080, font_size=60)


class TestCor:
    def test_converte_estilo_para_tag(self) -> None:
        """São formatos diferentes e o libass não reclama do errado."""
        assert _color("&H0000E5FF") == "&H00E5FF&"

    def test_descarta_o_alfa(self) -> None:
        assert _color("&HA0123456") == "&H123456&"


class TestAss:
    def _plano_cheio(self) -> GraphicsPlan:
        return plan_overlays(12.0, hook="Isto muda tudo", cta="Segue", handle="@benez")

    def test_sem_overlay_nao_gera_arquivo(self) -> None:
        """Arquivo vazio custaria um passo de libass por nada."""
        assert montar(GraphicsPlan(duration=10.0)) is None

    def test_um_evento_por_elemento_no_minimo(self) -> None:
        ass = montar(self._plano_cheio())
        eventos = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert len(eventos) >= 4

    def test_sem_caractere_de_controle(self) -> None:
        r"""Sem raw string, ``\a`` e ``\b`` viram BEL e backspace."""
        ass = montar(self._plano_cheio())
        for proibido in ("\x07", "\x08", "\x0c", "\x0b"):
            assert proibido not in ass, "tag ASS virou byte de controle"

    def test_tags_chegaram_inteiras(self) -> None:
        ass = montar(self._plano_cheio())
        for tag in (r"\an5", r"\bord0", r"\fscx", r"\pos(", r"\t(", r"\p1"):
            assert tag in ass, f"{tag} sumiu"

    def test_resolucao_declarada_bate(self) -> None:
        ass = montar(self._plano_cheio(), width=540, height=960)
        assert "PlayResX: 540" in ass
        assert "PlayResY: 960" in ass

    def test_overlay_desconhecido_avisa_e_nao_quebra(self) -> None:
        plano = GraphicsPlan(
            duration=5.0, overlays=[Overlay("inexistente", 0.0, 1.0)]
        )
        assert montar(plano) is None
        assert any("inexistente" in a for a in plano.warnings)
