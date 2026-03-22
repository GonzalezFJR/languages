"""
pipeline_agent.py — LLM agent (via litellm) that builds .xlan file(s) from text.

Uses a single tool `create_xlan` that takes a list of tuples:
  [(original_text, translation, explanation), ...]
For long texts (>3000 chars) the text is split into batches.
Provider-agnostic via litellm.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import litellm

from app.config import settings
from app.services.xlan_service import save_xlan, _slugify
from app.services.project_service import get_project_path

BATCH_CHARS = 3000
logger = logging.getLogger(__name__)

def _is_debug() -> bool:
    return settings.debug and settings.app_env == "development"


# ── Tool schema — single create_xlan tool ────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "create_xlan",
            "description": (
                "Create the .xlan file from a list of tuples. "
                "Each tuple has exactly 3 string elements: "
                "[original_fragment, translation, didactic_explanation]. "
                "Call this function ONCE with ALL tuples covering the entire text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tuples": {
                        "type": "array",
                        "description": "List of [fragment, translation, explanation] tuples.",
                        "items": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 3,
                            "maxItems": 3,
                        },
                    },
                },
                "required": ["tuples"],
            },
        },
    },
]


# ── XlanSession — mutable state shared with the tool-calling loop ──

class XlanSession:
    def __init__(
        self,
        project_id: str,
        title: str,
        description: str,
        text_language: str,
        notes_language: str,
        user_dir: str = "public",
        source_type: str = "text",       # "text" | "file" | "ocr"
        extend_base: str | None = None,  # base filename to extend (e.g. "my_text.xlan")
    ):
        self.project_id = project_id
        self.title = title
        self.description = description
        self.text_language = text_language
        self.notes_language = notes_language
        self.user_dir = user_dir
        self.source_type = source_type
        self.extend_base = extend_base
        self.content: list[dict] = []
        self.seg_counter = 0
        self.saved_filenames: list[str] = []
        self.on_progress: Optional[Callable[[str], None]] = None

    # ── progress helper ──────────────────────────────────────────

    def _emit(self, msg: str) -> None:
        if self.on_progress:
            self.on_progress(msg)

    # ── tool dispatch ────────────────────────────────────────────

    def call_tool(self, name: str, args: dict) -> str:
        if name == "create_xlan":
            return self._create_xlan(args.get("tuples", []))
        return f"ERROR: unknown tool '{name}'"

    # ── tool implementation ──────────────────────────────────────

    def _create_xlan(self, tuples: list[list[str]]) -> str:
        """Build .xlan content from a list of [text, translation, info] tuples."""
        current_segs: list[dict] = []

        for t in tuples:
            if len(t) < 3:
                continue
            text, translation, info = t[0], t[1], t[2]

            self.seg_counter += 1
            current_segs.append({
                "id": f"seg_{self.seg_counter}",
                "text": text,
                "translation": translation,
                "info": info,
                "styles": [],
            })

            # Paragraph break on double newline
            if text.rstrip(" ").endswith("\n\n"):
                self.content.append({"type": "paragraph", "segments": current_segs})
                current_segs = []

        if current_segs:
            self.content.append({"type": "paragraph", "segments": current_segs})

        self._emit(f"✓ {len(self.content)} paragraphs, {self.seg_counter} segments")
        return f"OK: {len(self.content)} paragraphs, {self.seg_counter} segments"

    # ── line-break restoration ────────────────────────────────────

    def _restore_line_breaks(self, original_text: str) -> None:
        """Post-process segments so that \n from *original_text* are preserved,
        regardless of whether the LLM included them or not."""
        all_segs: list[dict] = []
        for block in self.content:
            all_segs.extend(block.get("segments", []))
        if not all_segs:
            return

        # 1. Strip existing \n from segment texts (we will re-add the correct ones)
        for seg in all_segs:
            seg["text"] = seg["text"].replace("\n", "")
            seg["translation"] = seg["translation"].replace("\n", "")

        # 2. Build map of positions in the stripped original where \n should appear
        #    (position = index of the last non-\n char before the \n)
        nl_after: set[int] = set()
        char_idx = -1
        for ch in original_text:
            if ch == "\n":
                if char_idx >= 0:
                    nl_after.add(char_idx)
            else:
                char_idx += 1
        if not nl_after:
            return

        # 3. Concatenate clean segment texts
        concat = "".join(s["text"] for s in all_segs)
        orig_stripped = original_text.replace("\n", "")

        # 4. Align orig_stripped ↔ concat with two pointers and map nl positions
        mapped: set[int] = set()   # positions in *concat* after which \n belongs
        oi = ci = 0
        while oi < len(orig_stripped) and ci < len(concat):
            if orig_stripped[oi] == concat[ci]:
                if oi in nl_after:
                    mapped.add(ci)
                oi += 1; ci += 1
            elif orig_stripped[oi] in (" ", "\t", "\r"):
                if oi in nl_after:
                    mapped.add(max(ci - 1, 0))
                oi += 1
            elif concat[ci] in (" ", "\t", "\r"):
                ci += 1
            else:                             # true mismatch – advance both
                if oi in nl_after:
                    mapped.add(ci)
                oi += 1; ci += 1

        if not mapped:
            return

        # 5. Insert \n into segment texts at the mapped positions
        offset = 0
        for seg in all_segs:
            txt = seg["text"]
            parts: list[str] = []
            for i, ch in enumerate(txt):
                parts.append(ch)
                if (offset + i) in mapped:
                    parts.append("\n")
            seg["text"] = "".join(parts)
            offset += len(txt)

            # Mirror trailing \n into translation
            trail = len(seg["text"]) - len(seg["text"].rstrip("\n"))
            if trail > 0:
                seg["translation"] = seg["translation"].rstrip("\n") + "\n" * trail

        # 6. Rebuild content blocks (re-split paragraphs on \n\n)
        new_content: list[dict] = []
        cur: list[dict] = []
        for seg in all_segs:
            cur.append(seg)
            if seg["text"].rstrip(" ").endswith("\n\n"):
                new_content.append({"type": "paragraph", "segments": list(cur)})
                cur = []
        if cur:
            new_content.append({"type": "paragraph", "segments": cur})
        self.content = new_content

    # ── persist ──────────────────────────────────────────────────

    def save(self, part_index: int = 0, total_parts: int = 1) -> str:
        """Write the .xlan file. Returns filename. Metadata registration is handled by the caller."""
        translates_path = get_project_path(self.project_id, self.user_dir) / "translates"
        translates_path.mkdir(parents=True, exist_ok=True)

        if self.extend_base:
            from app.services.document_service import load_section_metadata
            meta = load_section_metadata(self.project_id, "translates", self.user_dir)
            entry = meta.get("files", {}).get(self.extend_base, {})
            existing_parts = entry.get("parts", [self.extend_base])

            base_slug = self.extend_base.replace(".xlan", "")
            base_slug = re.sub(r"_\d+$", "", base_slug)

            next_num = len(existing_parts) + 1
            filename = f"{base_slug}_{next_num}.xlan"
            while (translates_path / filename).exists():
                next_num += 1
                filename = f"{base_slug}_{next_num}.xlan"
        else:
            slug = _slugify(self.title)
            if part_index == 0:
                filename = f"{slug}.xlan"
                dest = translates_path / filename
                counter = 1
                while dest.exists():
                    filename = f"{slug}_{counter}.xlan"
                    dest = translates_path / filename
                    counter += 1
            else:
                filename = f"{slug}_{part_index + 1}.xlan"

        xlan_data = {
            "meta": {
                "title": self.title,
                "description": self.description,
                "text_language": self.text_language,
                "notes_language": self.notes_language,
                "created_at": datetime.utcnow().isoformat(),
            },
            "content": self.content,
        }
        if total_parts > 1:
            xlan_data["meta"]["part"] = part_index + 1
            xlan_data["meta"]["total_parts"] = total_parts

        save_xlan(self.project_id, filename, xlan_data, self.user_dir)
        self.saved_filenames.append(filename)
        self._emit(f"💾 Saved: {filename}")
        return filename


# ── Agent runner ─────────────────────────────────────────────────

def _build_model_string(provider: str, model: str) -> str:
    """Map provider + model to the litellm model string."""
    provider = provider.lower()
    if provider == "openai":
        return model  # "gpt-4o-mini", "gpt-4o", ...
    if "/" in model:
        return model  # already prefixed
    return f"{provider}/{model}"  # "anthropic/claude-3-haiku-...", etc.


def _load_system_prompt() -> str:
    candidates = [
        Path("agent.txt"),
        Path(__file__).parent.parent.parent / "agent.txt",
    ]
    for p in candidates:
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError("agent.txt not found")


def run_xlan_agent(
    session: XlanSession,
    raw_text: str,
    *,
    text_blocks: list[dict] | None = None,  # legacy compat, ignored
) -> str:
    """
    Run the tool-calling LLM loop to build the .xlan.
    If the text is >BATCH_CHARS, it is split into batches at paragraph
    boundaries, and each batch produces a separate .xlan part.

    Emits progress via session.on_progress.
    Returns the first (or only) saved filename.
    Metadata registration is NOT done here — see api_pipeline_agent.py.
    """
    batches = _split_batches(raw_text)
    total_parts = len(batches)

    if total_parts > 1:
        session._emit(f"📦 Long text ({len(raw_text)} chars) — split into {total_parts} parts")

    prev_context: str | None = None

    if session.extend_base:
        prev_context = _load_last_block_text(
            session.project_id, session.extend_base, session.user_dir
        )

    first_filename: str | None = None

    for batch_idx, batch_text in enumerate(batches):
        if total_parts > 1:
            session._emit(f"\n── Part {batch_idx + 1}/{total_parts} ──")

        session.content = []
        session.seg_counter = (
            0 if batch_idx == 0 else session.seg_counter
        )

        filename = _run_single_batch(
            session, batch_text, batch_idx, total_parts, prev_context
        )

        if batch_idx == 0:
            first_filename = filename

        if batch_idx < total_parts - 1:
            prev_context = _extract_tail_text(session.content)

    return first_filename or ""


def _split_batches(text: str) -> list[str]:
    """Split text into batches of ~BATCH_CHARS at paragraph boundaries."""
    if len(text) <= BATCH_CHARS:
        return [text]

    # Split into paragraphs (separated by 2+ newlines)
    paragraphs = re.split(r"(\n{2,})", text)
    # paragraphs now alternates between content and separators

    batches: list[str] = []
    current = ""

    for chunk in paragraphs:
        if len(current) + len(chunk) > BATCH_CHARS and current.strip():
            batches.append(current)
            current = chunk
        else:
            current += chunk

    if current.strip():
        batches.append(current)

    return batches if batches else [text]


def _extract_tail_text(content: list[dict], max_blocks: int = 2) -> str:
    """Get the concatenated text of the last few blocks (for context)."""
    tail = content[-max_blocks:] if len(content) >= max_blocks else content
    parts = []
    for block in tail:
        segs = block.get("segments", [])
        parts.append("".join(s.get("text", "") for s in segs))
    return "\n".join(parts)


def _load_last_block_text(project_id: str, filename: str, user_dir: str) -> str | None:
    """Load the last block's text from an existing xlan file for context."""
    from app.services.xlan_service import load_xlan
    from app.services.document_service import load_section_metadata

    # If the file has parts, load the last part
    meta = load_section_metadata(project_id, "translates", user_dir)
    entry = meta.get("files", {}).get(filename, {})
    parts = entry.get("parts", [filename])
    target = parts[-1] if parts else filename

    xlan = load_xlan(project_id, target, user_dir)
    if not xlan:
        return None
    content = xlan.get("content", [])
    if not content:
        return None
    return _extract_tail_text(content)


