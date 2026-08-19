"""As implementações precisam honrar a interface abstrata.

Bug real: o pipeline passou a oferecer cancelamento e o `stages.py` começou a
chamar `engine.process(..., should_cancel=...)`. A interface e o TTS foram
atualizados; o MuseTalk não. O render quebrava com
`got an unexpected keyword argument 'should_cancel'` — e só na etapa de
lip-sync, depois de minutos de TTS já gastos.

Pior ainda: numa correção parcial o corpo do método usava `should_cancel` sem
declará-lo, o que daria `NameError` no mesmo lugar.
"""

from __future__ import annotations

import inspect

import pytest

from services.lipsync.base import LipSyncEngine
from services.lipsync.musetalk import MuseTalkEngine
from services.tts.base import TTSEngine
from services.tts.qwen3_tts import Qwen3TTSEngine
from services.transcription.base import TranscriptionEngine
from services.transcription.faster_whisper import FasterWhisperEngine
from services.comfyui.base import GenerativeVideoEngine
from services.comfyui.wan22 import Wan22Engine

PARES = [
    (LipSyncEngine, MuseTalkEngine, "process"),
    (TTSEngine, Qwen3TTSEngine, "synthesize"),
    (TranscriptionEngine, FasterWhisperEngine, "transcribe"),
    (GenerativeVideoEngine, Wan22Engine, "generate"),
]
IDS = [f"{i.__name__}.{m}" for _, i, m in PARES]


class TestAssinaturas:
    @pytest.mark.parametrize("base,impl,metodo", PARES, ids=IDS)
    def test_aceita_todos_os_parametros_da_interface(
        self, base: type, impl: type, metodo: str
    ) -> None:
        esperados = set(inspect.signature(getattr(base, metodo)).parameters)
        recebidos = set(inspect.signature(getattr(impl, metodo)).parameters)
        faltando = esperados - recebidos
        assert not faltando, (
            f"{impl.__name__}.{metodo} não aceita {faltando} — "
            "o chamador quebra em tempo de execução"
        )

    @pytest.mark.parametrize("base,impl,metodo", PARES, ids=IDS)
    def test_corpo_nao_usa_nome_nao_declarado(
        self, base: type, impl: type, metodo: str
    ) -> None:
        """Usar um parâmetro sem declará-lo dá NameError na hora errada."""
        import ast
        import textwrap

        fonte = textwrap.dedent(inspect.getsource(getattr(impl, metodo)))
        arvore = ast.parse(fonte)
        funcao = arvore.body[0]
        assert isinstance(funcao, (ast.FunctionDef, ast.AsyncFunctionDef))

        declarados = {a.arg for a in funcao.args.args}
        declarados |= {a.arg for a in funcao.args.kwonlyargs}

        for parametro in inspect.signature(getattr(base, metodo)).parameters:
            if parametro == "self":
                continue
            usa = any(
                isinstance(n, ast.Name) and n.id == parametro
                for n in ast.walk(funcao)
            )
            if usa:
                assert parametro in declarados, (
                    f"{impl.__name__}.{metodo} usa '{parametro}' sem declarar"
                )


class TestInstanciacao:
    @pytest.mark.parametrize("base,impl,metodo", PARES, ids=IDS)
    def test_engine_instancia(self, base: type, impl: type, metodo: str) -> None:
        """Uma engine precisa instanciar mesmo sem os pesos baixados."""
        assert impl() is not None

    @pytest.mark.parametrize("base,impl,metodo", PARES, ids=IDS)
    def test_implementa_a_interface(self, base: type, impl: type, metodo: str) -> None:
        assert issubclass(impl, base)
        assert not inspect.isabstract(impl), (
            f"{impl.__name__} deixou método abstrato sem implementar"
        )


class TestCancelamentoChegaNoWorker:
    """Cancelar durante o lip-sync (a etapa mais longa) precisa funcionar."""

    def test_lipsync_repassa_should_cancel(self) -> None:
        import inspect as ins

        fonte = ins.getsource(MuseTalkEngine.process)
        assert "should_cancel=should_cancel" in fonte, (
            "o lip-sync roda por dezenas de minutos; sem repassar o "
            "cancelamento o usuário fica preso"
        )

    def test_tts_repassa_should_cancel(self) -> None:
        import inspect as ins

        fonte = ins.getsource(Qwen3TTSEngine.synthesize)
        assert "should_cancel" in fonte

    def test_runner_aceita_should_cancel(self) -> None:
        from core.worker.runner import run_worker

        assert "should_cancel" in inspect.signature(run_worker).parameters
