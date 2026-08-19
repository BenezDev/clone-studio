"""Testes do núcleo: config, hardware, licenças, cache, projeto e templates."""

from __future__ import annotations

import json

import pytest

from core.hardware.detect import classify_profile, detect
from core.licensing.registry import CommercialStatus, get_registry
from core.storage.cache import get_cache, hash_inputs
from core.storage.paths import get_paths


class TestHardware:
    def test_detecta_o_essencial(self) -> None:
        report = detect()
        assert report.os_name
        assert report.cpu_threads and report.cpu_threads > 0
        assert report.ram_total_mb and report.ram_total_mb > 0
        assert report.profile in {
            "CPU_ONLY", "LOW_VRAM", "MID_VRAM", "HIGH_VRAM", "EXTREME"
        }

    def test_sem_gpu_e_cpu_only(self) -> None:
        report = detect()
        report.gpus = []
        profile, motivo = classify_profile(report)
        assert profile == "CPU_ONLY"
        assert motivo

    def test_serializa_e_volta(self, tmp_path) -> None:
        from core.hardware.detect import load_report, save_report

        original = detect()
        destino = tmp_path / "hardware.json"
        save_report(original, destino)
        recarregado = load_report(destino)
        assert recarregado is not None
        assert recarregado.profile == original.profile
        assert len(recarregado.tools) == len(original.tools)


class TestConfig:
    def test_carrega_padrao(self, temp_root) -> None:
        from core.config.loader import load_settings

        settings = load_settings(reload=True)
        assert settings.app.host == "127.0.0.1"
        assert settings.video.width == 1080
        assert settings.video.height == 1920

    def test_bind_externo_e_recusado(self) -> None:
        from core.config.schema import AppConfig

        with pytest.raises(ValueError, match="local-first"):
            AppConfig(host="0.0.0.0")

    def test_telemetria_nao_pode_ser_ligada(self) -> None:
        from core.config.schema import PrivacyConfig

        with pytest.raises(ValueError, match="desativad"):
            PrivacyConfig(telemetry=True)

    def test_merge_recursivo(self) -> None:
        from core.config.loader import deep_merge

        base = {"a": {"b": 1, "c": 2}, "d": 3}
        over = {"a": {"c": 9}}
        assert deep_merge(base, over) == {"a": {"b": 1, "c": 9}, "d": 3}

    def test_sobreposicao_do_usuario(self, temp_root) -> None:
        from core.config.loader import save_user_overrides

        settings = save_user_overrides({"video": {"fps": 24}})
        assert settings.video.fps == 24
        assert (temp_root / "config" / "local.yaml").exists()

    def test_sobreposicao_invalida_nao_grava(self, temp_root) -> None:
        from core.config.loader import save_user_overrides

        with pytest.raises(ValueError):
            save_user_overrides({"app": {"host": "0.0.0.0"}})
        assert not (temp_root / "config" / "local.yaml").exists()

    def test_modelo_por_perfil(self, temp_root) -> None:
        from core.config.loader import load_settings

        tts = load_settings(reload=True).tts
        assert "0.6B" in tts.resolve_model("CPU_ONLY")
        assert "1.7B" in tts.resolve_model("HIGH_VRAM")


class TestRegistry:
    def test_carrega(self, temp_root) -> None:
        registry = get_registry()
        assert len(registry) > 0
        assert registry.get("qwen3_tts_0_6b_base") is not None

    def test_licencas_declaradas(self, temp_root) -> None:
        for entry in get_registry():
            assert entry.license
            assert entry.license != "desconhecida", (
                f"{entry.key} está sem licença declarada"
            )
            assert entry.source, f"{entry.key} está sem fonte oficial"

    def test_qwen_e_apache(self, temp_root) -> None:
        entry = get_registry().require("qwen3_tts_0_6b_base")
        assert entry.license == "Apache-2.0"
        assert entry.commercial is CommercialStatus.ALLOWED
        assert entry.commercial_ok()

    def test_dependencia_pendente_bloqueia_comercial(self, temp_root) -> None:
        """O MuseTalk é MIT, mas o DWPose precisa de verificação."""
        entry = get_registry().require("musetalk_15")
        assert entry.commercial is CommercialStatus.ALLOWED
        assert not entry.commercial_ok()
        assert any("DWPose" in b or "dwpose" in b for b in entry.commercial_blockers())

    def test_recomendacao_comercial_exclui_pendentes(self, temp_root) -> None:
        registry = get_registry()
        chaves = {e.key for e in registry.recommended_for("lipsync", "commercial")}
        assert "musetalk_15" not in chaves
        pessoais = {e.key for e in registry.recommended_for("lipsync", "personal")}
        assert "musetalk_15" in pessoais

    def test_hardware_incompativel(self, temp_root) -> None:
        registry = get_registry()
        report = detect()
        report.gpus = []
        report.ram_total_mb = 8000
        verdict = registry.check_hardware(registry.require("wan22_ti2v_5b"), report)
        assert not verdict.compatible
        assert verdict.reasons

    def test_musetalk_tem_componentes(self, temp_root) -> None:
        entry = get_registry().require("musetalk_15")
        assert len(entry.components) >= 4
        assert entry.code_revision, "o commit do código precisa estar fixado"
        # O peso baixado por URL precisa estar declarado, senão o pipeline
        # dependeria de internet no primeiro lip-sync.
        assert any("s3fd" in u.filename for u in entry.url_components)


