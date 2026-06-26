"""Session-aware conversation buffer for Clinical Chat.

Bug #6 in the uplift: Clinical Chat (8206) has no persistent conversation
memory across turns — each ``/chat`` request is effectively stateless. This
buffer fixes that with an in-process ``OrderedDict`` keyed by session id.
Swap in a Redis backend by setting ``CONVERSATION_BACKEND=redis`` env var.

Turns are capped at ``max_turns`` per session (default 50). Oldest turns
are evicted on append. Sessions are LRU-evicted when the global cap is hit.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional


@dataclass
class ConversationTurn:
    role: str            # "user" | "assistant" | "system"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


class ConversationBuffer:
    """LRU+capped-per-session conversation store.

    Parameters
    ----------
    max_turns:
        Maximum turns retained per session. Oldest evicted on append.
    max_sessions:
        Maximum concurrent sessions before LRU eviction.
    """

    def __init__(self, max_turns: int = 50, max_sessions: int = 1024) -> None:
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self._sessions: "OrderedDict[str, Deque[ConversationTurn]]" = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ mutations
    def append(self, session_id: str, turn: ConversationTurn) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions.move_to_end(session_id)
                self._sessions[session_id].append(turn)
            else:
                if len(self._sessions) >= self.max_sessions:
                    self._sessions.popitem(last=False)  # LRU evict
                self._sessions[session_id] = deque([turn], maxlen=self.max_turns)

    def append_exchange(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Convenience: append a user turn then an assistant turn atomically."""
        meta = metadata or {}
        self.append(session_id, ConversationTurn(role="user", content=user_message, metadata=meta))
        self.append(session_id, ConversationTurn(role="assistant", content=assistant_message, metadata=meta))

    # ------------------------------------------------------------------ readers
    def history(self, session_id: str, limit: Optional[int] = None) -> List[ConversationTurn]:
        with self._lock:
            turns = list(self._sessions.get(session_id, ()))
            if limit is not None:
                turns = turns[-limit:]
            return turns

    def has_session(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._sessions

    def session_ids(self) -> List[str]:
        with self._lock:
            return list(self._sessions.keys())

    # ------------------------------------------------------------------ lifecycle
    def clear(self, session_id: Optional[str] = None) -> None:
        with self._lock:
            if session_id is None:
                self._sessions.clear()
            else:
                self._sessions.pop(session_id, None)


# ---------------------------------------------------------------- module-wide singleton

_buffer_singleton: Optional[ConversationBuffer] = None
_singleton_lock = threading.Lock()


def get_conversation_buffer() -> ConversationBuffer:
    """Return the process-wide default buffer.

    Reads ``CONVERSATION_MAX_TURNS`` and ``CONVERSATION_MAX_SESSIONS`` env
    vars on first call. Redis backend is a TODO — activated via
    ``CONVERSATION_BACKEND=redis`` + ``REDIS_URL``.
    """
    global _buffer_singleton
    if _buffer_singleton is None:
        with _singleton_lock:
            if _buffer_singleton is None:
                max_turns = int(os.environ.get("CONVERSATION_MAX_TURNS", "50"))
                max_sessions = int(os.environ.get("CONVERSATION_MAX_SESSIONS", "1024"))
                _buffer_singleton = ConversationBuffer(max_turns=max_turns, max_sessions=max_sessions)
    return _buffer_singleton


__all__ = ["ConversationBuffer", "ConversationTurn", "get_conversation_buffer"]
