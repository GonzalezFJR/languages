import copy
import json

from app.lextor.document import validate_document, plain_text
from app.lextor.legacy import xlan_to_lextor


def _doc(sentences, text_lang="fr", notes_lang="es", btype="paragraph"):
    return {"meta": {"title": "T", "text_lang": text_lang, "notes_lang": notes_lang},
            "blocks": [{"type": btype, "sentences": sentences}]}


def codes(rep):
    return {i.code for i in rep.errors}, {i.code for i in rep.warnings}, {i.code for i in rep.fixes}


def test_example_is_perfect(example_doc):
    rep = validate_document(example_doc)
    assert rep.ok and not rep.warnings and not rep.fixes, rep.as_text()
    assert all(c["status"] == "pass" for c in rep.checks())


def test_accepts_json_string_and_code_fence(example_doc):
    rep = validate_document("```json\n" + json.dumps(example_doc) + "\n```")
    assert rep.ok


def test_chunk_must_exist_in_order():
    rep = validate_document(_doc([{"text": "Le chat dort.", "translation": "El gato duerme.", "chunks": [
        {"text": "dort.", "gloss": "duerme."}, {"text": "Le chat", "gloss": "El gato"}]}]))
    errors, _, _ = codes(rep)
    assert "E_CHUNK_NOT_FOUND" in errors


def test_fuzzy_match_fixes_quotes_and_case():
    rep = validate_document(_doc([{"text": "C’est l’heure.", "translation": "Es la hora.", "chunks": [
        {"text": "c'est", "gloss": "es"}, {"text": "l'heure.", "gloss": "la hora."}]}]))
    assert rep.ok, rep.as_text()
    _, _, fixes = codes(rep)
    assert "F_CHUNK_MATCH" in fixes
    assert rep.doc["blocks"][0]["sentences"][0]["chunks"][0]["text"] == "C’est"


def test_newlines_become_warnings_not_data():
    rep = validate_document(_doc([{"text": "Il pleut\nsur la ville", "translation": "Llueve sobre la ciudad",
                                   "chunks": [{"text": "Il pleut\n", "gloss": "Llueve"},
                                              {"text": "sur la ville", "gloss": "sobre la ciudad"}]}]))
    assert rep.ok
    _, warns, _ = codes(rep)
    assert "W_NEWLINE" in warns
    assert "\n" not in json.dumps(rep.doc["blocks"]).replace("\\n", "")
    assert rep.doc["blocks"][0]["sentences"][0]["text"] == "Il pleut sur la ville"


def test_uncovered_words():
    # one uncovered word → warning, many → error
    small = validate_document(_doc([{"text": "Le chat dort bien.", "translation": "x", "chunks": [
        {"text": "Le chat", "gloss": "El gato"}, {"text": "bien.", "gloss": "bien."}]}]))
    assert small.ok and "W_UNCOVERED" in codes(small)[1]
    big = validate_document(_doc([{"text": "Le chat noir dort très bien ce matin.", "translation": "x", "chunks": [
        {"text": "Le chat", "gloss": "El gato"}]}]))
    assert not big.ok and "E_UNCOVERED" in codes(big)[0]


def test_tuples_and_missing_translation_are_repaired():
    rep = validate_document(_doc([{"text": "Bonjour Marie", "chunks": [["Bonjour", "Hola", "Saludo"], ["Marie", "María"]]}]))
    assert rep.ok
    s = rep.doc["blocks"][0]["sentences"][0]
    assert s["chunks"][0] == {"text": "Bonjour", "gloss": "Hola", "note": "Saludo"}
    assert s["translation"] == "Hola María"
    assert "W_NO_TRANSLATION" in codes(rep)[1]


def test_granularity_and_alternatives():
    rep = validate_document(_doc([{"text": "Je suis allé au marché avec mes amis hier.", "translation": "x",
                                   "chunks": [{"text": "Je suis allé au marché avec mes amis hier.",
                                               "gloss": "fui / estuve"}]}]))
    _, warns, _ = codes(rep)
    assert {"W_LONG_CHUNK", "W_UNSPLIT", "W_ALTERNATIVES"} <= warns


def test_translit_required_for_non_latin():
    rep = validate_document(_doc([{"text": "Я люблю", "translation": "Me encanta", "chunks": [
        {"text": "Я", "gloss": "yo"}, {"text": "люблю", "gloss": "amo", "translit": "lyublyú"}]}], text_lang="ru"))
    assert rep.ok
    w = [i for i in rep.warnings if i.code == "W_NO_TRANSLIT"]
    assert len(w) == 1 and "chunks[0]" in w[0].path


def test_japanese_unspaced_granularity():
    rep = validate_document(_doc([{"text": "猫が好きです。", "translation": "Me gustan los gatos.", "chunks": [
        {"text": "猫が", "gloss": "los gatos", "reading": "ねこが", "translit": "neko ga", "note": "が marca"},
        {"text": "好きです。", "gloss": "me gustan", "translit": "suki desu", "note": "好き = gustar"}]}],
        text_lang="ja"))
    assert rep.ok and not rep.warnings, rep.as_text()


def test_language_errors():
    assert not validate_document(_doc([], text_lang="es", notes_lang="es")).ok
    assert not validate_document({"meta": {"title": "x", "text_lang": "french", "notes_lang": "es"},
                                  "blocks": []}).ok


def test_block_aliases_and_groups():
    rep = validate_document({"meta": {"title": "t", "text_lang": "fr", "notes_lang": "es"}, "blocks": [
        {"type": "poem", "sentences": [{"text": "Je ne sais pas", "translation": "No sé", "chunks": [
            {"text": "Je", "gloss": "yo"}, {"text": "ne", "gloss": "no", "group": "n"},
            {"text": "sais", "gloss": "sé"}, {"text": "pas", "gloss": "(neg)", "group": "solo"}]}]}]})
    assert rep.ok
    b = rep.doc["blocks"][0]
    assert b["type"] == "verse"
    assert "group" not in b["sentences"][0]["chunks"][1]  # single-member groups are dropped
    assert "W_GROUP_SINGLE" in codes(rep)[1]


def test_legacy_conversion_handles_spaces_and_linebreaks():
    xlan = {"meta": {"title": "Poema", "text_language": "fr", "notes_language": "es"}, "content": [
        {"type": "paragraph", "segments": [
            {"id": "a", "text": "Il pleut\n", "translation": "Llueve\n", "info": "x"},
            {"id": "b", "text": "sur la ville\n\n", "translation": "sobre la ciudad", "info": "y",
             "user_note": {"highlight_color": "yellow", "comment": "bonito"}},
            {"id": "c", "text": "Fin.", "translation": "Fin.", "info": "z"},
        ]}]}
    doc = xlan_to_lextor(xlan)
    rep = validate_document(doc)
    assert rep.ok, rep.as_text()
    assert [b["type"] for b in doc["blocks"]] == ["verse", "paragraph"]
    assert doc["blocks"][0]["sentences"][1]["text"] == "sur la ville"
    assert doc["annotations"] == {"b0.s1.c0": {"color": "yellow", "comment": "bonito"}}
    assert plain_text(doc) == "Il pleut\nsur la ville\n\nFin."


def test_does_not_mutate_input(example_doc):
    before = copy.deepcopy(example_doc)
    validate_document(example_doc)
    assert example_doc == before