class TestCache:
    def test_mesma_entrada_mesma_chave(self, temp_root, tmp_path) -> None:
        arquivo = tmp_path / "a.txt"
        arquivo.write_text("conteúdo")
        assert hash_inputs(files=[arquivo], text="x") == hash_inputs(
            files=[arquivo], text="x"
        )

    def test_parametro_diferente_muda_chave(self, temp_root, tmp_path) -> None:
        arquivo = tmp_path / "a.txt"
        arquivo.write_text("conteúdo")
        a = hash_inputs(files=[arquivo], params={"speed": 1.0})
        b = hash_inputs(files=[arquivo], params={"speed": 1.1})
        assert a != b

    def test_conteudo_diferente_muda_chave(self, temp_root, tmp_path) -> None:
        arquivo = tmp_path / "a.txt"
        arquivo.write_text("um")
        primeiro = hash_inputs(files=[arquivo])
        arquivo.write_text("outro conteúdo diferente")
        assert hash_inputs(files=[arquivo]) != primeiro

    def test_ciclo_de_vida(self, temp_root) -> None:
        cache = get_cache("teste")
        entry = cache.entry(text="abc")
        assert not entry.hit
        entry.directory.mkdir(parents=True, exist_ok=True)
        entry.file("saida.txt").write_text("ok")
        entry.commit({"n": 1})
        assert cache.entry(text="abc").hit
        assert cache.entry(text="abc").load_metadata()["n"] == 1


class TestProject:
    def test_cria_e_recarrega(self, temp_root) -> None:
        from core.storage.project import Project, create_project

        project = create_project("Programadores e vendas", "por que vender importa")
        assert project.directory.exists()
        assert (project.directory / "project.json").exists()

        recarregado = Project.load(project.id)
        assert recarregado.title == project.title
        assert recarregado.idea == project.idea

    def test_id_datado_e_unico(self, temp_root) -> None:
        from core.storage.project import create_project

        a = create_project("Mesmo título")
        b = create_project("Mesmo título")
        assert a.id != b.id

    def test_resume_exige_arquivo_presente(self, temp_root) -> None:
        from core.storage.project import create_project

        project = create_project("teste")
        saida = project.stages_dir / "voz.wav"
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_bytes(b"x")

        project.record_stage("tts", output=saida)
        assert project.stage_completed("tts")

        # Arquivo apagado: a etapa não pode contar como concluída.
        saida.unlink()
        assert not project.stage_completed("tts")

    def test_invalidacao_em_cascata(self, temp_root) -> None:
        from core.pipeline.stages import STAGE_ORDER
        from core.storage.project import create_project

        project = create_project("teste")
        for name in STAGE_ORDER:
            project.record_stage(name)
        project.invalidate_from("tts", STAGE_ORDER)

        assert "prepare" in project.stages
        assert "tts" not in project.stages
        assert "lipsync" not in project.stages
        assert "render" not in project.stages

    def test_formato_futuro_e_recusado(self, temp_root) -> None:
        from core.storage.project import Project, create_project

        project = create_project("teste")
        arquivo = project.directory / "project.json"
        data = json.loads(arquivo.read_text())
        data["format_version"] = 999
        arquivo.write_text(json.dumps(data))

        with pytest.raises(ValueError, match="versão mais nova"):
            Project.load(project.id)


