"""Gerador de legendas estilo Reels/TikTok/Shorts.

Produz ASS (queimado no vídeo pelo FFmpeg) e SRT (auxiliar).

Por que ASS e não SRT no render: SRT não tem posicionamento, contorno, sombra
nem destaque por palavra. As legendas do MVP precisam disso — e precisam estar
queimadas, porque legendas automáticas da plataforma não são confiáveis nem
controláveis.

Destaque por palavra: em vez de usar tags de karaokê (`\\k`), que renderizam de
forma inconsistente entre players, cada estado é um evento `Dialogue` próprio
com a palavra ativa estilizada inline. É mais verboso no arquivo, e
absolutamente previsível no libass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from services.transcription.base import Transcript, Word

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptionPreset:
    """Aparência de um estilo de legenda.

    Tamanhos são frações da ALTURA do vídeo, então o mesmo preset funciona em
    1080x1920 e no preview 540x960 sem ajuste manual.
    """

    key: str
    label: str
    font: str = "Montserrat"
    font_size_ratio: float = 0.047
    bold: bool = True
    uppercase: bool = False
    primary_color: str = "&H00FFFFFF"       # branco (BGR com alfa)
    highlight_color: str = "&H0000E5FF"     # amarelo-âmbar
    outline_color: str = "&H00000000"
    back_color: str = "&HA0000000"
    outline: float = 3.5
    shadow: float = 1.2
    border_style: int = 1                   # 1 = contorno+sombra, 3 = caixa
    words_per_cue: int = 3
    # Posição vertical do centro do bloco, em fração da altura.
    vertical_anchor: float = 0.72
    scale_active: float = 1.0
    letter_spacing: float = 0.0
    max_chars_per_line: int = 22


PRESETS: dict[str, CaptionPreset] = {
    "minimal": CaptionPreset(
        key="minimal",
        label="Minimal",
        font="Inter",
        font_size_ratio=0.036,
        bold=False,
        outline=2.0,
        shadow=0.0,
        words_per_cue=5,
        highlight_color="&H00FFFFFF",
        vertical_anchor=0.78,
    ),
    "hormozi": CaptionPreset(
        key="hormozi",
        label="Hormozi",
        font="Montserrat",
        font_size_ratio=0.058,
        bold=True,
        uppercase=True,
        outline=5.0,
        shadow=2.0,
        words_per_cue=3,
        highlight_color="&H0000D7FF",
        scale_active=1.12,
        vertical_anchor=0.68,
        max_chars_per_line=18,
    ),
    "clean": CaptionPreset(
        key="clean",
        label="Clean",
        font="Inter",
        font_size_ratio=0.042,
        bold=True,
        outline=3.0,
        shadow=1.0,
        words_per_cue=4,
        highlight_color="&H0090EE90",
        vertical_anchor=0.74,
    ),
    "big_tech": CaptionPreset(
        key="big_tech",
        label="Big Tech",
        font="Inter",
        font_size_ratio=0.046,
        bold=True,
        border_style=3,
        back_color="&HC0000000",
        outline=1.0,
        shadow=0.0,
        words_per_cue=4,
        highlight_color="&H00F0A020",
        vertical_anchor=0.76,
    ),
    "podcast": CaptionPreset(
        key="podcast",
        label="Podcast",
        font="Inter",
        font_size_ratio=0.038,
        bold=False,
        outline=2.5,
        shadow=1.0,
        words_per_cue=5,
        highlight_color="&H00FFFFFF",
        vertical_anchor=0.82,
        max_chars_per_line=28,
    ),
    "karaoke": CaptionPreset(
        key="karaoke",
        label="Karaoke",
        font="Montserrat",
        font_size_ratio=0.050,
        bold=True,
        uppercase=True,
        outline=4.0,
        shadow=1.5,
        words_per_cue=4,
        primary_color="&H00B4B4B4",
        highlight_color="&H0000FFFF",
        scale_active=1.06,
        vertical_anchor=0.70,
    ),
}


# ---------------------------------------------------------------------------
# Agrupamento em cues
# ---------------------------------------------------------------------------


@dataclass
class Cue:
    words: list[Word] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words)


# Uma pausa maior que isso encerra o cue mesmo antes de encher.
_PAUSE_BREAK = 0.45
# Pontuação que naturalmente fecha um cue.
_HARD_PUNCTUATION = ".!?…"
_SOFT_PUNCTUATION = ",;:"


def group_words(
    words: Sequence[Word],
    *,
    words_per_cue: int = 3,
    max_chars: int = 26,
    max_duration: float = 3.0,
) -> list[Cue]:
    """Agrupa palavras em blocos legíveis.

    Quebra por: limite de palavras, limite de caracteres, pausa longa,
    pontuação forte e duração máxima. Isso evita o efeito comum de legendas
    automáticas em que o texto muda de forma arrítmica.
    """
    cues: list[Cue] = []
    current = Cue()

    def flush() -> None:
        nonlocal current
        if current.words:
            cues.append(current)
            current = Cue()

    for index, word in enumerate(words):
        cleaned = word.text.strip()
        if not cleaned:
            continue
        current.words.append(word)

        chars = len(current.text)
        stripped = cleaned.rstrip("\"')]}")
        ends_hard = stripped.endswith(tuple(_HARD_PUNCTUATION))
        ends_soft = stripped.endswith(tuple(_SOFT_PUNCTUATION))
        duration = current.end - current.start

        next_gap = 0.0
        if index + 1 < len(words):
            next_gap = words[index + 1].start - word.end

        if (
            len(current.words) >= words_per_cue
            or chars >= max_chars
            or ends_hard
            or (ends_soft and len(current.words) >= max(2, words_per_cue - 1))
            or next_gap >= _PAUSE_BREAK
            or duration >= max_duration
        ):
            flush()

    flush()
    return cues


# ---------------------------------------------------------------------------
# Geração de ASS
# ---------------------------------------------------------------------------


def _timestamp(seconds: float) -> str:
    """Formato ASS: H:MM:SS.cc (centésimos)."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _wrap(text: str, max_chars: int) -> str:
    """Quebra automática de linha usando `\\N` (quebra dura do ASS)."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return r"\N".join(lines)


def _escape(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "(").replace("}", ")")


def build_ass(
    transcript: Transcript,
    *,
    preset: CaptionPreset,
    width: int,
    height: int,
    uppercase: bool | None = None,
    words_per_cue: int | None = None,
    safe_bottom: float = 0.22,
    emphasis_words: Iterable[str] = (),
) -> str:
    """Monta o arquivo ASS completo com destaque por palavra."""
    words = [w for w in transcript.words if w.text.strip()]
    if not words:
        raise ValueError(
            "A transcrição não trouxe timestamps por palavra — sem isso não há "
            "legenda sincronizada."
        )

    use_upper = preset.uppercase if uppercase is None else uppercase
    per_cue = words_per_cue or preset.words_per_cue
    font_size = max(12, round(height * preset.font_size_ratio))
    emphasis = {w.strip().lower() for w in emphasis_words if w.strip()}

    # A âncora é medida do topo; o ASS mede a margem inferior a partir da base.
    margin_v = max(
        round(height * safe_bottom),
        round(height * (1.0 - preset.vertical_anchor)),
    )
    margin_h = round(width * 0.08)

    header = f"""[Script Info]
