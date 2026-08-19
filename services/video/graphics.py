r"""Animações gráficas queimadas no vídeo, escritas em ASS.

**Por que ASS e não um motor de animação.** O projeto já renderiza ASS pelo
FFmpeg para as legendas, e o libass sabe muito mais do que texto: formas
vetoriais (``\p1``), transformações animadas (``\t``), movimento (``\move``),
fades, recortes e desfoque. Lower third, card de hook, barra de progresso e end
card cabem inteiros nisso.

O que isso evita é o custo real da alternativa: um subprojeto Node com Chromium
headless, centenas de MB, tempo de render por frame numa máquina sem GPU — e,
no caso do Remotion, uma licença que exige Company License a partir de quatro
pessoas e cuja responsabilidade recai sobre quem detém a IP do projeto. Num
software feito para ser vendido, isso viaja junto com o comprador.

**Herança de estilo.** Os overlays não têm paleta própria: pegam fonte e cores
do preset de legenda do projeto. Vídeo em que a legenda é de um jeito e o card
de outro parece montado por duas pessoas diferentes.

**Cuidado ao editar este arquivo.** Toda string com tag ASS é raw string. Em
Python, ``\a`` é BEL, ``\b`` é backspace e ``\f`` é formfeed — sem o ``r``,
``\an5``, ``\bord`` e ``\fscx`` viram bytes de controle e o libass descarta a
linha em silêncio, sem erro nenhum.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from services.video.captions import PRESETS as CAPTION_PRESETS
from services.video.captions import CaptionPreset, _escape, _timestamp, _wrap

# Frações da altura do vídeo, para o mesmo plano servir 1080x1920 e o preview.
PROGRESS_HEIGHT = 0.0055
HOOK_ANCHOR = 0.30
LOWER_THIRD_ANCHOR = 0.865
END_CARD_ANCHOR = 0.46

# Um hook que fica menos que isso não dá tempo de ler; mais que isso rouba a
# atenção da fala.
HOOK_SECONDS = 2.4
END_CARD_SECONDS = 2.2


@dataclass(frozen=True)
class Overlay:
    kind: str
    start: float
    end: float
    text: str = ""
    reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class GraphicsPlan:
    duration: float
    overlays: list[Overlay] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": round(self.duration, 3),
            "overlays": [
                {**asdict(o), "start": round(o.start, 3), "end": round(o.end, 3)}
                for o in self.overlays
            ],
            "warnings": self.warnings,
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path


def plan_overlays(
    duration: float,
    *,
    hook: str = "",
    cta: str = "",
    handle: str = "",
    progress: bool = True,
) -> GraphicsPlan:
    """Decide o que aparece e quando.

    Nada é inventado: o hook e o CTA vêm do roteiro. Um card com texto genérico
    seria pior que card nenhum.
    """
    plan = GraphicsPlan(duration=max(0.0, duration))
    if plan.duration <= 0:
        plan.warnings.append("Duração desconhecida; nenhum gráfico planejado.")
        return plan

    if progress:
        plan.overlays.append(
            Overlay("progress", 0.0, plan.duration, reason="retenção")
        )

    hook = (hook or "").strip()
    if hook:
        fim = min(HOOK_SECONDS, plan.duration * 0.4)
        if fim < 0.8:
            plan.warnings.append("Vídeo curto demais para o card de hook.")
        else:
            plan.overlays.append(Overlay("hook", 0.0, fim, hook, "abertura"))

    handle = (handle or "").strip()
    if handle:
        # Entra depois do hook para os dois não disputarem a atenção.
        inicio = min(HOOK_SECONDS + 0.3, plan.duration * 0.5)
        fim = min(inicio + 2.6, plan.duration)
        if fim - inicio >= 0.8:
            plan.overlays.append(
                Overlay("lower_third", inicio, fim, handle, "identificação")
            )

    cta = (cta or "").strip()
    if cta:
        # O teto de 40% vale para o end card pelo mesmo motivo do hook: sem ele,
        # num vídeo curto o card cobre o clipe inteiro em vez de fechar.
        inicio = max(plan.duration * 0.6, plan.duration - END_CARD_SECONDS)
        if plan.duration - inicio >= 0.8:
            plan.overlays.append(
                Overlay("end_card", inicio, plan.duration, cta, "chamada")
            )
        else:
            plan.warnings.append("Vídeo curto demais para o end card.")

    return plan


# ---------------------------------------------------------------------------
# Tradução para ASS
# ---------------------------------------------------------------------------


# Largura média de caractere numa sans bold, como fração do corpo da fonte.
# Serve para estimar quantos cabem na linha; erra para menos de propósito.
_CHAR_WIDTH_RATIO = 0.54

# Margem lateral livre de cada lado, em fração da largura.
_SIDE_MARGIN = 0.08


def _fit(text: str, *, width: int, font_size: int) -> str:
    r"""Escapa e quebra o texto para caber no quadro.

    A ordem importa: ``_escape`` troca ``\`` por um caractere de largura
    inteira, então quebrar antes de escapar transformaria a própria quebra
    ``\N`` em texto visível — foi exatamente o que aconteceu na primeira
    versão, com o ``\N`` aparecendo no meio da frase.

    A quantidade de caracteres por linha sai do tamanho real da fonte e da
    largura disponível. Um número fixo funciona num preset e estoura no
    seguinte, porque cada preset tem corpo diferente. E `WrapStyle: 2` desliga
    a quebra automática do libass: se a conta aqui errar, o texto sai do quadro
    em vez de descer de linha.
    """
    util = width * (1 - 2 * _SIDE_MARGIN)
    por_linha = max(8, int(util / max(1.0, font_size * _CHAR_WIDTH_RATIO)))
    return _wrap(_escape(text), por_linha)


def _color(ass_color: str) -> str:
    r"""Converte a cor de estilo (``&HAABBGGRR``) para a de tag (``&HBBGGRR&``).

    São formatos diferentes e o libass não avisa quando recebe o errado: ele lê
    o número inteiro e mascara, então passar os 8 dígitos só dá certo enquanto o
    alfa for ``00``. Com qualquer outro alfa a cor sai trocada, e o sintoma é um
    gráfico com a cor errada que ninguém liga ao byte extra.
    """
    limpo = ass_color.strip().lstrip("&Hh").rstrip("&")
    if len(limpo) >= 8:
        limpo = limpo[2:8]
    return f"&H{limpo.upper():>06}&"


def _drawing_rect(width: int, height: int) -> str:
    """Retângulo em modo de desenho, ancorado no canto superior esquerdo."""
    return rf"{{\p1}}m 0 0 l {width} 0 {width} {height} 0 {height}{{\p0}}"


def _dialogue(layer: int, start: float, end: float, style: str, text: str) -> str:
    return (
        f"Dialogue: {layer},{_timestamp(start)},{_timestamp(end)},"
        f"{style},,0,0,0,,{text}"
    )


def _progress_events(
    overlay: Overlay, width: int, height: int, preset: CaptionPreset, font: int
) -> list[str]:
    r"""Barra que enche da esquerda para a direita.

    Cresce por ``\fscx`` em vez de ``\clip`` animado: escala horizontal de um
    desenho ancorado em ``\an7`` é o caminho que o libass trata de forma
    idêntica em toda versão, enquanto recorte animado varia entre builds.
    """
    barra = max(2, int(round(height * PROGRESS_HEIGHT)))
    ms = int(overlay.duration * 1000)
    trilho = (
        rf"{{\an7\pos(0,0)\bord0\shad0\c{_color(preset.outline_color)}"
        rf"\alpha&H90&}}"
        + _drawing_rect(width, barra)
    )
    preenchimento = (
        rf"{{\an7\pos(0,0)\bord0\shad0\c{_color(preset.highlight_color)}"
        rf"\fscx0\t(0,{ms},\fscx100)}}"
        + _drawing_rect(width, barra)
    )
    return [
        _dialogue(4, overlay.start, overlay.end, "Graphics", trilho),
        _dialogue(5, overlay.start, overlay.end, "Graphics", preenchimento),
    ]


def _hook_events(
    overlay: Overlay, width: int, height: int, preset: CaptionPreset, font: int
) -> list[str]:
    r"""Card de abertura, com pop de entrada.

    O ``\t`` com aceleração 0.6 desacelera no fim: crescimento linear parece
    mecânico, e é justamente o primeiro meio segundo do vídeo.
    """
    bruto = overlay.text.upper() if preset.uppercase else overlay.text
    texto = _fit(bruto, width=width, font_size=font)
    return [
        _dialogue(
            6,
            overlay.start,
            overlay.end,
            "Graphics",
            rf"{{\an5\pos({width // 2},{int(height * HOOK_ANCHOR)})"
            rf"\fscx78\fscy78\t(0,260,0.6,\fscx100\fscy100)\fad(120,220)}}{texto}",
        )
    ]


def _lower_third_events(
    overlay: Overlay, width: int, height: int, preset: CaptionPreset, font: int
) -> list[str]:
    """Faixa que entra deslizando pela esquerda, com o texto junto."""
    altura = max(8, int(round(height * 0.052)))
    largura = max(40, int(round(width * 0.62)))
    y = int(height * LOWER_THIRD_ANCHOR)
    margem = int(width * 0.06)
    ms_entrada = 260

    faixa = (
        rf"{{\an7\move({-largura},{y},{margem},{y},0,{ms_entrada})"
        rf"\bord0\shad0\c{_color(preset.highlight_color)}"
        rf"\alpha&H30&\fad(0,200)}}" + _drawing_rect(largura, altura)
    )
    texto = (
        rf"{{\an4\move({-largura},{y + altura // 2},{margem + int(width * 0.03)},"
        rf"{y + altura // 2},0,{ms_entrada})\fs{max(10, int(height * 0.026))}"
        rf"\bord0\shad1\fad(0,200)}}" + _escape(overlay.text)
    )
    return [
        _dialogue(6, overlay.start, overlay.end, "Graphics", faixa),
        _dialogue(7, overlay.start, overlay.end, "Graphics", texto),
    ]


def _end_card_events(
    overlay: Overlay, width: int, height: int, preset: CaptionPreset, font: int
) -> list[str]:
    bruto = overlay.text.upper() if preset.uppercase else overlay.text
    texto = _fit(bruto, width=width, font_size=font)
    return [
        _dialogue(
            6,
            overlay.start,
            overlay.end,
            "Graphics",
            rf"{{\an5\pos({width // 2},{int(height * END_CARD_ANCHOR)})"
            rf"\fscx88\fscy88\t(0,300,0.7,\fscx100\fscy100)\fad(260,120)}}{texto}",
        )
    ]


_BUILDERS = {
    "progress": _progress_events,
    "hook": _hook_events,
    "lower_third": _lower_third_events,
    "end_card": _end_card_events,
}


def build_graphics_ass(
    plan: GraphicsPlan,
    *,
    width: int,
    height: int,
    preset: CaptionPreset,
) -> str | None:
    """Monta o ASS dos overlays. Devolve None quando não há nada a desenhar."""
    if not plan.overlays:
        return None

    fonte = max(12, int(round(height * preset.font_size_ratio)))
    cabecalho = f"""[Script Info]
; Gráficos gerados pelo Local Clone Studio
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Graphics,{preset.font},{fonte},{preset.primary_color},{preset.highlight_color},{preset.outline_color},{preset.back_color},{-1 if preset.bold else 0},0,0,0,100,100,0,0,1,{preset.outline},{preset.shadow},5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    eventos: list[str] = []
    for overlay in plan.overlays:
        construtor = _BUILDERS.get(overlay.kind)
        if construtor is None:
            plan.warnings.append(f"Overlay '{overlay.kind}' desconhecido; ignorado.")
            continue
        eventos.extend(construtor(overlay, width, height, preset, fonte))

    if not eventos:
        return None
    return cabecalho + "\n".join(eventos) + "\n"


def write_graphics(
    plan: GraphicsPlan,
    destination: Path,
    *,
    width: int,
    height: int,
    preset_key: str = "hormozi",
) -> Path | None:
    preset = CAPTION_PRESETS.get(preset_key) or next(iter(CAPTION_PRESETS.values()))
    conteudo = build_graphics_ass(plan, width=width, height=height, preset=preset)
    if conteudo is None:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(conteudo, encoding="utf-8")
    return destination


def available_elements() -> Sequence[str]:
    return tuple(_BUILDERS)
