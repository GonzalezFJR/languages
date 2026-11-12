# Lextor format guide (`lextor/1`)

Lextor turns a text into an **interactive bilingual reader** for language learners. The learner reads the
original text; tapping or hovering any fragment shows its contextual translation and a short teaching
note. A parallel view shows a natural translation sentence by sentence.

Your job: produce one JSON document in the format below and send it with `lextor_create_text`.
The server validates it, fixes small issues automatically and tells you exactly what to correct.

**Vocabulary.** `text_lang` is the language of the text being studied. `notes_lang` is the learner's
language: write **every** `translation`, `gloss`, `note`, `grammar`, `literal` and `summary` in `notes_lang`.

---

## 1. Document shape

```json
{
  "meta": {
    "title": "Original title (in text_lang)",
    "title_translation": "Title in notes_lang",
    "text_lang": "fr",
    "notes_lang": "es",
    "level": "A2",
    "summary": "One or two sentences in notes_lang about the content.",
    "author": "optional", "source": "optional"
  },
  "blocks": [
    {"type": "heading", "level": 1, "sentences": [ ... ]},
    {"type": "paragraph", "sentences": [ ... ]},
    {"type": "verse", "sentences": [ ... ]}
  ]
}
```

### Blocks: structure instead of line breaks

| type | use for | rendering |
|---|---|---|
| `heading` (`level` 1–3) | titles, chapter and section headings | one line, large |
| `paragraph` | prose, dialogue turns, quotes | sentences flow on the same line |
| `verse` | poems, songs, lists, scripts, addresses, anything where **line breaks matter** | **each sentence is its own line** |

- A new paragraph or a new dialogue turn is a new `paragraph` block.
- A blank line between stanzas means a new `verse` block.
- **Never** put `\n`, tabs or Markdown in any string. Line structure is expressed only with blocks.

### Sentences

```json
{
  "text": "Elle ne prend jamais de sucre.",
  "translation": "Nunca toma azúcar.",
  "note": "optional: idiom, cultural context or a point about the whole sentence",
  "chunks": [ ... ]
}
```

- `text`: the sentence (or verse line) **copied exactly** from the source, including its punctuation.
  Keep the source's spelling, quotes and spacing conventions (e.g. French `« … ? »`).
- `translation`: a **natural, fluent** translation of the whole sentence, as a good translator would
  write it. This is what the parallel view shows, so it must read well on its own.
- Split long sentences (more than ~35 words) into several sentences when the punctuation allows.

### Chunks: minimal units of meaning

```json
{"text": "prend", "gloss": "toma", "lemma": "prendre", "grammar": "presente · 3ª sg",
 "note": "«Prendre» se usa para comidas y bebidas: prendre un café = tomar un café."}
```

| field | required | content |
|---|---|---|
| `text` | yes | an **exact substring** of the sentence `text` |
| `gloss` | yes | the translation of this fragment **in this context** — one option only, never `a / b` |
| `note` | most chunks | 1–3 short sentences that teach something (see §3) |
| `translit` | non-Latin scripts | romanization of the fragment (see §4) |
| `reading` | Japanese with kanji | kana reading for furigana |
| `lemma` | when inflected | dictionary form (infinitive, nominative singular…) |
| `grammar` | when useful | compact tag: `verbo · pretérito · 3ª pl`, `sust. fem. · dativo` |
| `literal` | when the gloss is idiomatic | word-for-word meaning |
| `group` | rarely | same id on separated pieces that form one unit: `ne … pas`, German separable verbs (`rufe … an`), split phrasal verbs |

**The three alignment rules** (checked automatically):

1. Each chunk `text` is copied **character for character** from its sentence `text`.
2. Chunks appear **in order** and do not overlap.
3. **Every word** of the sentence belongs to exactly one chunk. Spaces between chunks are never written:
   the server rebuilds the spacing from the sentence. Punctuation may be attached to the neighbouring
   chunk (recommended) or left out.

---

## 2. How to segment

A chunk is the **smallest piece that still carries meaning in context** — usually 1 to 4 words,
never more than 6.

- Keep together what is learned together: articles with their noun (`la fenêtre`), set phrases and
  idioms (`s'il vous plaît`, `by the way`, `il y a`), compound verb forms (`ha comido`, `have been`),
  phrasal verbs (`give up`), prepositional phrases when short (`près de`).
- Split at natural boundaries: commas, conjunctions, clause starts, subject / verb / object.
- A single word is fine when it deserves its own explanation (a verb, a false friend, a particle).
- Names and trivial words may share a chunk with a neighbour; don't create chunks without content.
- Languages without spaces (Chinese, Japanese, Thai): chunk by word or short expression
  (≤ 8 characters). Japanese particles may go with their word (`猫が`) or alone when you explain them.

---

## 3. What makes a good note

The note is the heart of Lextor. Write it for a learner at the document's `level`, in `notes_lang`,
in plain text (use «quotes» or 'quotes' for words, no Markdown). Prefer what is **useful and
specific** to this fragment:

- grammar that explains the form: tense, case, agreement, why this preposition or mood;
- meaning in context vs. literal meaning; register (formal, colloquial, archaic);
- false friends and contrasts with `notes_lang`;
- a reusable pattern or collocation, a short related example;
- pronunciation traps; cultural context when relevant.

Don't repeat the gloss, don't write generic filler ("this is a word"), and don't explain the same
point again on every occurrence — refer back briefly ("como antes, partitivo").
For A1–A2 explain basics; for B2–C2 focus on nuance, register and idiom.
Trivial chunks (names, numbers, an article already explained) can omit the note.

---

## 4. Scripts and transliteration

Texts in non-Latin scripts **must** include `translit` on every chunk with non-Latin letters.
{{SCRIPT_RULES}}

---

## 5. Workflow with the tools

1. `lextor_get_format_guide` once per conversation (you are reading it).
2. Pick the project: `lextor_list_projects`. If none matches the language pair, `lextor_create_text`
   creates one automatically (or call `lextor_create_project`).
3. Build the document and call `lextor_create_text`. The response contains the validation report.
   - If it **fails**, fix exactly the listed paths and call again. Warnings don't block saving but
     improve quality — fix them when cheap.
   - Use `lextor_validate` first if you want to test without saving.
4. **Long texts** (more than ~40 sentences or ~600 words): send the first part with
   `lextor_create_text`, then the rest with `lextor_append_blocks` in batches of ~30 sentences,
   continuing exactly where you stopped, and finally `lextor_show_text` to display the whole text.
   Never summarize or skip parts of the source.
5. Tell the user the result is ready. The response includes a link to open the text in Lextor;
   always share it. If the chat shows the interactive reader, the user can read there directly.

Process the **whole** text the user gives you, faithfully. If the source has obvious typos or OCR
errors (e.g. text from a photo), correct them in `text` and mention the correction in the note.
If the user only gives a topic, you may write an original text at the requested level.

---

## 6. Complete example ({{EXAMPLE_PAIR}})

```json
{{EXAMPLE_JSON}}
```
