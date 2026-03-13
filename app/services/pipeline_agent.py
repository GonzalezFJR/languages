"""
pipeline_agent.py — LLM agent (via litellm) that builds a .xlan file from text blocks.

Uses a tool-calling loop: the LLM calls add_heading / add_paragraph for each block,
then validate_xlan when done. Provider-agnostic via litellm.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import litellm

from app.config import settings
from app.services.xlan_service import save_xlan, register_xlan_in_metadata, _slugify
from app.services.project_service import get_project_path


# ── Tool schema (OpenAI function-calling format, supported by all litellm providers) ──

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "add_heading",
            "description": (
                "Add a heading block to the .xlan file. "
                "Use for chapter titles, section titles, and sub-section headers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The heading text exactly as it appears in the source (study language).",
                    },
                    "translation": {
                        "type": "string",
                        "description": "Translation of the heading in the notes language.",
                    },
                    "info": {
                        "type": "string",
                        "description": (
                            "Brief linguistic note about the heading "
                            "(vocabulary, structure) in the notes language."
                        ),
                    },
                    "level": {
                        "type": "integer",
                        "description": "Heading level: 1 = main title, 2 = section, 3 = subsection.",
                        "default": 1,
                    },
                },
                "required": ["text", "translation", "info"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_paragraph",
            "description": (
                "Add a paragraph block with segmented text. "
                "Split the paragraph into meaningful linguistic units: phrases, clauses, or short word groups. "
                "Each segment is one vocabulary/grammar learning point."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "segments": {
                        "type": "array",
                        "description": "Ordered list of segments that make up the paragraph.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "Segment text in the study language (preserve exact wording and punctuation).",
                                },
                                "translation": {
                                    "type": "string",
                                    "description": "Natural translation of this segment in the notes language.",
                                },
                                "info": {
                                    "type": "string",
                                    "description": (
                                        "Linguistic explanation in the notes language: "
                                        "grammar, vocabulary, idiom meaning, etymology, usage tips. "
                                        "Be concise (1–3 sentences)."
                                    ),
                                },
                            },
                            "required": ["text", "translation", "info"],
                        },
                    },
                },
                "required": ["segments"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_xlan_state",
            "description": (
                "Returns a summary of the current .xlan being built: "
                "block count, segment count, and the last few blocks added."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_xlan",
            "description": (
                "Validate the complete .xlan structure. "
                "Call this after all blocks have been processed to confirm everything is correct."
            ),
            "parameters": {"type": "object", "properties": {}},
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
    ):
        self.project_id = project_id
        self.title = title
        self.description = description
        self.text_language = text_language
        self.notes_language = notes_language
        self.user_dir = user_dir
        self.content: list[dict] = []
        self.seg_counter = 0
        self.on_progress: Optional[Callable[[str], None]] = None

    # ── progress helper ──────────────────────────────────────────

    def _emit(self, msg: str) -> None:
        if self.on_progress:
            self.on_progress(msg)

    # ── tool dispatch ────────────────────────────────────────────

    def call_tool(self, name: str, args: dict) -> str:
        if name == "add_heading":
            return self._add_heading(**args)
        if name == "add_paragraph":
            return self._add_paragraph(**args)
        if name == "get_xlan_state":
            return self._get_state()
        if name == "validate_xlan":
            return self._validate()
        return f"ERROR: unknown tool '{name}'"

    # ── tool implementations ─────────────────────────────────────

    def _add_heading(
        self,
        text: str,
        translation: str,
        info: str,
        level: int = 1,
    ) -> str:
        self.seg_counter += 1
        block = {
            "type": "heading",
            "level": int(level),
            "segments": [
                {
                    "id": f"h{level}_s{self.seg_counter}",
                    "text": text,
                    "translation": translation,
                    "info": info,
                    "styles": [],
                }
            ],
        }
        self.content.append(block)
        self._emit(f'✓ Heading: "{text[:60]}"')
        return f"OK: heading block added at index {len(self.content) - 1}"

    def _add_paragraph(self, segments: list[dict]) -> str:
        segs = []
        for seg in segments:
            self.seg_counter += 1
            segs.append(
                {
                    "id": f"seg_{self.seg_counter}",
                    "text": seg.get("text", ""),
                    "translation": seg.get("translation", ""),
                    "info": seg.get("info", ""),
                    "styles": [],
                }
            )
        block = {"type": "paragraph", "segments": segs}
        self.content.append(block)
        self._emit(f"✓ Paragraph added ({len(segs)} segments)")
        return (
            f"OK: paragraph block added at index {len(self.content) - 1} "
            f"with {len(segs)} segments"
        )

    def _get_state(self) -> str:
        last = [
            {
                "type": b["type"],
                "preview": b["segments"][0]["text"][:60] if b.get("segments") else "",
            }
            for b in self.content[-3:]
        ]
        return json.dumps(
            {
                "blocks": len(self.content),
                "segments": self.seg_counter,
                "last_blocks": last,
            },
            ensure_ascii=False,
        )

    def _validate(self) -> str:
        errors = []
        for i, b in enumerate(self.content):
            if b.get("type") not in ("heading", "paragraph"):
                errors.append(f"Block {i}: invalid type '{b.get('type')}'")
            if not b.get("segments"):
                errors.append(f"Block {i}: no segments")
            for j, seg in enumerate(b.get("segments", [])):
                if not seg.get("text"):
                    errors.append(f"Block {i}, seg {j}: empty text")
        if errors:
            return "ERRORS:\n" + "\n".join(errors)
        return f"VALID: {len(self.content)} blocks, {self.seg_counter} total segments"

    # ── persist ──────────────────────────────────────────────────

    def save(self) -> str:
        """Write the .xlan file and register it in section metadata. Returns filename."""
        slug = _slugify(self.title)
        filename = f"{slug}.xlan"
        translates_path = get_project_path(self.project_id, self.user_dir) / "translates"
        translates_path.mkdir(parents=True, exist_ok=True)

        dest = translates_path / filename
        counter = 1
        while dest.exists():
            filename = f"{slug}_{counter}.xlan"
            dest = translates_path / filename
            counter += 1

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
        save_xlan(self.project_id, filename, xlan_data, self.user_dir)
        register_xlan_in_metadata(
            self.project_id, filename, self.title, self.description, self.user_dir
        )
        self._emit(f"💾 Saved as: {filename}")
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


def run_xlan_agent(session: XlanSession, text_blocks: list[dict]) -> str:
    """
    Run the tool-calling LLM loop to build the .xlan.
    Emits progress via session.on_progress.
    Returns the saved filename.
    """
    system_prompt = _load_system_prompt()
    model = _build_model_string(settings.llm_provider, settings.llm_model)
    api_key = settings.llm_api_key or None

    # Format blocks for the user message
    blocks_text = "\n\n".join(
        f"[HEADING L{b.get('level', 1)}] {b['text']}"
        if b.get("type") == "heading"
        else f"[PARAGRAPH] {b['text']}"
        for b in text_blocks
    )

    user_message = (
        f"Build the .xlan file for the text below.\n"
        f"- Text language (study language, goes in all `text` fields): {session.text_language}\n"
        f"- Notes language (native language, goes in `translation` and `info` fields): {session.notes_language}\n\n"
        f"TEXT BLOCKS ({len(text_blocks)} total):\n\n{blocks_text}\n\n"
        f"Process every block in order. Call `add_heading` for headings and "
        f"`add_paragraph` for paragraphs. When all blocks are done, call `validate_xlan`."
    )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    session._emit(f"🤖 Agent started — model: {model}")

    max_iterations = 300  # safety cap (each block ~2 iterations: call + result)
    for _ in range(max_iterations):
        response = litellm.completion(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            api_key=api_key,
            temperature=0.1,
        )

        msg = response.choices[0].message

        # Serialize assistant turn into messages history
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

        # No tool calls → agent is done
        if not msg.tool_calls:
            session._emit("✅ Agent finished processing all blocks")
            break

        # Execute each tool call and feed results back
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            result = session.call_tool(name, args)
            messages.append(
                {
                    "role": "tool",
                    "content": result,
                    "tool_call_id": tc.id,
                }
            )

    return session.save()
