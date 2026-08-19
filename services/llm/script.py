"""Script Engine — geração e validação de roteiros.

Regra inegociável: **nunca confiar no JSON do LLM**. Modelos locais erram
tipos, inventam campos, devolvem strings onde deveriam devolver números e
às vezes escrevem em inglês quando se pede português. Tudo passa pelo
Pydantic e por uma camada de coerção antes de virar dado do sistema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from services.llm.ollama import OllamaClient, OllamaUnavailable, extract_json

# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------


class VoiceStyle(BaseModel):
    emotion: str = "confident"
    speed: float = 1.0

    @field_validator("speed", mode="before")
    @classmethod
    def _coerce_speed(cls, value: Any) -> float:
        try:
            speed = float(value)
        except (TypeError, ValueError):
            return 1.0
        # Fora dessa faixa a voz fica caricata.
        return max(0.7, min(1.4, speed))


class Scene(BaseModel):
    start: float = 0.0
    text: str = ""
    visual: str = "talking_head"
    broll_prompt: str | None = None
    emphasis_words: list[str] = Field(default_factory=list)

    @field_validator("start", mode="before")
    @classmethod
    def _coerce_start(cls, value: Any) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("emphasis_words", mode="before")
    @classmethod
    def _coerce_emphasis(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [w.strip() for w in value.split(",") if w.strip()]
        if isinstance(value, list):
            return [str(w).strip() for w in value if str(w).strip()]
        return []

    @field_validator("broll_prompt", mode="before")
    @classmethod
    def _clean_broll(cls, value: Any) -> str | None:
        if not value or str(value).strip().lower() in {"null", "none", "n/a", ""}:
            return None
        return str(value).strip()


class Script(BaseModel):
    title: str = ""
    hook: str = ""
    estimated_duration: float = 45.0
    voice_style: VoiceStyle = Field(default_factory=VoiceStyle)
    scenes: list[Scene] = Field(default_factory=list)
    cta: str = ""
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)

    # Metadados de rastreabilidade (não vêm do LLM).
    preset: str = ""
    model: str = ""

    @field_validator("estimated_duration", mode="before")
    @classmethod
    def _coerce_duration(cls, value: Any) -> float:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            return 45.0
        return max(5.0, min(180.0, duration))

    @field_validator("hashtags", mode="before")
    @classmethod
    def _coerce_hashtags(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = value.replace(",", " ").split()
        tags: list[str] = []
        for raw in value:
            tag = str(raw).strip().lstrip("#")
            if tag:
                tags.append(f"#{tag}")
        return tags[:12]

    @property
    def full_text(self) -> str:
        """O texto que será efetivamente falado."""
        parts = [s.text.strip() for s in self.scenes if s.text.strip()]
        return " ".join(parts).strip()

    @property
    def all_emphasis_words(self) -> list[str]:
        words: list[str] = []
        for scene in self.scenes:
            words.extend(scene.emphasis_words)
        return words

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "Script":
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

PresetKey = Literal[
    "viral", "educacional", "storytelling", "tech", "polemico",
    "noticia", "venda", "autoridade", "tutorial",
]


PRESETS: dict[str, dict[str, str]] = {
    "viral": {
        "label": "Viral",
        "emotion": "excited",
        "guidance": (
            "Hook nos primeiros 2 segundos que gere curiosidade ou contradiga o "
            "senso comum. Frases curtas. Ritmo acelerado. Uma ideia por frase. "
            "Sem introdução — comece pelo ponto mais forte."
        ),
    },
    "educacional": {
        "label": "Educacional",
        "emotion": "confident",
        "guidance": (
            "Explique um conceito com clareza. Comece pelo problema que a pessoa "
            "tem, depois entregue a explicação em passos. Use analogia concreta. "
            "Evite jargão sem definir."
        ),
    },
    "storytelling": {
        "label": "Storytelling",
        "emotion": "warm",
        "guidance": (
            "Conte uma história real em primeira pessoa: situação, conflito, "
            "virada, lição. O hook é o momento mais tenso da história, contado "
            "fora de ordem."
        ),
    },
    "tech": {
        "label": "Tech",
        "emotion": "confident",
        "guidance": (
            "Público técnico. Seja preciso, cite o que importa, sem hype. "
            "Compare alternativas quando fizer sentido. Nada de promessa vaga."
        ),
    },
    "polemico": {
        "label": "Polêmico",
        "emotion": "assertive",
        "guidance": (
            "Defenda uma posição contra-intuitiva com argumento honesto. "
            "Reconheça o outro lado antes de refutá-lo. Sem ataque pessoal, sem "
            "desinformação e sem generalizar sobre grupos de pessoas."
        ),
    },
    "noticia": {
        "label": "Notícia",
        "emotion": "neutral",
        "guidance": (
            "Fato primeiro, contexto depois, implicação no final. Tom direto. "
            "Não afirme nada que não tenha sido informado no material de entrada."
        ),
    },
    "venda": {
        "label": "Venda",
        "emotion": "confident",
        "guidance": (
            "Problema, agitação, solução, prova, oferta, chamada. Concreto e "
            "específico. Sem promessa de resultado garantido."
        ),
    },
    "autoridade": {
        "label": "Autoridade",
        "emotion": "confident",
        "guidance": (
            "Posicione experiência sem arrogância. Uma tese clara sustentada por "
            "experiência própria. Tom calmo, ritmo mais lento."
        ),
    },
    "tutorial": {
        "label": "Tutorial",
        "emotion": "neutral",
        "guidance": (
            "Passo a passo numerado e acionável. Cada cena é um passo. Diga o "
            "resultado esperado no começo."
        ),
    },
}


SYSTEM_PROMPT = """Você é um roteirista brasileiro especializado em vídeos verticais curtos para TikTok, Reels e Shorts.