; Gerado pelo Local Clone Studio
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{preset.font},{font_size},{preset.primary_color},{preset.highlight_color},{preset.outline_color},{preset.back_color},{-1 if preset.bold else 0},0,0,0,100,100,{preset.letter_spacing},0,{preset.border_style},{preset.outline},{preset.shadow},2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    cues = group_words(
        words, words_per_cue=per_cue, max_chars=preset.max_chars_per_line + 6
    )

    lines: list[str] = []
    for cue in cues:
        for index, active in enumerate(cue.words):
            start = active.start
            # O último estado do cue se estende até o fim do cue; os demais
            # terminam quando a próxima palavra começa (sem buracos).
            if index + 1 < len(cue.words):
                end = max(cue.words[index + 1].start, start + 0.02)
            else:
                end = max(cue.end, start + 0.08)

            rendered: list[str] = []
            for position, word in enumerate(cue.words):
                text = _escape(word.text.strip())
                if use_upper:
                    text = text.upper()
                is_active = position == index
                is_emphasis = word.text.strip().lower().strip(".,!?;:") in emphasis

                if is_active:
                    tags = f"\\c{preset.highlight_color}"
                    if preset.scale_active != 1.0:
                        scale = round(preset.scale_active * 100)
                        tags += f"\\fscx{scale}\\fscy{scale}"
                    rendered.append(f"{{{tags}}}{text}{{\\r}}")
                elif is_emphasis:
                    rendered.append(f"{{\\c{preset.highlight_color}}}{text}{{\\r}}")
                else:
                    rendered.append(text)

            body = _wrap(" ".join(rendered), preset.max_chars_per_line + 8)
            lines.append(
                f"Dialogue: 0,{_timestamp(start)},{_timestamp(end)},Default,,0,0,0,,{body}"
            )

    return header + "\n".join(lines) + "\n"


def build_srt(transcript: Transcript, *, words_per_cue: int = 5) -> str:
    """SRT auxiliar (upload em plataformas, revisão, arquivamento)."""
    cues = group_words(
        [w for w in transcript.words if w.text.strip()],
        words_per_cue=words_per_cue,
        max_chars=42,
    )
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        start = _srt_timestamp(cue.start)
        end = _srt_timestamp(cue.end)
        blocks.append(f"{index}\n{start} --> {end}\n{cue.text}\n")
    return "\n".join(blocks)


def _srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_captions(
    transcript: Transcript,
    output_dir: Path,
    *,
    preset_key: str = "hormozi",
    width: int = 1080,
    height: int = 1920,
    uppercase: bool | None = None,
    words_per_cue: int | None = None,
    safe_bottom: float = 0.22,
    emphasis_words: Iterable[str] = (),
    basename: str = "captions",
) -> dict[str, Path]:
    """Escreve `captions.ass` + `captions.srt` e devolve os caminhos."""
    preset = PRESETS.get(preset_key)
    if preset is None:
        raise KeyError(
            f"Preset de legenda desconhecido: '{preset_key}'. "
            f"Disponíveis: {', '.join(PRESETS)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    ass_path = output_dir / f"{basename}.ass"
    srt_path = output_dir / f"{basename}.srt"

    ass_path.write_text(
        build_ass(
            transcript,
            preset=preset,
            width=width,
            height=height,
            uppercase=uppercase,
            words_per_cue=words_per_cue,
            safe_bottom=safe_bottom,
            emphasis_words=emphasis_words,
        ),
        encoding="utf-8",
    )
    srt_path.write_text(build_srt(transcript), encoding="utf-8")
    return {"ass": ass_path, "srt": srt_path}