class TestTemplates:
    def test_plano_de_composicao(self, temp_root) -> None:
        from services.templates.library import SelectionCriteria, plan_composition
        from services.templates.models import TemplateMetadata

        templates = [
            TemplateMetadata(id="a", duration=30.0, video_path="a.mp4",
                             width=1080, height=1920),
            TemplateMetadata(id="b", duration=20.0, video_path="b.mp4",
                             width=1080, height=1920),
        ]
        plano = plan_composition(15.0, SelectionCriteria(), templates)
        assert plano.strategy == "single"
        assert len(plano.segments) == 1
        assert abs(plano.total_duration - 15.0) < 0.1

    def test_composicao_multipla(self, temp_root) -> None:
        from services.templates.library import SelectionCriteria, plan_composition
        from services.templates.models import TemplateMetadata

        templates = [
            TemplateMetadata(id="a", duration=10.0, video_path="a.mp4",
                             width=1080, height=1920),
            TemplateMetadata(id="b", duration=10.0, video_path="b.mp4",
                             width=1080, height=1920),
        ]
        plano = plan_composition(17.0, SelectionCriteria(), templates)
        assert plano.strategy == "multi"
        assert len(plano.segments) >= 2
        assert abs(plano.total_duration - 17.0) < 0.2

    def test_material_insuficiente_avisa(self, temp_root) -> None:
        from services.templates.library import SelectionCriteria, plan_composition
        from services.templates.models import TemplateMetadata

        templates = [
            TemplateMetadata(id="a", duration=8.0, video_path="a.mp4",
                             width=1080, height=1920),
        ]
        plano = plan_composition(40.0, SelectionCriteria(), templates)
        assert plano.strategy == "revisit"
        assert plano.warnings

    def test_sem_template_falha_claramente(self, temp_root) -> None:
        from services.templates.library import (
            SelectionCriteria,
            TemplateError,
            plan_composition,
        )

        with pytest.raises(TemplateError, match="template"):
            plan_composition(10.0, SelectionCriteria(), [])

    def test_vertical_pontua_mais(self, temp_root) -> None:
        from services.templates.library import SelectionCriteria, score_template
        from services.templates.models import TemplateMetadata

        vertical = TemplateMetadata(id="v", duration=30, width=1080, height=1920,
                                    orientation="vertical")
        horizontal = TemplateMetadata(id="h", duration=30, width=1920, height=1080,
                                      orientation="horizontal")
        criteria = SelectionCriteria()
        assert score_template(vertical, criteria) > score_template(horizontal, criteria)

    def test_energia_influencia_a_escolha(self, temp_root) -> None:
        from services.templates.library import SelectionCriteria, choose_template
        from services.templates.models import TemplateMetadata

        calmo = TemplateMetadata(id="calmo", duration=30, energy="low",
                                 gestures="low", width=1080, height=1920)
        agitado = TemplateMetadata(id="agitado", duration=30, energy="high",
                                   gestures="high", width=1080, height=1920)
        escolha = choose_template(
            SelectionCriteria(energy="high", gestures="high"), [calmo, agitado]
        )
        assert escolha.id == "agitado"


class TestLipSyncCacheKey:
    """O cache de preprocessamento do MuseTalk não pode depender dos bytes do
    vídeo composto: encoders não são determinísticos, e o passo custa ~5 s por
    frame."""

    def _engine(self):
        from services.lipsync.musetalk import MuseTalkEngine

        return MuseTalkEngine()

    def _seed(self, duration: float = 5.0) -> dict:
        return {
            "fps": 25,
            "segments": [
                {
                    "template_id": "t1",
                    "content": "abc123",
                    "start": 0.35,
                    "duration": duration,
                }
            ],
        }

    def test_mesma_composicao_mesma_chave(self, temp_root, tmp_path) -> None:
        engine = self._engine()
        config = engine._default_config()
        composto = tmp_path / "composto.mp4"

        a = engine._template_cache_dir(composto, config, self._seed())
        b = engine._template_cache_dir(composto, config, self._seed())
        assert a == b

    def test_bytes_do_composto_nao_influenciam(self, temp_root, tmp_path) -> None:
        """Dois arquivos com o MESMO plano e bytes diferentes -> mesma chave."""
        engine = self._engine()
        config = engine._default_config()

        primeiro = tmp_path / "composto.mp4"
        primeiro.write_bytes(b"encode 1")
        chave_a = engine._template_cache_dir(primeiro, config, self._seed())

        primeiro.write_bytes(b"encode 2, bytes completamente diferentes")
        chave_b = engine._template_cache_dir(primeiro, config, self._seed())

        assert chave_a == chave_b

    def test_plano_diferente_muda_a_chave(self, temp_root, tmp_path) -> None:
        engine = self._engine()
        config = engine._default_config()
        composto = tmp_path / "composto.mp4"

        a = engine._template_cache_dir(composto, config, self._seed(5.0))
        b = engine._template_cache_dir(composto, config, self._seed(6.0))
        assert a != b

    def test_bbox_shift_invalida(self, temp_root, tmp_path) -> None:
        from services.lipsync.base import LipSyncConfig

        engine = self._engine()
        composto = tmp_path / "composto.mp4"

        a = engine._template_cache_dir(
            composto, LipSyncConfig(bbox_shift=0), self._seed()
        )
        b = engine._template_cache_dir(
            composto, LipSyncConfig(bbox_shift=5), self._seed()
        )
        assert a != b

    def test_sem_seed_cai_no_hash_do_arquivo(self, temp_root, tmp_path) -> None:
        engine = self._engine()
        config = engine._default_config()
        composto = tmp_path / "composto.mp4"
        composto.write_bytes(b"conteudo")

        chave_a = engine._template_cache_dir(composto, config, None)
        composto.write_bytes(b"outro conteudo diferente")
        chave_b = engine._template_cache_dir(composto, config, None)
        assert chave_a != chave_b


class TestPaths:
    def test_tudo_dentro_da_raiz(self, temp_root) -> None:
        paths = get_paths()
        for path in (
            paths.models, paths.cache, paths.projects, paths.exports,
            paths.logs, paths.identity,
        ):
            assert paths.root in path.parents or path == paths.root
