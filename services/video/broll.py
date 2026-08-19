"""Biblioteca de B-roll: indexação, escolha e composição.

**B-roll aqui é cobertura, não corte.** Vale a mesma restrição da fase 9: o
vídeo está lip-sincronizado quadro a quadro com o áudio, então nada pode
remover tempo. Um trecho de apoio entra *por cima* da imagem durante alguns
segundos — o áudio segue intacto e a boca continua batendo quando a imagem
volta. É exatamente o que B-roll é num vídeo de uma câmera só.

**Correspondência por palavra-chave, e o nome disso não é semântica.** A busca
compara os termos do `broll_prompt` da cena com as palavras do nome do arquivo,
sem acento e sem palavra vazia. Índice semântico de verdade exigiria um modelo
de embeddings — outro peso para baixar, outro ambiente, mais RAM. Para uma
biblioteca pessoal de dezenas de arquivos com nomes descritivos, casar palavra
resolve, e é honesto sobre o que faz.

**Nada é escolhido sem o autor pedir.** Só entra B-roll onde a cena tem
`broll_prompt` preenchido. Enfiar imagem por conta própria em cima do rosto de
alguém seria adivinhação cara de desfazer.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from core.media import ffmpeg
from core.storage.paths import get_paths

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

# Palavras que aparecem em quase todo nome de arquivo e em quase todo prompt:
# casariam com tudo e destruiriam o ranking.
STOPWORDS = frozenset(
    """
    a as o os um uma uns umas de do da dos das em no na nos nas por para com
    sem sobre entre ao aos e ou que se ja mais menos muito pouco the of and
    img video clip final novo nova copy copia
    """.split()
)

# Um corte curto demais lê como falha de render; longo demais tira o rosto de
# cena e o vídeo deixa de ser seu.
MIN_CUT_SECONDS = 1.2
MAX_CUT_SECONDS = 3.5
# Teto de cobertura: acima disso não é mais um vídeo com apoio visual.
MAX_COVERAGE = 0.40
MAX_CUTS = 4


@dataclass(frozen=True)
class BrollAsset:
    id: str
    path: str
    kind: str
    duration: float
    width: int
    height: int
    keywords: tuple[str, ...]

    @property
    def is_video(self) -> bool:
        return self.kind == "video"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "keywords": list(self.keywords)}


@dataclass(frozen=True)
class BrollCut:
    asset_id: str
    start: float
    end: float
    prompt: str
    score: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "prompt": self.prompt,
            "score": round(self.score, 3),
        }


def normalize(text: str) -> list[str]:
    """Minúsculas, sem acento, sem pontuação, sem palavra vazia.

    Sem tirar acento, "gráfico" e "grafico" seriam termos diferentes — e o
    usuário digita dos dois jeitos.
    """
    sem_acento = "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(c) != "Mn"
    )
    bruto = "".join(c if c.isalnum() else " " for c in sem_acento).split()
    return [p for p in bruto if len(p) > 2 and p not in STOPWORDS]


def index_broll(directory: Path | None = None) -> list[BrollAsset]:
    """Varre a pasta e lê metadados reais de cada arquivo."""
    paths = get_paths()
    alvo = Path(directory) if directory else paths.broll_dir
    if not alvo.exists():
        return []

    assets: list[BrollAsset] = []
    for arquivo in sorted(alvo.iterdir()):
        if not arquivo.is_file():
            continue
        suffix = arquivo.suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            kind = "video"
        elif suffix in IMAGE_SUFFIXES:
            kind = "image"
        else:
            continue

        try:
            info = ffmpeg.probe(arquivo)
        except (ffmpeg.FFmpegError, FileNotFoundError):
            # Arquivo ilegível não derruba a indexação da biblioteca inteira.
            continue

        largura, altura = info.resolution or (0, 0)
        assets.append(
            BrollAsset(
                id=arquivo.stem,
                path=str(arquivo.relative_to(paths.root)),
                kind=kind,
                duration=round(info.duration, 2) if kind == "video" else 0.0,
                width=largura,
                height=altura,
                keywords=tuple(normalize(arquivo.stem)),
            )
        )
    return assets


def score_asset(asset: BrollAsset, terms: Sequence[str]) -> float:
    """Fração dos termos do pedido que aparecem no nome do arquivo."""
    if not terms or not asset.keywords:
        return 0.0
    chaves = set(asset.keywords)
    acertos = sum(
        1
        for termo in terms
        if termo in chaves or any(termo in k or k in termo for k in chaves)
    )
    return acertos / len(terms)


def find_asset(prompt: str, library: Iterable[BrollAsset]) -> tuple[BrollAsset | None, float]:
    melhor: BrollAsset | None = None
    melhor_nota = 0.0
    termos = normalize(prompt)
    for asset in library:
        nota = score_asset(asset, termos)
        if nota > melhor_nota:
            melhor, melhor_nota = asset, nota
    return melhor, melhor_nota


def plan_broll(
    scenes: Sequence[dict[str, Any]],
    *,
    library: Sequence[BrollAsset],
    duration: float,
    transcript: Any = None,
    min_score: float = 0.34,
) -> tuple[list[BrollCut], list[str]]:
    """Decide onde cada trecho de apoio entra.

    O tempo de cada cena sai da transcrição quando ela existe: as falas foram
    sintetizadas na ordem do roteiro, então o início de cada frase reconhecida
    corresponde ao início da cena. Sem transcrição, divide proporcional ao
    tamanho do texto — pior, mas honesto, e avisado.
    """
    avisos: list[str] = []
    cortes: list[BrollCut] = []
    if duration <= 0 or not scenes or not library:
        return cortes, avisos

    limites = _scene_windows(scenes, duration, transcript)
    if transcript is None:
        avisos.append(
            "Sem transcrição: o B-roll foi posicionado por proporção de texto. "
            "Ative as legendas para casar com a fala."
        )

    orcamento = duration * MAX_COVERAGE
    usado = 0.0
    for scene, (inicio, fim) in zip(scenes, limites):
        if len(cortes) >= MAX_CUTS:
            avisos.append(f"Limite de {MAX_CUTS} trechos de apoio atingido.")
            break
        prompt = (scene.get("broll_prompt") or "").strip()
        if not prompt:
            continue

        asset, nota = find_asset(prompt, library)
        if asset is None or nota < min_score:
            avisos.append(f"Nada na biblioteca combina com “{prompt}”.")
            continue

        disponivel = min(fim - inicio, MAX_CUT_SECONDS)
        if asset.is_video and asset.duration > 0:
            disponivel = min(disponivel, asset.duration)
        # Apara para caber no orçamento em vez de descartar: num vídeo curto o
        # primeiro corte já estouraria o teto, e descartar deixaria o roteiro
        # que pediu apoio visual sem nenhum.
        disponivel = min(disponivel, orcamento - usado)
        if disponivel < MIN_CUT_SECONDS:
            if orcamento - usado < MIN_CUT_SECONDS:
                avisos.append(
                    "Teto de cobertura alcançado; o resto do B-roll foi descartado."
                )
                break
            avisos.append(f"“{prompt}”: janela curta demais para entrar.")
            continue

        cortes.append(
            BrollCut(asset.id, inicio, inicio + disponivel, prompt, nota)
        )
        usado += disponivel

    return cortes, avisos


def _scene_windows(
    scenes: Sequence[dict[str, Any]], duration: float, transcript: Any
) -> list[tuple[float, float]]:
    segmentos = list(getattr(transcript, "segments", []) or []) if transcript else []
    if len(segmentos) >= len(scenes):
        janelas = []
        for indice in range(len(scenes)):
            inicio = float(segmentos[indice].start)
            fim = (
                float(segmentos[indice + 1].start)
                if indice + 1 < len(segmentos)
                else duration
            )
            janelas.append((inicio, fim))
        return janelas

    # Proporcional ao tamanho do texto: cena maior fala por mais tempo.
    tamanhos = [max(1, len(str(s.get("text", "")))) for s in scenes]
    total = sum(tamanhos)
    janelas = []
    cursor = 0.0
    for tamanho in tamanhos:
        fatia = duration * tamanho / total
        janelas.append((cursor, cursor + fatia))
        cursor += fatia
    return janelas


def compose(
    base_video: Path,
    cuts: Sequence[BrollCut],
    library: Sequence[BrollAsset],
    destination: Path,
    *,
    width: int,
    height: int,
    fps: int,
) -> Path:
    """Queima os trechos de apoio por cima do vídeo base.

    Um `filter_complex` só, com um `overlay` por corte. Cada entrada é
    normalizada para o quadro (escala cobrindo e corte, igual ao render) e tem
    o PTS deslocado para o instante do corte, senão o apoio apareceria sempre a
    partir do próprio segundo zero.

    `eof_action=pass` e `shortest=0` são obrigatórios: sem eles, o primeiro
    trecho de apoio que termina encerra a saída inteira e o vídeo sai truncado.
    """
    por_id = {a.id: a for a in library}
    paths = get_paths()

    entradas: list[str] = ["-i", str(base_video)]
    partes: list[str] = []
    corrente = "0:v"

    for indice, corte in enumerate(cuts, start=1):
        asset = por_id.get(corte.asset_id)
        if asset is None:
            continue
        arquivo = paths.root / asset.path
        if not arquivo.exists():
            continue

        if asset.is_video:
            entradas += ["-i", str(arquivo)]
        else:
            # Imagem parada precisa virar stream com duração, senão o overlay
            # recebe um frame só e ele some no quadro seguinte.
            entradas += ["-loop", "1", "-t", f"{corte.duration:.3f}", "-i", str(arquivo)]

        rotulo = f"b{indice}"
        partes.append(
            f"[{indice}:v]"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps},"
            f"trim=duration={corte.duration:.3f},"
            f"setpts=PTS-STARTPTS+{corte.start:.3f}/TB[{rotulo}]"
        )
        saida = f"v{indice}"
        partes.append(
            f"[{corrente}][{rotulo}]"
            f"overlay=eof_action=pass:shortest=0:"
            f"enable='between(t,{corte.start:.3f},{corte.end:.3f})'[{saida}]"
        )
        corrente = saida

    if not partes:
        raise ValueError("Nenhum trecho de apoio utilizável.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run_ffmpeg(
        [
            *entradas,
            "-filter_complex", ";".join(partes),
            "-map", f"[{corrente}]",
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "16",
            "-pix_fmt", "yuv420p",
            str(destination),
        ]
    )
    return destination


def save_index(assets: Sequence[BrollAsset], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([a.to_dict() for a in assets], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
