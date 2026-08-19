"""Instrumentação do lip-sync e o que ela sustenta.

A etapa é 95% do render. Sem tempo por fase, qualquer conversa sobre
performance vira chute — e foi assim que uma medição contaminada quase virou
uma mudança de padrão que não faz nada.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "services" / "lipsync" / "worker" / "musetalk_worker.py"


class TestResultadoDoLipSync:
    def test_carrega_tempo_por_fase(self) -> None:
        from services.lipsync.base import LipSyncResult

        resultado = LipSyncResult(
            output=Path("saida.mp4"),
            frames=100,
            fps=25.0,
            seconds_per_frame=3.4,
            duration_seconds=340.0,
            phase_seconds={"inferencia": 300.0, "modelos": 25.0},
            batch_size=4,
        )
        payload = resultado.to_dict()
        assert payload["phase_seconds"]["inferencia"] == 300.0
        assert payload["batch_size"] == 4

    def test_default_vazio_nao_compartilha_estado(self) -> None:
        """`dict` como default mutável seria compartilhado entre instâncias."""
        from services.lipsync.base import LipSyncResult

        a = LipSyncResult(Path("a"), 1, 25.0, 1.0, 1.0)
        b = LipSyncResult(Path("b"), 1, 25.0, 1.0, 1.0)
        a.phase_seconds["x"] = 1.0
        assert b.phase_seconds == {}


class TestWorkerInstrumentado:
    """Análise por fonte: importar o worker exigiria o ambiente do MuseTalk."""

    def _fonte(self) -> str:
        return WORKER.read_text(encoding="utf-8")

    def test_usa_inference_mode(self) -> None:
        """`inference_mode` é estritamente mais barato que `no_grad`."""
        fonte = self._fonte()
        assert "torch.inference_mode()" in fonte
        assert "torch.no_grad()" not in fonte

    def test_mede_as_fases_caras(self) -> None:
        fonte = self._fonte()
        for fase in ("modelos", "audio", "template", "inferencia", "recomposicao"):
            assert f'"{fase}"' in fonte, f"fase {fase} sem cronômetro"

    def test_devolve_os_tempos_ao_host(self) -> None:
        """Tempo que só vai para o log some quando o terminal fecha."""
        assert '"phase_seconds"' in self._fonte()

    def test_sintaxe_valida(self) -> None:
        ast.parse(self._fonte())

    def test_tags_ass_nao_viraram_bytes_de_controle(self) -> None:
        """Guarda geral: nenhum arquivo do projeto deve conter BEL ou backspace."""
        for proibido in ("\x07", "\x08"):
            assert proibido not in self._fonte()


class TestDiagnostico:
    def test_biblioteca_de_broll_aparece(self, temp_root) -> None:
        """Descobrir a biblioteca vazia antes do render, não depois de 20 min."""
        from core.diagnostics.checks import DiagnosticsReport, check_identity

        report = DiagnosticsReport()
        check_identity(report)
        nomes = [c.name for c in report.checks]
        assert "Biblioteca de B-roll" in nomes

    def test_biblioteca_vazia_nao_e_erro(self, temp_root) -> None:
        """B-roll é opcional: vazio não pode assustar quem não usa."""
        from core.diagnostics.checks import DiagnosticsReport, Level, check_identity

        report = DiagnosticsReport()
        check_identity(report)
        broll = next(c for c in report.checks if c.name == "Biblioteca de B-roll")
        assert broll.level is not Level.ERROR
        assert broll.level is not Level.WARN
