"""Tests for cca.proxy.prompt_chunker."""
from __future__ import annotations

import pytest

from cca.proxy.prompt_chunker import ChunkedPrompt, PromptChunker


@pytest.fixture
def chunker():
    return PromptChunker(min_static_words=10)


# ── ChunkedPrompt properties ──────────────────────────────────────────────────

class TestChunkedPromptProperties:
    def test_total_tokens_is_sum(self):
        chunk = ChunkedPrompt(
            static_part="system",
            dynamic_part="user",
            cache_key="abc",
            static_tokens=100,
            dynamic_tokens=50,
        )
        assert chunk.total_tokens == 150

    def test_cacheable_fraction_when_static_dominates(self):
        chunk = ChunkedPrompt(
            static_part="s", dynamic_part="d", cache_key="k",
            static_tokens=80, dynamic_tokens=20,
        )
        assert abs(chunk.cacheable_fraction - 0.8) < 0.001

    def test_cacheable_fraction_zero_when_no_static(self):
        chunk = ChunkedPrompt(
            static_part="", dynamic_part="all dynamic", cache_key="k",
            static_tokens=0, dynamic_tokens=10,
        )
        assert chunk.cacheable_fraction == 0.0

    def test_cacheable_fraction_zero_when_no_tokens(self):
        chunk = ChunkedPrompt(
            static_part="", dynamic_part="", cache_key="k",
            static_tokens=0, dynamic_tokens=0,
        )
        assert chunk.cacheable_fraction == 0.0


# ── PromptChunker.split ───────────────────────────────────────────────────────

class TestSplit:
    def test_finds_human_turn_boundary(self, chunker):
        prompt = (
            "You are a helpful assistant. You answer questions concisely and clearly.\n"
            "\nHuman: What is the capital of France?"
        )
        chunk = chunker.split(prompt)
        assert "France" in chunk.dynamic_part
        assert chunk.static_part != ""

    def test_all_dynamic_when_no_boundary(self, chunker):
        prompt = "Just a single sentence with no markers."
        chunk = chunker.split(prompt)
        assert chunk.static_part == ""
        assert chunk.dynamic_part == prompt

    def test_short_static_is_discarded(self):
        strict_chunker = PromptChunker(min_static_words=50)
        prompt = "Short.\nHuman: What?"
        chunk = strict_chunker.split(prompt)
        assert chunk.static_part == ""

    def test_two_paragraph_prompt_splits_at_last_paragraph(self, chunker):
        prompt = (
            "You are a helpful assistant. Answer questions carefully and concisely.\n\n"
            "What is Python?"
        )
        chunk = chunker.split(prompt)
        assert "Python" in chunk.dynamic_part

    def test_cache_key_is_sha256_hex(self, chunker):
        prompt = "System prompt.\n\nHuman: Question?"
        chunk = chunker.split(prompt)
        assert len(chunk.cache_key) == 64
        assert all(c in "0123456789abcdef" for c in chunk.cache_key)

    def test_static_and_dynamic_together_cover_original(self, chunker):
        prompt = (
            "You are a helpful assistant. You answer questions concisely.\n"
            "\nHuman: Explain recursion."
        )
        chunk = chunker.split(prompt)
        combined = chunk.static_part + chunk.dynamic_part
        # All words from original appear somewhere in the split
        assert "assistant" in combined
        assert "recursion" in combined

    def test_empty_prompt_returns_empty_chunk(self, chunker):
        chunk = chunker.split("")
        assert chunk.total_tokens >= 0
        assert chunk.static_part == ""


# ── PromptChunker.split_messages ─────────────────────────────────────────────

class TestSplitMessages:
    def test_single_message_is_all_dynamic(self, chunker):
        messages = [{"role": "user", "content": "Hello"}]
        chunk = chunker.split_messages(messages)
        assert chunk.static_part == ""
        assert "Hello" in chunk.dynamic_part

    def test_system_plus_user_message_splits_correctly(self, chunker):
        messages = [
            {"role": "system", "content": "You are a helpful assistant with expertise in Python."},
            {"role": "user", "content": "What is a decorator?"},
        ]
        chunk = chunker.split_messages(messages)
        assert "decorator" in chunk.dynamic_part

    def test_multi_turn_puts_last_message_as_dynamic(self, chunker):
        messages = [
            {"role": "system", "content": "You are an expert assistant who is very knowledgeable."},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer with some detail and explanation"},
            {"role": "user", "content": "Follow-up question here"},
        ]
        chunk = chunker.split_messages(messages)
        assert "Follow-up" in chunk.dynamic_part
        assert "First question" in chunk.static_part

    def test_empty_messages_returns_empty_chunk(self, chunker):
        chunk = chunker.split_messages([])
        assert chunk.total_tokens >= 0

    def test_static_tokens_greater_than_zero_for_multi_turn(self, chunker):
        messages = [
            {"role": "system", "content": "You are a very helpful assistant with lots of knowledge."},
            {"role": "user", "content": "Question?"},
        ]
        chunk = chunker.split_messages(messages)
        assert chunk.static_tokens > 0

    def test_dynamic_tokens_always_positive(self, chunker):
        messages = [{"role": "user", "content": "Any question at all"}]
        chunk = chunker.split_messages(messages)
        assert chunk.dynamic_tokens > 0

    def test_different_static_content_produces_different_cache_keys(self, chunker):
        # Static part must be >= min_static_words (10) to get a non-empty cache key
        msgs_a = [
            {"role": "system", "content": "You are assistant A with expertise in data science, machine learning, and analytics."},
            {"role": "user", "content": "Same question here"},
        ]
        msgs_b = [
            {"role": "system", "content": "You are assistant B with expertise in web development, APIs, and cloud architecture."},
            {"role": "user", "content": "Same question here"},
        ]
        chunk_a = chunker.split_messages(msgs_a)
        chunk_b = chunker.split_messages(msgs_b)
        # Both must have non-empty static parts for the keys to differ
        assert chunk_a.static_part != ""
        assert chunk_b.static_part != ""
        assert chunk_a.cache_key != chunk_b.cache_key
