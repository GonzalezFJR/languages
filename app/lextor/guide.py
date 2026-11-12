"""Builds the model-facing format guide, specialised for a language pair."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.lextor import languages

_DIR = Path(__file__).parent

_SCRIPT_EXAMPLES = {
    "cyrillic": ('ru', {"text": "Я люблю читать", "gloss": "Me encanta leer", "translit": "ya lyublyú chitát'",
                         "note": "«Люблю» (1ª sg de любить) + infinitivo = me gusta / me encanta hacer algo."}),
    "greek": ('el', {"text": "Καλημέρα", "gloss": "Buenos días", "translit": "kaliméra",
                      "note": "De καλή (buena) + μέρα (día). Se usa hasta el mediodía."}),
    "arabic": ('ar', {"text": "في البيت", "gloss": "en la casa", "translit": "fī al-bayt",
                       "note": "«في» = en. «البيت» lleva el artículo «ال» (al-); ante consonante solar se asimila."}),
    "hebrew": ('he', {"text": "בַּבַּיִת", "gloss": "en casa", "translit": "babáyit",
                       "note": "Prefijo «בְּ» (en) + artículo fundidos: «ba-»."}),
    "devanagari": ('hi', {"text": "मेरा नाम", "gloss": "mi nombre", "translit": "merā nām",
                           "note": "«मेरा» = mi (masc.), concuerda con «नाम», masculino."}),
    "han": ('zh', {"text": "我喜欢", "gloss": "me gusta", "translit": "wǒ xǐhuan",
                    "note": "«喜欢» (xǐhuan) = gustar; el sujeto es quien siente el gusto, al revés que en español."}),
    "japanese": ('ja', {"text": "猫が", "gloss": "el gato (sujeto)", "reading": "ねこが", "translit": "neko ga",
                         "note": "«が» marca el sujeto gramatical; aquí presenta información nueva."}),
    "hangul": ('ko', {"text": "학교에", "gloss": "a la escuela", "translit": "hakgyo-e",
                       "note": "«에» indica destino o lugar estático."}),
    "thai": ('th', {"text": "ขอบคุณ", "gloss": "gracias", "translit": "khop khun",
                     "note": "Se suele añadir ครับ (hombres) o ค่ะ (mujeres) por cortesía."}),
}


@lru_cache(maxsize=1)
def _base() -> str:
    return (_DIR / "guide.md").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def example_document() -> dict:
    return json.loads((_DIR / "examples" / "fr-es.json").read_text(encoding="utf-8"))


def _script_rules(text_lang: str | None) -> str:
    lines = []
    lang = languages.get(text_lang) if text_lang else None
    if lang and lang.script != "latin":
        hint = languages.TRANSLIT_HINTS.get(lang.script, "")
        code, ex = _SCRIPT_EXAMPLES.get(lang.script, (None, None))
        lines.append(f"\n**This text is in {lang.native} ({lang.script} script).** Use {hint}.")
        if ex:
            lines.append(f"\nExample chunk:\n\n```json\n{json.dumps(ex, ensure_ascii=False)}\n```")
        if lang.script == "japanese":
            lines.append("\nPut the kana reading of any chunk containing kanji in `reading` (furigana) and "
                         "Hepburn rōmaji in `translit`. Omit `reading` for kana-only chunks.")
        if lang.rtl:
            lines.append("\nThe reader handles right-to-left layout; write the text in normal logical order.")
        return "\n".join(lines)
    if lang:
        return f"\n{lang.native} uses the Latin script: omit `translit`."
    # generic: list all
    lines.append("\nRecommended romanizations:")
    for script, hint in languages.TRANSLIT_HINTS.items():
        lines.append(f"- **{script}**: {hint}")
    lines.append("\nJapanese also uses `reading` (kana) for furigana on chunks with kanji.")
    return "\n".join(lines)


def build_guide(text_lang: str | None = None, notes_lang: str | None = None, level: str | None = None) -> str:
    doc = example_document()
    guide = _base()
    guide = guide.replace("{{SCRIPT_RULES}}", _script_rules(text_lang))
    guide = guide.replace("{{EXAMPLE_PAIR}}", "French text, Spanish notes")
    guide = guide.replace("{{EXAMPLE_JSON}}", json.dumps(doc, ensure_ascii=False, indent=1))
    header = []
    if text_lang or notes_lang:
        tl = languages.get(text_lang) if text_lang else None
        nl = languages.get(notes_lang) if notes_lang else None
        header.append("> **For this request:** "
                      + (f"text_lang = `{text_lang}` ({tl.native})" if tl else "")
                      + (", " if tl and nl else "")
                      + (f"notes_lang = `{notes_lang}` ({nl.native}) — write all glosses and notes in "
                         f"{nl.native}" if nl else "")
                      + (f"; level {level}" if level else "") + ".")
        if notes_lang and notes_lang != "es":
            header.append("> The example below has Spanish notes; yours must be in notes_lang.")
        header.append("")
    return "\n".join(header) + guide


SERVER_INSTRUCTIONS = """Lextor creates interactive bilingual reading texts for language learners.
When the user wants to read, study, translate-for-learning or annotate a text in another language:
1) call lextor_get_format_guide (once) to learn the exact lextor/1 JSON format;
2) build the document — original sentences copied exactly, a natural translation per sentence, and short
   chunks (1–4 words) each with a contextual gloss and a teaching note in the learner's language;
   no line breaks inside strings (use paragraph / verse / heading blocks);
3) save it with lextor_create_text (long texts: continue with lextor_append_blocks), fix any validation
   errors it reports, and share the returned link with the user.
Use lextor_list_projects / lextor_list_texts / lextor_get_text to browse or continue existing work."""