REGRAS ABSOLUTAS:
- Escreva SEMPRE em português brasileiro natural e falado, nunca em tom de artigo escrito.
- Use contrações da fala real ("tá", "pra", "cê" quando couber ao tom).
- Frases curtas. Uma ideia por frase.
- O texto será lido em voz alta por um clone de voz: escreva como se fala, não como se escreve.
- Não use emojis dentro do campo "text" das cenas.
- Não use markdown, asteriscos ou formatação no texto falado.
- Responda SOMENTE com um objeto JSON válido, sem cercas de código e sem comentários."""


SCHEMA_HINT = """{
  "title": "título curto do vídeo",
  "hook": "primeira frase, máximo 12 palavras",
  "estimated_duration": 45,
  "voice_style": {"emotion": "confident", "speed": 1.05},
  "scenes": [
    {
      "start": 0,
      "text": "texto falado desta cena",
      "visual": "talking_head",
      "broll_prompt": null,
      "emphasis_words": ["palavra"]
    }
  ],
  "cta": "chamada para ação final",
  "caption": "legenda para o post",
  "hashtags": ["#exemplo"]
}"""


# ---------------------------------------------------------------------------
# Geração
# ---------------------------------------------------------------------------

# ~2.6 palavras por segundo é o ritmo confortável de fala em pt-BR.
WORDS_PER_SECOND = 2.6


def build_prompt(
    idea: str,
    *,
    preset: str = "viral",
    duration: int = 45,
    extra_instructions: str = "",
) -> str:
    config = PRESETS.get(preset, PRESETS["viral"])
    target_words = int(duration * WORDS_PER_SECOND)

    return f"""Crie o roteiro de um vídeo vertical sobre:

{idea}

ESTILO: {config['label']}
{config['guidance']}

DURAÇÃO ALVO: {duration} segundos (aproximadamente {target_words} palavras faladas no total)
Divida em 3 a 8 cenas. A soma dos textos das cenas deve ficar perto de {target_words} palavras.

{extra_instructions}

