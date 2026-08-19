"""Biblioteca local de roteiros.

O ponto sensível aqui não é o carregamento — é a trava de lacunas. Um roteiro
de notícia com `{{NOME_DA_LEI}}` não preenchido geraria um vídeo em que a sua
voz clonada fala "abre chaves nome da lei fecha chaves" em voz alta.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from services.llm.library import (
    NICHE_LABELS,
    LibraryError,
    get_library,
    library_dir,
)

NICHOS_ESPERADOS = {
    "politica",
    "noticia_politica",
    "noticia_tecnica",
    "saude_publica",
    "sociologia",
}


class TestCatalogo:
    def test_tem_cinquenta_roteiros(self) -> None:
        assert len(get_library()) == 50

    def test_cinco_nichos_com_dez_cada(self) -> None:
        contagem = get_library().niches()
        assert set(contagem) == NICHOS_ESPERADOS
        assert all(n == 10 for n in contagem.values()), contagem

    def test_todo_nicho_tem_rotulo(self) -> None:
        for nicho in get_library().niches():
            assert nicho in NICHE_LABELS

    def test_ids_unicos(self) -> None:
        ids = [s.id for s in get_library()]
        assert len(ids) == len(set(ids))

    def test_yaml_de_cada_nicho_e_valido(self) -> None:
        arquivos = list(library_dir().glob("*.yaml"))
        assert len(arquivos) == 5
        for arquivo in arquivos:
            dados = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
            assert dados.get("niche"), f"{arquivo.name} sem nicho"
            assert dados.get("scripts"), f"{arquivo.name} sem roteiros"


class TestConteudo:
    def test_todo_roteiro_tem_hook_e_cenas(self) -> None:
        for item in get_library():
            assert item.hook.strip(), f"{item.id} sem hook"
            assert len(item.scenes) >= 3, f"{item.id} tem poucas cenas"

    def test_duracao_cabe_num_reels(self) -> None:
        """Entre 20 e 75 segundos ao ritmo de fala em pt-BR.

        Modelo é medido com as lacunas preenchidas: contar `{{QUAL_IMPOSTO}}`
        como uma palavra subestima muito o texto que vai ser falado.
        """
        for item in get_library():
            if item.is_template:
                exemplo = {s: "uma expressão de exemplo" for s in item.slots}
                palavras = len(item.to_script(exemplo).full_text.split())
            else:
                palavras = item.word_count
            segundos = palavras / 2.6
            assert 20 <= segundos <= 75, (
                f"{item.id}: {segundos:.0f}s ({palavras} palavras)"
            )

    def test_hashtags_bem_formadas(self) -> None:
        for item in get_library():
            for tag in item.hashtags:
                assert tag.startswith("#"), f"{item.id}: {tag}"
                assert " " not in tag, f"{item.id}: {tag}"

    def test_saude_sempre_tem_disclaimer(self) -> None:
        """Saúde sem ressalva é o tipo de conteúdo que causa dano real."""
        for item in get_library().filter(niche="saude_publica"):
            assert item.disclaimer, f"{item.id} sem disclaimer"

    def test_nenhum_texto_com_markdown(self) -> None:
        """O texto é falado em voz alta; asterisco viraria ruído."""
        for item in get_library():
            for cena in item.scenes:
                assert "**" not in cena, f"{item.id}: markdown na fala"
                assert not re.search(r"\[.+?\]\(.+?\)", cena), f"{item.id}: link"


class TestLacunas:
    def test_noticia_politica_e_toda_modelo(self) -> None:
        """Notícia datada não pode vir com fato pronto — envelhece ou inventa."""
        for item in get_library().filter(niche="noticia_politica"):
            assert item.is_template, f"{item.id} deveria ser modelo"
            assert item.slots, f"{item.id} é modelo mas não tem lacuna"

    def test_perene_nao_tem_lacuna(self) -> None:
        for item in get_library().filter(templates=False):
            assert not item.slots, f"{item.id} não é modelo mas tem {item.slots}"

    def test_recusa_gerar_com_lacuna_vazia(self) -> None:
        modelos = get_library().filter(templates=True)
        assert modelos
        with pytest.raises(LibraryError, match="lacunas"):
            modelos[0].to_script()

    def test_recusa_com_preenchimento_parcial(self) -> None:
        item = get_library().filter(templates=True)[0]
        parcial = {item.slots[0]: "algum valor"}
        with pytest.raises(LibraryError):
            item.to_script(parcial)

    def test_valor_em_branco_nao_conta_como_preenchido(self) -> None:
        item = get_library().filter(templates=True)[0]
        vazio = {s: "   " for s in item.slots}
        with pytest.raises(LibraryError):
            item.to_script(vazio)

    def test_preenchido_gera_script_limpo(self) -> None:
        for item in get_library().filter(templates=True):
            valores = {s: f"exemplo de {s.lower()}" for s in item.slots}
            script = item.to_script(valores)
            assert "{{" not in script.full_text, f"{item.id} deixou lacuna"
            assert "}}" not in script.full_text


class TestConversao:
    def test_vira_script_do_pipeline(self) -> None:
        item = get_library().filter(niche="politica")[0]
        script = item.to_script()
        assert script.title
        assert script.scenes
        assert script.full_text.strip()
        assert script.model.startswith("biblioteca:")

    def test_tempos_de_cena_sao_crescentes(self) -> None:
        script = get_library().filter(niche="sociologia")[0].to_script()
        tempos = [c.start for c in script.scenes]
        assert tempos == sorted(tempos)
        assert tempos[0] == 0.0

    def test_todo_roteiro_perene_converte(self) -> None:
        for item in get_library().filter(templates=False):
            script = item.to_script()
            assert script.full_text.strip(), f"{item.id} virou script vazio"


class TestBusca:
    def test_filtra_por_nicho(self) -> None:
        itens = get_library().filter(niche="sociologia")
        assert len(itens) == 10
        assert all(i.niche == "sociologia" for i in itens)

    def test_busca_por_texto(self) -> None:
        assert get_library().filter(query="vacina")

    def test_busca_sem_resultado(self) -> None:
        assert get_library().filter(query="zzzznaoexiste") == []

    def test_separa_modelos_de_prontos(self) -> None:
        biblioteca = get_library()
        modelos = biblioteca.filter(templates=True)
        prontos = biblioteca.filter(templates=False)
        assert len(modelos) + len(prontos) == len(biblioteca)
        assert modelos and prontos

    def test_require_falha_com_mensagem_util(self) -> None:
        with pytest.raises(LibraryError, match="não existe"):
            get_library().require("id_inexistente")


class TestFuncionaOffline:
    def test_biblioteca_nao_importa_rede_nem_llm(self) -> None:
        """O ponto da biblioteca é funcionar sem Ollama e sem internet."""
        fonte = (
            Path(__file__).resolve().parents[2] / "services/llm/library.py"
        ).read_text(encoding="utf-8")
        for proibido in ("import httpx", "import requests", "from services.llm.ollama"):
            assert proibido not in fonte, f"library.py não deve depender de {proibido}"
