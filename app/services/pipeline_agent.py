"""
pipeline_agent.py — LLM agent (via litellm) that builds .xlan file(s) from text.

Uses a single-call tool approach: the LLM calls `build_xlan` once with ALL blocks
(headings + paragraphs) in a single response.  For long texts (>3000 chars) the
text is split into batches at paragraph boundaries, and each batch produces a
separate .xlan part.  Provider-agnostic via litellm.
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
from app.services.xlan_service import save_xlan, register_xlan_in_metadata, _slugify
from app.services.project_service import get_project_path

BATCH_CHARS = 3000
logger = logging.getLogger(__name__)

def _is_debug() -> bool:
    return settings.debug and settings.app_env == "development"


# ── Tool schema — single build_xlan tool ─────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "build_xlan",
            "description": (
                "Build the complete .xlan content in a SINGLE call. "
                "Pass ALL blocks (headings and paragraphs) at once as an ordered array. "
                "Each block is either a heading or a paragraph with its segments. "
                "Process the ENTIRE text — do not call this tool more than once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "blocks": {
                        "type": "array",
                        "description": "Ordered list of ALL blocks (headings and paragraphs) that make up the text.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["heading", "paragraph"],
                                    "description": "Block type: 'heading' for titles/sections, 'paragraph' for text blocks.",
                                },
                                "level": {
                                    "type": "integer",
                                    "description": "Heading level (1-3). Only used when type='heading'. Default: 1.",
                                },
                                "segments": {
                                    "type": "array",
                                    "description": (
                                        "Segments of this block. Headings have exactly 1 segment. "
                                        "Paragraphs have 1+ segments of 1–5 words each (max 8). "
                                        "Segments must NOT overlap — they partition the text exactly."
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "text": {
                                                "type": "string",
                                                "description": "Segment text in the study language. Must be 1–5 words (max 8).",
                                            },
                                            "translation": {
                                                "type": "string",
                                                "description": "Natural translation of this segment in the notes language.",
                                            },
                                            "info": {
                                                "type": "string",
                                                "description": "Brief linguistic note (1–3 sentences) in the notes language.",
                                            },
                                        },
                                        "required": ["text", "translation", "info"],
                                    },
                                },
                            },
                            "required": ["type", "segments"],
                        },
                    },
                },
                "required": ["blocks"],
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
        self.on_progress: Optional[Callable[[str], None]] = None

    # ── progress helper ──────────────────────────────────────────

    def _emit(self, msg: str) -> None:
        if self.on_progress:
            self.on_progress(msg)

    # ── tool dispatch ────────────────────────────────────────────

    def call_tool(self, name: str, args: dict) -> str:
        if name == "build_xlan":
            return self._build_xlan(args.get("blocks", []))
        return f"ERROR: unknown tool '{name}'"

    # ── tool implementation ──────────────────────────────────────

    def _build_xlan(self, blocks: list[dict]) -> str:
        """Process ALL blocks at once. Returns validation result."""
        warnings: list[str] = []

        for block_idx, block in enumerate(blocks):
            btype = block.get("type", "paragraph")
            segments_raw = block.get("segments", [])

            if btype == "heading":
                level = int(block.get("level", 1))
                if not segments_raw:
                    warnings.append(f"Block {block_idx}: heading with no segments — skipped")
                    continue
                seg = segments_raw[0]
                self.seg_counter += 1
                self.content.append({
                    "type": "heading",
                    "level": level,
                    "segments": [{
                        "id": f"h{level}_s{self.seg_counter}",
                        "text": seg.get("text", ""),
                        "translation": seg.get("translation", ""),
                        "info": seg.get("info", ""),
                        "styles": [],
                    }],
                })
                self._emit(f'✓ Heading: "{seg.get("text", "")[:60]}"')

            elif btype == "paragraph":
                segs = []
                for seg in segments_raw:
                    text = seg.get("text", "")
                    translation = seg.get("translation", "")

                    # Skip segments with content but empty/non-alphanumeric translation
                    if text.strip() and not re.search(
                        r"[a-zA-Z\u00C0-\u024F\u0400-\u04FF\u0600-\u06FF"
                        r"\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\uAC00-\uD7AF0-9]",
                        translation,
                    ):
                        warnings.append(
                            f"Block {block_idx}: segment '{text[:40]}' has no valid translation — skipped"
                        )
                        self._emit(f"⚠ Skipped segment with empty translation: \"{text[:50]}\"")
                        continue

                    # Check word count
                    word_count = len(text.split())
                    if word_count > 8:
                        warnings.append(
                            f"Block {block_idx}: segment '{text[:40]}' has {word_count} words (max 8)"
                        )
                        self._emit(f"❌ Segment too long ({word_count} words): \"{text[:50]}\"")
                    elif word_count > 5:
                        warnings.append(
                            f"Block {block_idx}: segment '{text[:40]}' has {word_count} words (ideal ≤5)"
                        )

                    self.seg_counter += 1
                    segs.append({
                        "id": f"seg_{self.seg_counter}",
                        "text": text,
                        "translation": translation,
                        "info": seg.get("info", ""),
                        "styles": [],
                    })

                if not segs:
                    warnings.append(f"Block {block_idx}: no valid segments — skipped")
                    continue



                self.content.append({"type": "paragraph", "segments": segs})
                self._emit(f"✓ Paragraph added ({len(segs)} segments)")
            else:
                warnings.append(f"Block {block_idx}: unknown type '{btype}' — skipped")

        # Inline validation
        errors = []
        for i, b in enumerate(self.content):
            if not b.get("segments"):
                errors.append(f"Block {i}: no segments")
            for j, seg in enumerate(b.get("segments", [])):
                if not seg.get("text"):
                    errors.append(f"Block {i}, seg {j}: empty text")

        result_parts = [
            f"OK: {len(self.content)} blocks, {self.seg_counter} total segments"
        ]
        if warnings:
            result_parts.append("WARNINGS:\n" + "\n".join(warnings))
        if errors:
            result_parts.append("ERRORS:\n" + "\n".join(errors))

        return "\n".join(result_parts)

    # ── persist ──────────────────────────────────────────────────

    def save(self, part_index: int = 0, total_parts: int = 1) -> str:
        """Write the .xlan file and register it in section metadata. Returns filename."""
        translates_path = get_project_path(self.project_id, self.user_dir) / "translates"
        translates_path.mkdir(parents=True, exist_ok=True)

        if self.extend_base:
            # Extending: find the next part number from the metadata
            from app.services.document_service import load_section_metadata
            meta = load_section_metadata(self.project_id, "translates", self.user_dir)
            entry = meta.get("files", {}).get(self.extend_base, {})
            existing_parts = entry.get("parts", [self.extend_base])

            # Derive the slug from the base filename, not from title
            base_slug = self.extend_base.replace(".xlan", "")
            # Remove trailing _N to get the root slug
            base_slug = re.sub(r"_\d+$", "", base_slug)

            next_num = len(existing_parts) + 1
            filename = f"{base_slug}_{next_num}.xlan"
            # Safety: skip if file already exists
            while (translates_path / filename).exists():
                next_num += 1
                filename = f"{base_slug}_{next_num}.xlan"
        else:
            slug = _slugify(self.title)
            # Determine filename — parts use _2, _3, etc. (first part has no suffix)
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
        # Mark multi-part files
        if total_parts > 1:
            xlan_data["meta"]["part"] = part_index + 1
            xlan_data["meta"]["total_parts"] = total_parts

        save_xlan(self.project_id, filename, xlan_data, self.user_dir)

        # Register in metadata only for new files (not extensions, not multi-part beyond first)
        if part_index == 0 and not self.extend_base:
            register_xlan_in_metadata(
                self.project_id, filename, self.title, self.description, user_dir=self.user_dir
            )

        # Update the metadata entry with parts list if multi-part or extending
        if total_parts > 1 or self.extend_base:
            base_slug = re.sub(r"_\d+$", "", filename.replace(".xlan", ""))
            self._update_parts_in_metadata(base_slug, filename, part_index)

        self._emit(f"💾 Saved as: {filename}")
        return filename

    def _update_parts_in_metadata(self, slug: str, new_filename: str, part_index: int) -> None:
        """Maintain the 'parts' list in the metadata entry for the base file."""
        from app.services.document_service import load_section_metadata, save_section_metadata
        meta = load_section_metadata(self.project_id, "translates", self.user_dir)
        base_filename = self.extend_base or f"{slug}.xlan"

        entry = meta["files"].get(base_filename)
        if not entry:
            return

        parts = entry.get("parts", [base_filename])
        if new_filename not in parts:
            parts.append(new_filename)
        entry["parts"] = parts
        meta["files"][base_filename] = entry
        save_section_metadata(self.project_id, "translates", meta, self.user_dir)


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
    """
    batches = _split_batches(raw_text)
    total_parts = len(batches)

    if total_parts > 1:
        session._emit(f"📦 Texto largo ({len(raw_text)} chars) — dividido en {total_parts} partes")

    prev_context: str | None = None

    # If extending an existing file, load last block as context
    if session.extend_base:
        prev_context = _load_last_block_text(
            session.project_id, session.extend_base, session.user_dir
        )

    first_filename: str | None = None

    for batch_idx, batch_text in enumerate(batches):
        if total_parts > 1:
            session._emit(f"\n── Parte {batch_idx + 1}/{total_parts} ──")

        # Reset content for each part
        session.content = []
        session.seg_counter = (
            0 if batch_idx == 0 else session.seg_counter  # continue counter across parts
        )

        filename = _run_single_batch(
            session, batch_text, batch_idx, total_parts, prev_context
        )

        if batch_idx == 0:
            first_filename = filename

        # The last few blocks of this batch become the context for the next
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
        f"Build the .xlan file for the text below.",
        f"- Text language (study language, goes in all `text` fields): {session.text_language}",
        f"- Notes language (native language, goes in `translation` and `info` fields): {session.notes_language}",
    ]

    if session.source_type == "ocr":
        user_parts.append(
            "\nIMPORTANT: This text was extracted via OCR from an image. "
            "Some individual words or characters might be wrong. If you clearly detect an OCR error, "
            "fix it in the `text` field and mention the correction in the `info` field.\n"
            "Since this is OCR text, the line breaks come from the physical layout of the page, NOT "
            "from the author's formatting. For regular prose, ignore those line breaks and let text flow naturally. "
            "However, DETECT DIALOGUE: if the text contains dialogue markers (—, –, -, «, \", etc.), "
            "each speaker's line MUST be its own separate paragraph block. Do not merge different speakers' "
            "lines into a single paragraph."
        )

    if prev_context:
        user_parts.append(
            f"\nCONTEXT — The text below is a continuation of a previous passage. "
            f"Here is the end of the previous passage for context only. "
            f"Do NOT include this context text in the new .xlan — only process the TEXT below:\n"
            f"---\n{prev_context}\n---"
        )

    if total_parts > 1:
        user_parts.append(f"\nThis is part {part_index + 1} of {total_parts}.")

    user_parts.append(
        f"\nTEXT TO PROCESS:\n\n{batch_text}\n\n"
        f"Read this text, identify headings and paragraphs, and call `build_xlan` "
        f"ONCE with ALL blocks. Do not make multiple tool calls — put everything "
        f"in a single `build_xlan` call."
    )

    user_message = "\n".join(user_parts)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    session._emit(f"🤖 Agent started — model: {model}")

    # Expect 1 call (build_xlan), allow up to 3 iterations for error recovery
    max_iterations = 3
    result = ""
    for iteration in range(max_iterations):
        t0 = time.time()

        if _is_debug():
            sys_tokens = len(system_prompt.split())
            user_tokens = len(user_message.split())
            total_msg_words = sum(len(m.get("content", "").split()) for m in messages)
            logger.info(
                f"[LLM] iteration={iteration} messages={len(messages)} "
                f"~words_in_context={total_msg_words}"
            )
            session._emit(f"🔧 LLM call #{iteration+1} — {len(messages)} messages in context")

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

        # Extract token usage
        usage = getattr(response, "usage", None)
        if usage:
            prompt_tok = getattr(usage, "prompt_tokens", 0)
            completion_tok = getattr(usage, "completion_tokens", 0)
            total_tok = getattr(usage, "total_tokens", 0)
            usage_str = f"tokens: {prompt_tok} in / {completion_tok} out / {total_tok} total"
            session._emit(f"⏱ LLM responded in {elapsed:.1f}s — {usage_str}")
            if _is_debug():
                logger.info(f"[LLM] {elapsed:.1f}s — {usage_str}")
        else:
            session._emit(f"⏱ LLM responded in {elapsed:.1f}s")
            if _is_debug():
                logger.info(f"[LLM] {elapsed:.1f}s — no usage info")

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
            # No tool calls — if we already have content, we're done
            if session.content:
                session._emit("✅ Agent finished processing")
                break
            # Otherwise the LLM didn't call the tool — retry once
            if iteration < max_iterations - 1:
                session._emit("⚠ No tool call received, retrying…")
                messages.append({
                    "role": "user",
                    "content": "You must call `build_xlan` with all the blocks. Please try again.",
                })
                continue
            break

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            if _is_debug():
                args_len = len(tc.function.arguments)
                n_blocks = len(args.get("blocks", [])) if name == "build_xlan" else 0
                logger.info(
                    f"[LLM] tool_call: {name} — "
                    f"args_size={args_len} chars, blocks={n_blocks}"
                )

            result = session.call_tool(name, args)
            messages.append(
                {
                    "role": "tool",
                    "content": result,
                    "tool_call_id": tc.id,
                }
            )
            session._emit(f"📊 {len(session.content)} blocks, {session.seg_counter} segments")

        # After processing build_xlan, check if there were errors to retry
        if "ERRORS:" not in result:
            session._emit("✅ Agent finished processing")
            break
        elif iteration < max_iterations - 1:
            session._emit("⚠ Errors found, asking LLM to fix…")
            # Reset content for next attempt
            session.content = []
            session.seg_counter = 0

    return session.save(part_index=part_index, total_parts=total_parts)
