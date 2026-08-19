"""Normalização de texto para TTS em português brasileiro.

Um roteiro escrito não se lê como fala. Esta camada roda ANTES do TTS e trata:
números, siglas, URLs, símbolos, palavras em inglês e quebras de frase — para
que o modelo receba algo pronunciável em vez de "R$ 1.500,00 (via API)".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Números por extenso (pt-BR)
# ---------------------------------------------------------------------------

_UNITS = [
    "zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito",
    "nove", "dez", "onze", "doze", "treze", "quatorze", "quinze", "dezesseis",
    "dezessete", "dezoito", "dezenove",
]
_TENS = [
    "", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta",
    "oitenta", "noventa",
]
_HUNDREDS = [
    "", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos",
    "seiscentos", "setecentos", "oitocentos", "novecentos",
]


def _under_thousand(number: int) -> str:
    if number == 0:
        return ""
    if number == 100:
        return "cem"
    parts: list[str] = []
    hundreds, rest = divmod(number, 100)
    if hundreds:
        parts.append(_HUNDREDS[hundreds])
    if rest:
        if rest < 20:
            parts.append(_UNITS[rest])
        else:
            tens, units = divmod(rest, 10)
            parts.append(_TENS[tens] if not units else f"{_TENS[tens]} e {_UNITS[units]}")
    return " e ".join(parts)


def number_to_words(number: int) -> str:
    """Converte um inteiro para extenso em pt-BR (até bilhões)."""
    if number < 0:
        return "menos " + number_to_words(-number)
    if number < 20:
        return _UNITS[number]
    if number < 1000:
        return _under_thousand(number)

    scales = [
        (1_000_000_000, "bilhão", "bilhões"),
        (1_000_000, "milhão", "milhões"),
        (1_000, "mil", "mil"),
    ]
    for value, singular, plural in scales:
        if number >= value:
            count, rest = divmod(number, value)
            if value == 1_000:
                head = "mil" if count == 1 else f"{number_to_words(count)} mil"
            else:
                head = f"{number_to_words(count)} {singular if count == 1 else plural}"
            if not rest:
                return head
            separator = " e " if rest < 100 or rest % 100 == 0 else " "
            return f"{head}{separator}{number_to_words(rest)}"
    return str(number)


def _decimal_to_words(integer_part: str, decimal_part: str) -> str:
    integer = number_to_words(int(integer_part))
    digits = " ".join(_UNITS[int(d)] for d in decimal_part)
    return f"{integer} vírgula {digits}"


# ---------------------------------------------------------------------------
# Dicionários de pronúncia
# ---------------------------------------------------------------------------

# Siglas que se leem letra a letra (soletradas em pt-BR).
_SPELLED_ACRONYMS = {
    "AI", "IA", "API", "CPU", "GPU", "RAM", "SSD", "HTTP", "HTTPS", "URL",
    "HTML", "CSS", "SQL", "PDF", "USB", "CEO", "CTO", "CFO", "RH", "PIX",
    "CNPJ", "CPF", "MEI", "IPTU", "IPVA", "FGTS", "INSS", "SUS", "TI",
    "SDK", "IDE", "CLI", "UI", "UX", "SEO", "CRM", "ERP", "KPI", "ROI",
    "B2B", "B2C", "SaaS", "LLM", "RAG", "MVP", "PR", "QA", "OS", "PC",
}

_LETTER_SOUNDS = {
    "A": "a", "B": "bê", "C": "cê", "D": "dê", "E": "é", "F": "éfe",
    "G": "gê", "H": "agá", "I": "i", "J": "jota", "K": "cá", "L": "éle",
    "M": "eme", "N": "ene", "O": "ó", "P": "pê", "Q": "quê", "R": "érre",
    "S": "ésse", "T": "tê", "U": "u", "V": "vê", "W": "dábliu", "X": "xis",
    "Y": "ípsilon", "Z": "zê",
}

# Termos técnicos em inglês grafados como se pronunciam em pt-BR.
_ENGLISH_TERMS = {
    "software": "sófitiuér",
    "hardware": "rárdiuér",
    "deploy": "deploi",
    "startup": "startâp",
    "startups": "startâps",
    "insight": "ínsait",
    "insights": "ínsaits",
    "marketing": "márquetin",
    "design": "dizáin",
    "feedback": "fídibéque",
    "mindset": "máindset",
    "growth": "gróuf",
    "prompt": "prompt",
    "hype": "raipe",
    "cloud": "cláud",
    "backend": "béquend",
    "frontend": "frontend",
    "framework": "fréimuork",
    "bug": "bâgue",
    "bugs": "bâgues",
    "deadline": "dédlain",
    "budget": "bâdget",
    "coach": "côutch",
    "player": "pleiêr",
    "players": "pleiêrs",
}

_SYMBOLS = {
    "%": " por cento",
    "&": " e ",
    "@": " arroba ",
    "+": " mais ",
    "=": " igual a ",
    "€": " euros",
    "£": " libras",
    "#": " hashtag ",
}


@dataclass
class TextPrepOptions:
    expand_numbers: bool = True
    expand_acronyms: bool = True
    expand_english: bool = True
    strip_markdown: bool = True
    add_breathing_pauses: bool = True
    max_sentence_chars: int = 220


# ---------------------------------------------------------------------------
# Etapas
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_MARKDOWN_RE = re.compile(r"[*_`~#>]{1,3}")
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_MONEY_RE = re.compile(r"R\$\s?(\d[\d.]*)(?:,(\d{1,2}))?")
_PERCENT_RE = re.compile(r"(\d[\d.]*)(?:,(\d+))?\s?%")
_ORDINAL_RE = re.compile(r"\b(\d+)[ºª°]")
_DECIMAL_RE = re.compile(r"\b(\d[\d.]*),(\d+)\b")
_INTEGER_RE = re.compile(r"\b\d[\d.]*\b")
_DOTTED_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+){2,}\b")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")


def _clean_urls(text: str) -> str:
    text = _EMAIL_RE.sub(lambda m: m.group(0).replace("@", " arroba ").replace(".", " ponto "), text)

    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        domain = re.sub(r"^https?://", "", url).split("/")[0]
        domain = domain.removeprefix("www.")
        spoken = domain.replace(".", " ponto ").replace("-", " traço ")
        return spoken

    return _URL_RE.sub(replace_url, text)


def _expand_money(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        reais = int(match.group(1).replace(".", ""))
        centavos = match.group(2)
        words = f"{number_to_words(reais)} {'real' if reais == 1 else 'reais'}"
        if centavos:
            cents = int(centavos.ljust(2, "0"))
            if cents:
                words += f" e {number_to_words(cents)} {'centavo' if cents == 1 else 'centavos'}"
        return words

    return _MONEY_RE.sub(replace, text)


def _expand_percent(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        whole = int(match.group(1).replace(".", ""))
        frac = match.group(2)
        if frac:
            return f"{_decimal_to_words(str(whole), frac)} por cento"
        return f"{number_to_words(whole)} por cento"

    return _PERCENT_RE.sub(replace, text)


def _expand_time(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return match.group(0)
        words = f"{number_to_words(hour)} {'hora' if hour == 1 else 'horas'}"
        if minute:
            words += f" e {number_to_words(minute)} {'minuto' if minute == 1 else 'minutos'}"
        return words

    return _TIME_RE.sub(replace, text)


_ORDINAL_WORDS = [
    "", "primeiro", "segundo", "terceiro", "quarto", "quinto", "sexto",
    "sétimo", "oitavo", "nono", "décimo",
]


def _expand_ordinals(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = int(match.group(1))
        if 1 <= value <= 10:
            return _ORDINAL_WORDS[value]
        return f"{number_to_words(value)}"

    return _ORDINAL_RE.sub(replace, text)


def _expand_numbers(text: str) -> str:
    text = _expand_money(text)
    text = _expand_percent(text)
    text = _expand_time(text)
    text = _expand_ordinals(text)

    # Versões e endereços pontuados não são separadores de milhar: "1.2.3"
    # deve soar "um ponto dois ponto três", nunca "cento e vinte e três".
    text = _DOTTED_NUMBER_RE.sub(
        lambda match: " ponto ".join(
            number_to_words(int(part)) for part in match.group(0).split(".")
        ),
        text,
    )

    def replace_decimal(match: re.Match[str]) -> str:
        return _decimal_to_words(match.group(1).replace(".", ""), match.group(2))

    text = _DECIMAL_RE.sub(replace_decimal, text)

    def replace_integer(match: re.Match[str]) -> str:
        raw = match.group(0).replace(".", "")
        if not raw.isdigit():
            return match.group(0)
        # Anos (1900-2099) soam melhor por extenso normal em pt-BR.
        value = int(raw)
        if value > 999_999_999_999:
            return " ".join(_UNITS[int(d)] for d in raw)
        return number_to_words(value)

    return _INTEGER_RE.sub(replace_integer, text)


def _spell_acronym(token: str) -> str:
    return " ".join(_LETTER_SOUNDS.get(ch.upper(), ch) for ch in token)


_KNOWN_ACRONYMS_UPPER = {a.upper() for a in _SPELLED_ACRONYMS}
_VOWELS = set("AEIOUÁÉÍÓÚÂÊÔÃÕÀ")


def _expand_acronyms(text: str) -> str:
    """Soletra siglas — sem destruir palavras escritas em CAIXA ALTA.

    Roteiros virais usam maiúsculas para ênfase ("isso NUNCA funciona"). Uma
    regra ingênua de "2-5 maiúsculas = sigla" transformaria isso em
    "ene u ene cê a". Só soletramos quando a sigla é conhecida ou quando o
    token não tem vogal (PDF, SMS, TV), que é o sinal mais confiável.
    """

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        stripped = token.replace(".", "")
        if stripped.upper() in _KNOWN_ACRONYMS_UPPER:
            return _spell_acronym(stripped)
        if 2 <= len(stripped) <= 4 and not (set(stripped.upper()) & _VOWELS):
            return _spell_acronym(stripped)
        return token

    # Inclui siglas conhecidas mistas/digitais (SaaS, B2B), mantendo palavras
    # comuns intactas pelo teste dentro de ``replace``.
    return re.sub(r"\b[A-Za-z][A-Za-z0-9.]{1,5}\b", replace, text)


def _expand_english(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        replacement = _ENGLISH_TERMS.get(word.lower())
        if not replacement:
            return word
        return replacement.capitalize() if word[0].isupper() else replacement

    pattern = r"\b(" + "|".join(sorted(_ENGLISH_TERMS, key=len, reverse=True)) + r")\b"
    return re.sub(pattern, replace, text, flags=re.IGNORECASE)


def _apply_symbols(text: str) -> str:
    for symbol, spoken in _SYMBOLS.items():
        text = text.replace(symbol, spoken)
    return text


def split_sentences(text: str) -> list[str]:
    """Divide em frases respeitando abreviações comuns."""
    protected = re.sub(
        r"\b(Sr|Sra|Dr|Dra|Prof|etc|ex|vs|pág|art)\.", r"\1<DOT>", text
    )
    pieces = re.split(r"(?<=[.!?…])\s+", protected)
    return [p.replace("<DOT>", ".").strip() for p in pieces if p.strip()]


def _add_breathing(text: str) -> str:
    """Insere respiros discretos onde a fala natural pausaria.

    Vírgulas longas e conjunções ganham reticências curtas, que o Qwen3-TTS
    interpreta como pausa sem alterar o conteúdo.
    """
    text = re.sub(r",\s+(mas|porém|só que|e aí|então|porque)\b", r", \1", text)
    return text


def normalize_for_tts(
    text: str, options: TextPrepOptions | None = None
) -> str:
    """Pipeline completo de normalização."""
    opts = options or TextPrepOptions()

    result = unicodedata.normalize("NFC", text)
    result = result.replace(" ", " ")

    if opts.strip_markdown:
        result = _MARKDOWN_RE.sub("", result)
        result = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", result)

    result = _clean_urls(result)
    if opts.expand_numbers:
        result = _expand_numbers(result)
    if opts.expand_acronyms:
        result = _expand_acronyms(result)
    result = _apply_symbols(result)
    if opts.expand_english:
        result = _expand_english(result)
    if opts.add_breathing_pauses:
        result = _add_breathing(result)

    result = _MULTISPACE_RE.sub(" ", result)
    result = re.sub(r"\s+([,.!?;:])", r"\1", result)
    result = re.sub(r"\n{2,}", "\n", result)
    return result.strip()


def chunk_for_synthesis(text: str, max_chars: int = 220) -> list[str]:
    """Agrupa frases em blocos sintetizáveis.

    Blocos muito longos degradam a prosódia e aumentam o risco de alucinação
    no TTS autoregressivo; muito curtos quebram a entonação. ~220 caracteres
    é um meio-termo estável.
    """
    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        if not current:
            current = sentence
        elif len(current) + len(sentence) + 1 <= max_chars:
            current = f"{current} {sentence}"
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)

    # Frase isolada maior que o limite: quebra em vírgulas.
    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars * 1.6:
            final.append(chunk)
            continue
        buffer = ""
        for piece in chunk.split(", "):
            candidate = f"{buffer}, {piece}" if buffer else piece
            if len(candidate) > max_chars and buffer:
                final.append(buffer)
                buffer = piece
            else:
                buffer = candidate
        if buffer:
            final.append(buffer)

    # Uma frase sem vírgulas ainda pode ser arbitrariamente longa. Faz o
    # último recurso por palavras, sem perder conteúdo nem cortar Unicode.
    bounded: list[str] = []
    for chunk in final:
        if len(chunk) <= max_chars * 1.6:
            bounded.append(chunk)
            continue
        current_words = ""
        for word in chunk.split():
            candidate = f"{current_words} {word}".strip()
            if current_words and len(candidate) > max_chars:
                bounded.append(current_words)
                current_words = word
            else:
                current_words = candidate
        if current_words:
            bounded.append(current_words)
    return bounded
