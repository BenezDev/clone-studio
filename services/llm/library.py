"""Biblioteca local de roteiros prontos.

Existe para que o estúdio seja utilizável **sem** o Ollama: os roteiros são
arquivos YAML versionados junto com o código, carregados do disco, sem rede e
sem modelo de linguagem.

Dois tipos de roteiro:

* **prontos** — texto completo, sobre assunto perene. Usa e grava.
* **modelos** — estrutura com lacunas `{{ASSIM}}` para assunto datado (notícia).
  O sistema NÃO inventa fato: quem preenche é você, com a fonte na mão.

Essa separação é deliberada. Um "roteiro pronto" sobre a notícia de hoje seria
ou desatualizado em dois dias, ou invenção — as duas coisas ruins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import yaml

from services.llm.script import Scene, Script, VoiceStyle
from core.storage.paths import get_paths

SLOT_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


class LibraryError(RuntimeError):
    pass


@dataclass(frozen=True)
class LibraryScript:
    """Um roteiro do catálogo."""

    id: str
    niche: str
    title: str
    hook: str
    scenes: tuple[str, ...]
    cta: str = ""
    caption: str = ""
    hashtags: tuple[str, ...] = ()
    emphasis: tuple[tuple[str, ...], ...] = ()
    preset: str = "educacional"
    emotion: str = "confident"
    speed: float = 1.0
    estimated_duration: float = 45.0
    tags: tuple[str, ...] = ()
    is_template: bool = False
    notes: str = ""
    disclaimer: str = ""

    # -- lacunas ---------------------------------------------------------

    @property
    def slots(self) -> tuple[str, ...]:
        """Nomes das lacunas a preencher, na ordem em que aparecem."""
        vistos: list[str] = []
        for texto in (self.hook, *self.scenes, self.cta, self.caption):
            for nome in SLOT_RE.findall(texto or ""):
                if nome not in vistos:
                    vistos.append(nome)
        return tuple(vistos)

    @property
    def word_count(self) -> int:
        return sum(len(s.split()) for s in self.scenes)

    def missing_slots(self, values: dict[str, str] | None = None) -> tuple[str, ...]:
        preenchidos = {k for k, v in (values or {}).items() if str(v).strip()}
        return tuple(s for s in self.slots if s not in preenchidos)

    # -- conversão -------------------------------------------------------

    def to_script(self, values: dict[str, str] | None = None) -> Script:
        """Converte para o `Script` que o pipeline consome.

        Levanta `LibraryError` se sobrar lacuna: gerar um vídeo com
        "{{NOME_DO_POLITICO}}" falado em voz alta seria pior que falhar.
        """
        faltando = self.missing_slots(values)
        if faltando:
            raise LibraryError(
                f"O roteiro '{self.id}' ainda tem lacunas por preencher: "
                + ", ".join(faltando)
            )

        def preencher(texto: str) -> str:
            if not values:
                return texto
            return SLOT_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), texto)

        cenas: list[Scene] = []
        cursor = 0.0
        for indice, bruto in enumerate(self.scenes):
            texto = preencher(bruto)
            enfase = list(self.emphasis[indice]) if indice < len(self.emphasis) else []
            cenas.append(
                Scene(start=round(cursor, 2), text=texto, emphasis_words=enfase)
            )
            cursor += max(1.0, len(texto.split()) / 2.6)

        return Script(
            title=preencher(self.title),
            hook=preencher(self.hook),
            estimated_duration=round(cursor, 1),
            voice_style=VoiceStyle(emotion=self.emotion, speed=self.speed),
            scenes=cenas,
            cta=preencher(self.cta),
            caption=preencher(self.caption),
            hashtags=list(self.hashtags),
            preset=self.preset,
            model=f"biblioteca:{self.id}",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "niche": self.niche,
            "title": self.title,
            "hook": self.hook,
            "scenes": list(self.scenes),
            "cta": self.cta,
            "caption": self.caption,
            "hashtags": list(self.hashtags),
            "preset": self.preset,
            "emotion": self.emotion,
            "speed": self.speed,
            "estimated_duration": self.estimated_duration,
            "tags": list(self.tags),
            "is_template": self.is_template,
            "slots": list(self.slots),
            "notes": self.notes,
            "disclaimer": self.disclaimer,
            "word_count": self.word_count,
        }


NICHE_LABELS = {
    "politica": "Política",
    "noticia_politica": "Notícias políticas",
    "noticia_tecnica": "Notícias técnicas",
    "saude_publica": "Saúde pública",
    "sociologia": "Sociologia",
}


class ScriptLibrary:
    def __init__(self, scripts: list[LibraryScript]) -> None:
        self._scripts = scripts
        self._by_id = {s.id: s for s in scripts}

    def __len__(self) -> int:
        return len(self._scripts)

    def __iter__(self) -> Iterator[LibraryScript]:
        return iter(self._scripts)

    def get(self, script_id: str) -> LibraryScript | None:
        return self._by_id.get(script_id)

    def require(self, script_id: str) -> LibraryScript:
        item = self._by_id.get(script_id)
        if item is None:
            raise LibraryError(
                f"Roteiro '{script_id}' não existe na biblioteca. "
                f"Use `clone-studio scripts list` para ver os disponíveis."
            )
        return item

    def niches(self) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for item in self._scripts:
            contagem[item.niche] = contagem.get(item.niche, 0) + 1
        return contagem

    def filter(
        self,
        *,
        niche: str | None = None,
        templates: bool | None = None,
        query: str = "",
    ) -> list[LibraryScript]:
        resultado = list(self._scripts)
        if niche:
            resultado = [s for s in resultado if s.niche == niche]
        if templates is not None:
            resultado = [s for s in resultado if s.is_template is templates]
        if query:
            termo = query.lower().strip()
            resultado = [
                s
                for s in resultado
                if termo in s.title.lower()
                or termo in s.hook.lower()
                or any(termo in t.lower() for t in s.tags)
                or any(termo in c.lower() for c in s.scenes)
            ]
        return resultado


def _parse(raw: dict[str, Any], niche: str) -> LibraryScript:
    cenas = raw.get("scenes") or []
    textos: list[str] = []
    enfases: list[tuple[str, ...]] = []
    for cena in cenas:
        if isinstance(cena, str):
            textos.append(cena)
            enfases.append(())
        else:
            textos.append(cena.get("text", ""))
            enfases.append(tuple(cena.get("emphasis") or ()))

    return LibraryScript(
        id=raw["id"],
        niche=niche,
        title=raw.get("title", raw["id"]),
        hook=raw.get("hook", ""),
        scenes=tuple(t for t in textos if t.strip()),
        cta=raw.get("cta", ""),
        caption=raw.get("caption", ""),
        hashtags=tuple(raw.get("hashtags") or ()),
        emphasis=tuple(enfases),
        preset=raw.get("preset", "educacional"),
        emotion=raw.get("emotion", "confident"),
        speed=float(raw.get("speed", 1.0)),
        estimated_duration=float(raw.get("estimated_duration", 45)),
        tags=tuple(raw.get("tags") or ()),
        is_template=bool(raw.get("template", False)),
        notes=(raw.get("notes") or "").strip(),
        disclaimer=(raw.get("disclaimer") or "").strip(),
    )


def library_dir() -> Path:
    return get_paths().root / "assets" / "scripts"


@lru_cache(maxsize=1)
def get_library() -> ScriptLibrary:
    diretorio = library_dir()
    if not diretorio.is_dir():
        return ScriptLibrary([])

    scripts: list[LibraryScript] = []
    vistos: set[str] = set()
    for arquivo in sorted(diretorio.glob("*.yaml")):
        dados = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
        niche = dados.get("niche") or arquivo.stem
        for bruto in dados.get("scripts") or []:
            item = _parse(bruto, niche)
            if item.id in vistos:
                raise LibraryError(
                    f"Roteiro duplicado na biblioteca: '{item.id}' "
                    f"(segunda ocorrência em {arquivo.name})"
                )
            vistos.add(item.id)
            scripts.append(item)
    return ScriptLibrary(scripts)


def reload_library() -> ScriptLibrary:
    get_library.cache_clear()
    return get_library()
