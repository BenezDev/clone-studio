"""Auto editor: monta uma EDL de enquadramento e vira filtro de render.

**O que este módulo não faz, e por quê.** "Corte" num editor comum remove
tempo. Aqui isso é impossível: o áudio é gerado pelo TTS e o vídeo foi
lip-sincronizado quadro a quadro contra ele. Tirar meio segundo da imagem
dessincroniza a boca da fala, e refazer o lip-sync custa os vinte minutos de
CPU que a etapa inteira leva. Não existe corte que não quebre o resultado.

O que um editor humano faz com **uma câmera só** — que é exatamente o caso do
Production Mode — é cortar *enquadramento*: fecha no rosto quando a frase
importa, abre quando ela termina. Visualmente lê como corte, mantém a duração
intacta e não encosta na sincronia. É isso que a EDL descreve.

A EDL é um artefato de dados, gravado em ``edl.json``. Isso é de propósito:
dá para ler, versionar, comparar entre execuções e — no futuro — editar à mão
antes de renderizar. Um filtro de FFmpeg montado direto no render seria
impossível de inspecionar.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

# Presets de ritmo. `clean` é o padrão histórico do projeto: nenhum movimento.
EDIT_PRESETS: dict[str, dict[str, Any]] = {
    # `moves=False` não é o mesmo que "sem candidatos a corte": sem ele, a
    # quebra por `max_shot_seconds` ainda inseriria cortes de respiro e o
    # preset que promete quadro parado começaria a se mexer.
    "clean": {
        "label": "Sem movimento",
        "moves": False,
        "use_emphasis": False,
        "use_sentences": False,
    },
    "sutil": {
        "label": "Sutil — fecha a cada frase",
        "moves": True,
        "use_emphasis": False,
        "use_sentences": True,
    },
    "dinamico": {
        "label": "Dinâmico — fecha em frases e ênfases",
        "moves": True,
        "use_emphasis": True,
        "use_sentences": True,
    },
}
DEFAULT_PRESET = "clean"

# Onde fica o rosto num enquadramento vertical de talking head. Ancorar o zoom
# no centro geométrico puxa a imagem para o tronco e corta a testa; 0.42 sobe o
# ponto de fuga para a altura dos olhos.
FACE_ANCHOR_Y = 0.42

# Tempo da transição entre dois enquadramentos. Abaixo de ~0.2s vira solavanco;
# acima de ~0.45s deixa de parecer corte e vira zoom de câmera amadora.
RAMP_SECONDS = 0.28


@dataclass(frozen=True)
class Shot:
    """Um enquadramento contínuo."""

    start: float
    end: float
    zoom: float
    reason: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class EditDecisionList:
    duration: float
    preset: str
    shots: list[Shot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_movement(self) -> bool:
        """Falso quando todos os enquadramentos têm o mesmo zoom."""
        return len({round(s.zoom, 4) for s in self.shots}) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": round(self.duration, 3),
            "preset": self.preset,
            "shots": [
                {**asdict(s), "start": round(s.start, 3), "end": round(s.end, 3)}
                for s in self.shots
            ],
            "warnings": self.warnings,
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "EditDecisionList":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            duration=float(raw.get("duration", 0.0)),
            preset=str(raw.get("preset", DEFAULT_PRESET)),
            shots=[
                Shot(
                    start=float(s["start"]),
                    end=float(s["end"]),
                    zoom=float(s["zoom"]),
                    reason=str(s.get("reason", "")),
                )
                for s in raw.get("shots", [])
            ],
            warnings=list(raw.get("warnings", [])),
        )


def _normalize(word: str) -> str:
    return "".join(c for c in word.lower() if c.isalnum())


def _candidate_cuts(
    transcript: Any,
    emphasis: Iterable[str],
    *,
    use_sentences: bool,
    use_emphasis: bool,
) -> list[tuple[float, str]]:
    """Momentos em que um editor humano trocaria o enquadramento."""
    cuts: list[tuple[float, str]] = []
    if transcript is None:
        return cuts

    if use_sentences:
        for segment in getattr(transcript, "segments", []) or []:
            cuts.append((float(segment.start), "frase nova"))

    if use_emphasis:
        alvos = {_normalize(w) for w in emphasis if _normalize(w)}
        for word in getattr(transcript, "words", []) or []:
            if _normalize(word.text) in alvos:
                cuts.append((float(word.start), f"ênfase: {word.text.strip()}"))

    return cuts


def plan_edit(
    duration: float,
    *,
    transcript: Any = None,
    emphasis: Sequence[str] = (),
    preset: str = DEFAULT_PRESET,
    min_shot_seconds: float = 2.0,
    max_shot_seconds: float = 8.0,
    punch_in_strength: float = 1.08,
) -> EditDecisionList:
    """Decide os enquadramentos ao longo do vídeo.

    A ordem importa. Primeiro junta os candidatos (início de frase e palavra
    enfatizada), depois **descarta** os que ficariam perto demais do anterior, e
    só então quebra os enquadramentos que ficaram longos demais. Fazer o
    contrário produziria um corte inserido por duração seguido de outro por
    frase 200 ms depois — o efeito de tremor que denuncia edição automática.
    """
    edl = EditDecisionList(duration=max(0.0, duration), preset=preset)
    if edl.duration <= 0:
        edl.warnings.append("Duração desconhecida; nenhum enquadramento planejado.")
        return edl

    estilo = EDIT_PRESETS.get(preset, EDIT_PRESETS[DEFAULT_PRESET])
    if preset not in EDIT_PRESETS:
        edl.warnings.append(
            f"Preset '{preset}' não existe; usando '{DEFAULT_PRESET}'."
        )

    if not estilo.get("moves", True):
        edl.shots.append(Shot(0.0, edl.duration, 1.0, "enquadramento fixo"))
        return edl

    # Zoom 1.0 é o quadro inteiro. Menos que isso deixaria borda preta.
    fechado = max(1.0, float(punch_in_strength))
    min_shot = max(0.5, float(min_shot_seconds))
    max_shot = max(min_shot, float(max_shot_seconds))

    candidatos = _candidate_cuts(
        transcript,
        emphasis,
        use_sentences=bool(estilo["use_sentences"]),
        use_emphasis=bool(estilo["use_emphasis"]),
    )
    if transcript is None and estilo["use_sentences"]:
        edl.warnings.append(
            "Sem transcrição: os enquadramentos foram distribuídos por tempo, "
            "não por frase. Ative as legendas para cortar na fala."
        )

    tempos: list[tuple[float, str]] = [(0.0, "abertura")]
    for tempo, motivo in sorted(candidatos, key=lambda c: c[0]):
        if tempo <= 0 or tempo >= edl.duration:
            continue
        if tempo - tempos[-1][0] < min_shot:
            continue
        tempos.append((tempo, motivo))

    # Enquadramento parado tempo demais cansa; quebra em partes iguais para não
    # deixar um pedaço curto sobrando no fim.
    expandidos: list[tuple[float, str]] = []
    for indice, (tempo, motivo) in enumerate(tempos):
        expandidos.append((tempo, motivo))
        fim = tempos[indice + 1][0] if indice + 1 < len(tempos) else edl.duration
        vao = fim - tempo
        if vao > max_shot:
            partes = int(vao // max_shot) + 1
            passo = vao / partes
            for parte in range(1, partes):
                expandidos.append((tempo + passo * parte, "respiro"))

    # Zoom alternado: dois enquadramentos seguidos no mesmo valor não leriam
    # como corte, seriam só um enquadramento mais longo.
    for indice, (tempo, motivo) in enumerate(expandidos):
        fim = (
            expandidos[indice + 1][0]
            if indice + 1 < len(expandidos)
            else edl.duration
        )
        edl.shots.append(
            Shot(
                start=tempo,
                end=fim,
                zoom=1.0 if indice % 2 == 0 else fechado,
                reason=motivo,
            )
        )

    if len(edl.shots) == 1:
        edl.warnings.append(
            "Vídeo curto demais para cortar: um enquadramento só, sem movimento."
        )
    return edl


def build_zoom_filter(
    edl: EditDecisionList,
    *,
    width: int,
    height: int,
    fps: int,
    ramp_seconds: float = RAMP_SECONDS,
    anchor_y: float = FACE_ANCHOR_Y,
) -> str | None:
    """Traduz a EDL num filtro ``zoompan``. Devolve None quando não há movimento.

    O zoom é escrito como soma acumulada de rampas em vez de uma cadeia de
    ``if`` aninhados: cada troca de enquadramento vira um termo
    ``delta*clip((it-corte)/rampa, 0, 1)``, e o valor em qualquer instante é a
    soma de tudo que já disparou. A expressão cresce linear com o número de
    cortes, e não em profundidade — o parser do FFmpeg não aguentaria dezenas
    de ``if`` encaixados.

    A âncora não é o centro. Para manter um ponto ``a`` fixo enquanto a janela
    encolhe, o deslocamento tem que ser ``a * tamanho * (1 - 1/zoom)``; com
    ``a`` no centro isso reduz à fórmula conhecida ``iw/2-(iw/zoom/2)``, e com
    ``a`` na altura dos olhos mantém o rosto no lugar em vez de deslizar para
    fora do quadro.

    ``fps`` é obrigatório porque o ``zoompan`` assume 25 quando não recebe nada
    — um render a 30 fps sairia silenciosamente a 25.
    """
    if not edl.shots or not edl.has_movement:
        return None

    rampa = max(0.05, float(ramp_seconds))
    termos = [f"{edl.shots[0].zoom:.4f}"]
    for anterior, atual in zip(edl.shots, edl.shots[1:]):
        delta = atual.zoom - anterior.zoom
        if abs(delta) < 1e-4:
            continue
        # A vírgula é escapada porque o FFmpeg usa ',' para separar filtros.
        termos.append(
            f"{delta:+.4f}*clip((it-{atual.start:.3f})/{rampa:.3f}\\,0\\,1)"
        )

    z = "".join(termos)
    x = "0.5*iw*(1-1/zoom)"
    y = f"{anchor_y:.3f}*ih*(1-1/zoom)"
    return (
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps}"
    )