def _run_single_batch(
    session: XlanSession,
    batch_text: str,
    part_index: int,
    total_parts: int,
    prev_context: str | None,
) -> str:
    """Run the agent loop for a single batch of text and save the result."""
    system_prompt = _load_system_prompt()
    model = _build_model_string(settings.llm_provider, settings.llm_model)
    api_key = settings.llm_api_key or None

    # Build user message
    user_parts: list[str] = [
        f"I'll now give you a text in {session.text_language}. I need you to do the following:",
        "",
        "- Divide the text into minimal meaningful units within context: words, expressions, or short phrases. Never make a division of more than five words.",
        f"- Translate each unit into {session.notes_language}.",
        f"- For each translation, write an explanatory paragraph about the translation, pointing out all aspects relevant to a student of that language (gender, number, declension, verb tense, literal meaning and meaning in context, construction, why a particular preposition or conjunction is used, etc.).",
        f"- Return the result as a list of tuples, with each tuple containing 3 elements: the phrase/word in {session.text_language}, the phrase/word in {session.notes_language}, and the didactic explanation.",
        "- Call the `create_xlan` tool with the list of tuples.",
        "",
        'IMPORTANT: respect the line breaks of the original text: do not introduce new line breaks, and add "\\n" to the end of the first two elements of each tuple if the original text contains a line break.',
    ]

    if session.source_type == "ocr":
        user_parts.append(
            "\nIMPORTANT: This text was extracted via OCR from an image. "
            "Some individual words or characters might be wrong. If you clearly detect an OCR error, "
            "fix it in the first element and mention the correction in the didactic explanation.\n"
            "Since this is OCR text, the line breaks come from the physical layout of the page, NOT "
            "from the author's formatting. For regular prose, ignore those line breaks and let text flow naturally. "
            "However, DETECT DIALOGUE: if the text contains dialogue markers (—, –, -, «, \", etc.), "
            "each speaker's line MUST be its own separate group of tuples."
        )

    if prev_context:
        user_parts.append(
            f"\nCONTEXT — The text below is a continuation of a previous passage. "
            f"Here is the end of the previous passage for context only. "
            f"Do NOT include this context text — only process the TEXT below:\n"
            f"---\n{prev_context}\n---"
        )

    if total_parts > 1:
        user_parts.append(f"\nThis is part {part_index + 1} of {total_parts}.")

    user_parts.append(f"\nTEXT:\n\n{batch_text}")

    user_message = "\n".join(user_parts)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    session._emit(f"🤖 Agent started — model: {model}")

    max_iterations = 3
    for iteration in range(max_iterations):
        t0 = time.time()

        if _is_debug():
            total_msg_words = sum(len(m.get("content", "").split()) for m in messages)
            logger.info(
                f"[LLM] iteration={iteration} messages={len(messages)} "
                f"~words_in_context={total_msg_words}"
            )

        response = litellm.completion(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            api_key=api_key,
            temperature=1,
        )

        elapsed = time.time() - t0
        msg = response.choices[0].message

        usage = getattr(response, "usage", None)
        if usage:
            prompt_tok = getattr(usage, "prompt_tokens", 0)
            completion_tok = getattr(usage, "completion_tokens", 0)
            session._emit(f"⏱ {elapsed:.1f}s — {prompt_tok} in / {completion_tok} out")
        else:
            session._emit(f"⏱ {elapsed:.1f}s")

        assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if not msg.tool_calls:
            if session.content:
                session._emit("✅ Done")
                break
            if iteration < max_iterations - 1:
                session._emit("⚠ No tool call, retrying…")
                messages.append({
                    "role": "user",
                    "content": "You must call `create_xlan` with the list of tuples. Please try again.",
                })
                continue
            break

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            result = session.call_tool(name, args)
            messages.append({
                "role": "tool",
                "content": result,
                "tool_call_id": tc.id,
            })

        if session.content:
            session._emit("✅ Done")
            break

    # Post-process: ensure original line breaks are preserved (skip OCR — layout \n)
    if session.source_type != "ocr":
        session._restore_line_breaks(batch_text)

    return session.save(part_index=part_index, total_parts=total_parts)
