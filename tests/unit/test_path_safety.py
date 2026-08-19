"""Validação de identificadores que viram nome de pasta.

Bug real: o validador exigia primeiro caractere alfanumérico, então o perfil
de voz `_smoke` — criado pelo próprio `voice-enroll` — era aceito no cadastro
e recusado no render. O usuário só descobria ao gerar o vídeo.

Os casos de recusa aqui não são teoria: um `id` chega pela API e viraria
caminho no disco.
"""

from __future__ import annotations

import pytest

from core.storage.paths import safe_path_component


class TestAceita:
    @pytest.mark.parametrize(
        "valor",
        ["me", "_smoke", "perfil-1", "v1.2", "A9", "_a", "meu_perfil_2026"],
    )
    def test_identificador_valido(self, valor: str) -> None:
        assert safe_path_component(valor) == valor

    def test_underscore_inicial(self) -> None:
        """Foi o caso do bug: `_smoke` é criado pelo sistema e precisa passar."""
        assert safe_path_component("_smoke") == "_smoke"


class TestRecusa:
    @pytest.mark.parametrize(
        "valor,motivo",
        [
            ("..", "travessia de diretório"),
            (".", "diretório atual"),
            ("../etc/passwd", "travessia"),
            ("a/b", "separador de caminho"),
            ("a\\b", "separador do Windows"),
            (".oculto", "ponto inicial cria arquivo oculto"),
            ("con", "reservado no Windows"),
            ("nul", "reservado no Windows"),
            ("com1", "reservado no Windows"),
            ("lpt9", "reservado no Windows"),
            ("PRN.txt", "reservado mesmo com extensão"),
            ("nome com espaço", "espaço"),
            ("fim.", "ponto final é removido pelo Windows"),
            ("fim ", "espaço final é removido pelo Windows"),
            ("", "vazio"),
            ("x" * 200, "longo demais"),
        ],
    )
    def test_identificador_perigoso(self, valor: str, motivo: str) -> None:
        with pytest.raises(ValueError):
            safe_path_component(valor)

    def test_tipo_errado(self) -> None:
        with pytest.raises(ValueError):
            safe_path_component(None)  # type: ignore[arg-type]


class TestCoerenciaComOSistema:
    def test_perfis_existentes_passam_na_validacao(self) -> None:
        """Todo perfil já gravado no disco precisa continuar utilizável."""
        from services.tts.qwen3_tts import list_voice_profiles

        for perfil in list_voice_profiles():
            assert safe_path_component(perfil.id) == perfil.id, (
                f"perfil '{perfil.id}' existe mas seria recusado no render"
            )

    def test_projetos_existentes_passam(self) -> None:
        from core.storage.project import list_projects

        for projeto in list_projects():
            assert safe_path_component(projeto.id) == projeto.id
