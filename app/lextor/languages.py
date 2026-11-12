"""Language registry used by the format validator, the web UI and the MCP server."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str          # Spanish name (UI language)
    native: str        # Endonym
    script: str        # latin | cyrillic | greek | arabic | hebrew | devanagari | han | japanese | hangul | thai
    spaced: bool = True  # Are words separated by spaces?
    rtl: bool = False


_LANGS = [
    Language("es", "Español", "Español", "latin"),
    Language("en", "Inglés", "English", "latin"),
    Language("fr", "Francés", "Français", "latin"),
    Language("de", "Alemán", "Deutsch", "latin"),
    Language("it", "Italiano", "Italiano", "latin"),
    Language("pt", "Portugués", "Português", "latin"),
    Language("ca", "Catalán", "Català", "latin"),
    Language("gl", "Gallego", "Galego", "latin"),
    Language("eu", "Euskera", "Euskara", "latin"),
    Language("nl", "Neerlandés", "Nederlands", "latin"),
    Language("sv", "Sueco", "Svenska", "latin"),
    Language("da", "Danés", "Dansk", "latin"),
    Language("no", "Noruego", "Norsk", "latin"),
    Language("fi", "Finés", "Suomi", "latin"),
    Language("pl", "Polaco", "Polski", "latin"),
    Language("cs", "Checo", "Čeština", "latin"),
    Language("ro", "Rumano", "Română", "latin"),
    Language("hu", "Húngaro", "Magyar", "latin"),
    Language("tr", "Turco", "Türkçe", "latin"),
    Language("vi", "Vietnamita", "Tiếng Việt", "latin"),
    Language("id", "Indonesio", "Bahasa Indonesia", "latin"),
    Language("la", "Latín", "Latina", "latin"),
    Language("ru", "Ruso", "Русский", "cyrillic"),
    Language("uk", "Ucraniano", "Українська", "cyrillic"),
    Language("bg", "Búlgaro", "Български", "cyrillic"),
    Language("el", "Griego", "Ελληνικά", "greek"),
    Language("ar", "Árabe", "العربية", "arabic", rtl=True),
    Language("fa", "Persa", "فارسی", "arabic", rtl=True),
    Language("he", "Hebreo", "עברית", "hebrew", rtl=True),
    Language("hi", "Hindi", "हिन्दी", "devanagari"),
    Language("zh", "Chino", "中文", "han", spaced=False),
    Language("ja", "Japonés", "日本語", "japanese", spaced=False),
    Language("ko", "Coreano", "한국어", "hangul"),
    Language("th", "Tailandés", "ไทย", "thai", spaced=False),
]

LANGUAGES: dict[str, Language] = {l.code: l for l in _LANGS}

# Recommended romanization per script, shown to the model in the guide.
TRANSLIT_HINTS = {
    "cyrillic": "transliteración latina sencilla y legible (p. ej. «здравствуйте» → «zdravstvuyte»); marca la sílaba tónica con tilde aguda si ayuda (zdrávstvuyte)",
    "greek": "transliteración latina (p. ej. «καλημέρα» → «kaliméra»)",
    "arabic": "transliteración con vocales largas marcadas (p. ej. «كِتَاب» → «kitāb»)",
    "hebrew": "transliteración latina con vocales (p. ej. «שָׁלוֹם» → «shalom»)",
    "devanagari": "transliteración IAST o simplificada (p. ej. «नमस्ते» → «namaste»)",
    "han": "pinyin con marcas de tono (p. ej. «你好» → «nǐ hǎo»)",
    "japanese": "rōmaji Hepburn en `translit` (p. ej. «食べます» → «tabemasu») y lectura en hiragana en `reading` cuando haya kanji (たべます)",
    "hangul": "romanización revisada (p. ej. «안녕하세요» → «annyeonghaseyo»)",
    "thai": "romanización RTGS (p. ej. «สวัสดี» → «sawatdi»)",
}

_CODE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


def is_valid_code(code: str) -> bool:
    return bool(code) and bool(_CODE_RE.match(code))


def get(code: str) -> Language:
    """Return the language, or a generic latin-script fallback for unknown codes."""
    base = (code or "").split("-")[0].lower()
    if base in LANGUAGES:
        return LANGUAGES[base]
    return Language(base or "und", base or "?", base or "?", "latin")


def needs_translit(code: str) -> bool:
    return get(code).script != "latin"


def name(code: str) -> str:
    return get(code).name


def ui_list() -> list[dict]:
    """Languages for <select> menus, sorted with the most common first."""
    return [{"code": l.code, "name": l.name, "native": l.native} for l in _LANGS]
