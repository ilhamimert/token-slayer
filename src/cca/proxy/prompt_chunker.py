"""Static/dynamic prompt separation for smarter context caching.

LLM providers (Anthropic, OpenAI) cache prompt prefixes when the same
tokens appear in repeated requests. The key insight:

  [STATIC PART]   = system prompt, documents, rules — changes rarely
  [DYNAMIC PART]  = user question, conversation tail — changes every turn

By splitting these, the proxy ensures the static prefix is always
cache-eligible and avoids unnecessary re-billing on each call.

Usage
-----
    chunker = PromptChunker()
    result = chunker.split("You are a helpful assistant.\n\nHuman: What is Python?")
    result.static_part   # "You are a helpful assistant."
    result.dynamic_part  # "Human: What is Python?"
    result.cache_key     # SHA-256 of static_part (for cache lookup)
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


# Patterns that indicate the boundary between static and dynamic content
_HUMAN_TURN_RE = re.compile(
    r"(?:^|\n)(?:Human|User|Q|Question)\s*:\s*",
    re.IGNORECASE,
)
_BOUNDARY_MARKERS = (
    "\nHuman:", "\nUser:", "\n[INST]", "\nQ:", "\nQuestion:",
    "<|im_start|>user", "<human>",
)


@dataclass(frozen=True)
class ChunkedPrompt:
    static_part: str    # System prompt / document context
    dynamic_part: str   # User question / conversation tail
    cache_key: str      # SHA-256 of static_part for cache keying
    static_tokens: int  # Approximate word count of static part
    dynamic_tokens: int # Approximate word count of dynamic part

    @property
    def total_tokens(self) -> int:
        return self.static_tokens + self.dynamic_tokens

    @property
    def cacheable_fraction(self) -> float:
        """Fraction of tokens that can be served from prefix cache."""
        if not self.total_tokens:
            return 0.0
        return self.static_tokens / self.total_tokens


def _approx_tokens(text: str) -> int:
    """Word-count approximation of token count (±20% of real tokenizer)."""
    return max(1, len(text.split()))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class PromptChunker:
    """Split a prompt into static (cacheable) and dynamic (volatile) parts.

    Parameters
    ----------
    min_static_words:
        Only treat content as "static" if it's at least this many words.
        Prevents single-word system prompts from distorting the split.
    """

    def __init__(self, min_static_words: int = 10) -> None:
        self._min_static_words = min_static_words

    def split(self, prompt: str) -> ChunkedPrompt:
        """Split *prompt* into static and dynamic parts."""
        split_index = self._find_boundary(prompt)

        if split_index is None or split_index <= 0:
            return ChunkedPrompt(
                static_part="",
                dynamic_part=prompt,
                cache_key=_sha256(""),
                static_tokens=0,
                dynamic_tokens=_approx_tokens(prompt),
            )

        static = prompt[:split_index].rstrip()
        dynamic = prompt[split_index:].lstrip()

        # Discard static part if it's too short to be worth caching
        if _approx_tokens(static) < self._min_static_words:
            return ChunkedPrompt(
                static_part="",
                dynamic_part=prompt,
                cache_key=_sha256(""),
                static_tokens=0,
                dynamic_tokens=_approx_tokens(prompt),
            )

        return ChunkedPrompt(
            static_part=static,
            dynamic_part=dynamic,
            cache_key=_sha256(static),
            static_tokens=_approx_tokens(static),
            dynamic_tokens=_approx_tokens(dynamic),
        )

    def split_messages(self, messages: list[dict]) -> ChunkedPrompt:
        """Split an OpenAI-style messages list into static/dynamic parts.

        System messages and all-but-last assistant/user messages → static.
        The final user message → dynamic.
        """
        if not messages:
            return self.split("")

        static_msgs: list[dict] = []
        dynamic_msg: dict = messages[-1]

        for msg in messages[:-1]:
            static_msgs.append(msg)

        static_text = "\n".join(
            f"{m.get('role','?')}: {m.get('content','')}" for m in static_msgs
        )
        dynamic_text = f"{dynamic_msg.get('role','user')}: {dynamic_msg.get('content','')}"

        static_tok = _approx_tokens(static_text)
        if static_tok < self._min_static_words:
            full = static_text + "\n" + dynamic_text
            return ChunkedPrompt(
                static_part="",
                dynamic_part=full,
                cache_key=_sha256(""),
                static_tokens=0,
                dynamic_tokens=_approx_tokens(full),
            )

        return ChunkedPrompt(
            static_part=static_text,
            dynamic_part=dynamic_text,
            cache_key=_sha256(static_text),
            static_tokens=static_tok,
            dynamic_tokens=_approx_tokens(dynamic_text),
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _find_boundary(self, prompt: str) -> int | None:
        """Return the character index where the dynamic part begins, or None."""
        # 1. Look for explicit turn markers
        best_pos: int | None = None
        for marker in _BOUNDARY_MARKERS:
            pos = prompt.find(marker)
            if pos > 0:
                if best_pos is None or pos > best_pos:
                    best_pos = pos

        if best_pos is not None:
            return best_pos

        # 2. Use regex for variations like "Q:", "Question :", etc.
        last_match = None
        for m in _HUMAN_TURN_RE.finditer(prompt):
            last_match = m
        if last_match and last_match.start() > 0:
            return last_match.start()

        # 3. If there are 2+ paragraphs, split before the last one
        paragraphs = re.split(r"\n{2,}", prompt)
        if len(paragraphs) >= 2:
            last_para = paragraphs[-1]
            split_at = len(prompt) - len(last_para)
            return split_at if split_at > 0 else None

        return None
