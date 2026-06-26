"""Unit tests for ConversationBuffer."""

from __future__ import annotations

import pytest

from shared.integration.conversation_buffer import ConversationBuffer, ConversationTurn


def test_empty_history_returns_empty_list(conversation_buffer):
    assert conversation_buffer.history("session-1") == []


def test_append_stores_turn(conversation_buffer):
    t = ConversationTurn(role="user", content="hi")
    conversation_buffer.append("s", t)
    assert [x.content for x in conversation_buffer.history("s")] == ["hi"]


def test_append_exchange_stores_two_turns(conversation_buffer):
    conversation_buffer.append_exchange("s", "hello", "hi there")
    roles = [t.role for t in conversation_buffer.history("s")]
    assert roles == ["user", "assistant"]


def test_max_turns_evicts_oldest(conversation_buffer):
    # fixture has max_turns=10
    for i in range(15):
        conversation_buffer.append("s", ConversationTurn(role="user", content=f"msg-{i}"))
    history = conversation_buffer.history("s")
    assert len(history) == 10
    assert history[0].content == "msg-5"
    assert history[-1].content == "msg-14"


def test_max_sessions_evicts_lru(conversation_buffer):
    # fixture has max_sessions=4
    for i in range(6):
        conversation_buffer.append(f"sess-{i}", ConversationTurn(role="user", content="x"))
    ids = conversation_buffer.session_ids()
    assert len(ids) == 4
    # First two should have been evicted
    assert "sess-0" not in ids
    assert "sess-1" not in ids


def test_append_moves_session_to_most_recent(conversation_buffer):
    for i in range(4):
        conversation_buffer.append(f"s{i}", ConversationTurn(role="user", content="x"))
    # Touch s0 — it should now be most recent
    conversation_buffer.append("s0", ConversationTurn(role="user", content="y"))
    # Now add 2 more sessions — s1 should be evicted, not s0
    conversation_buffer.append("s4", ConversationTurn(role="user", content="x"))
    conversation_buffer.append("s5", ConversationTurn(role="user", content="x"))
    ids = conversation_buffer.session_ids()
    assert "s0" in ids
    assert "s1" not in ids


def test_history_limit(conversation_buffer):
    for i in range(5):
        conversation_buffer.append("s", ConversationTurn(role="user", content=f"m{i}"))
    recent = conversation_buffer.history("s", limit=2)
    assert [t.content for t in recent] == ["m3", "m4"]


def test_clear_specific_session(conversation_buffer):
    conversation_buffer.append("a", ConversationTurn(role="user", content="x"))
    conversation_buffer.append("b", ConversationTurn(role="user", content="y"))
    conversation_buffer.clear("a")
    assert not conversation_buffer.has_session("a")
    assert conversation_buffer.has_session("b")


def test_clear_all(conversation_buffer):
    conversation_buffer.append("a", ConversationTurn(role="user", content="x"))
    conversation_buffer.append("b", ConversationTurn(role="user", content="y"))
    conversation_buffer.clear()
    assert conversation_buffer.session_ids() == []