Responda exatamente neste formato JSON:
{SCHEMA_HINT}"""


def _repair_scenes(script: Script) -> Script:
    """Corrige o que o LLM tipicamente erra nas cenas.

    Recalcula os tempos de início a partir do próprio texto: modelos locais
    quase sempre inventam `start` sem relação com o comprimento das falas.
    """
    script.scenes = [s for s in script.scenes if s.text.strip()]

    if not script.scenes and script.hook:
        script.scenes = [Scene(start=0.0, text=script.hook)]

    cursor = 0.0
    for scene in script.scenes:
        scene.start = round(cursor, 2)
        words = len(scene.text.split())
        cursor += max(1.0, words / WORDS_PER_SECOND)

    if script.scenes:
        script.estimated_duration = round(cursor, 1)
    return script


def parse_script(raw: str, *, preset: str = "", model: str = "") -> Script:
    """Converte a saída bruta do LLM num Script validado."""
    data = extract_json(raw)
    try:
        script = Script.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            "O roteiro devolvido pelo modelo não bate com o formato esperado:\n"
            f"{exc}"
        ) from exc

    script.preset = preset
    script.model = model
    return _repair_scenes(script)


def generate_script(
    idea: str,
    *,
    preset: str = "viral",
    duration: int = 45,
    model: str | None = None,
    extra_instructions: str = "",
    client: OllamaClient | None = None,
) -> Script:
    """Gera um roteiro estruturado a partir de uma ideia.

    Levanta `OllamaUnavailable` quando o serviço não está rodando — o chamador
    decide se cai para a escrita manual.
    """
    ollama = client or OllamaClient()
    chosen = model or ollama.resolve_model()

    config = PRESETS.get(preset, PRESETS["viral"])
    raw = ollama.generate(
        build_prompt(
            idea, preset=preset, duration=duration,
            extra_instructions=extra_instructions,
        ),
        system=SYSTEM_PROMPT,
        model=chosen,
        json_mode=True,
    )

    script = parse_script(raw, preset=preset, model=chosen)
    if not script.voice_style.emotion:
        script.voice_style.emotion = config["emotion"]
    return script


# ---------------------------------------------------------------------------
# Operações de edição
# ---------------------------------------------------------------------------


def _single_shot(
    instruction: str, content: str, client: OllamaClient | None = None,
    model: str | None = None,
) -> str:
    ollama = client or OllamaClient()
    return ollama.generate(
        f"{instruction}\n\n---\n{content}\n---\n\n"
        "Responda apenas com o texto resultante, sem explicação.",
        system=SYSTEM_PROMPT.replace(
            "Responda SOMENTE com um objeto JSON válido, sem cercas de código e "
            "sem comentários.",
            "Responda apenas com o texto pedido.",
        ),
        model=model,
    ).strip()


def improve_hook(hook: str, **kwargs: Any) -> str:
    return _single_shot(
        "Reescreva este hook de vídeo curto para prender a atenção nos "
        "primeiros 2 segundos. Máximo 12 palavras. Gere 1 versão apenas.",
        hook,
        **kwargs,
    )


def rewrite(text: str, instruction: str, **kwargs: Any) -> str:
    return _single_shot(f"Reescreva o texto abaixo. {instruction}", text, **kwargs)


def summarize(text: str, **kwargs: Any) -> str:
    return _single_shot(
        "Resuma o texto abaixo em no máximo 3 frases faladas.", text, **kwargs
    )


def long_to_short(text: str, duration: int = 45, **kwargs: Any) -> str:
    return _single_shot(
        f"Condense este conteúdo longo num roteiro falado de {duration} segundos "
        f"(cerca de {int(duration * WORDS_PER_SECOND)} palavras). "
        "Mantenha apenas a ideia mais forte.",
        text,
        **kwargs,
    )


def make_cta(text: str, **kwargs: Any) -> str:
    return _single_shot(
        "Crie uma chamada para ação final de uma frase para este roteiro. "
        "Sem clichê de 'segue o perfil'.",
        text,
        **kwargs,
    )


def make_caption(text: str, **kwargs: Any) -> str:
    return _single_shot(
        "Escreva a legenda do post para este roteiro. Até 200 caracteres, "
        "sem hashtags.",
        text,
        **kwargs,
    )


def make_hashtags(text: str, **kwargs: Any) -> list[str]:
    raw = _single_shot(
        "Liste de 5 a 8 hashtags em português relevantes para este roteiro, "
        "separadas por espaço.",
        text,
        **kwargs,
    )
    return [f"#{t.lstrip('#')}" for t in raw.replace(",", " ").split() if t.strip()][:8]


def make_title(text: str, **kwargs: Any) -> str:
    return _single_shot(
        "Crie um título curto e específico para este vídeo. Máximo 60 caracteres.",
        text,
        **kwargs,
    )


def make_broll_prompts(script: Script, **kwargs: Any) -> Script:
    """Sugere B-roll cena a cena, sem inventar quando não faz sentido."""
    for scene in script.scenes:
        if scene.visual != "talking_head" or scene.broll_prompt:
            continue
        suggestion = _single_shot(
            "Se esta frase se beneficiaria de uma imagem ou vídeo de apoio, "
            "descreva-a em até 10 palavras. Se não se beneficiar, responda "
            "exatamente: NENHUM",
            scene.text,
            **kwargs,
        )
        if suggestion.strip().upper() not in {"NENHUM", "NONE", ""}:
            scene.broll_prompt = suggestion.strip()
    return script


__all__ = [
    "PRESETS",
    "OllamaUnavailable",
    "Scene",
    "Script",
    "VoiceStyle",
    "build_prompt",
    "generate_script",
    "improve_hook",
    "long_to_short",
    "make_broll_prompts",
    "make_caption",
    "make_cta",
    "make_hashtags",
    "make_title",
    "parse_script",
    "rewrite",
    "summarize",
]
