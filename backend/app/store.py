"""In-memory session store.

The demo runs a single chat session (the brief scope), but the shape here —
one Session object owning its event log, state machine and subscriber queues —
maps 1:1 to a per-session-id store if you need multiple sessions.

Key design decisions:

* The agent loop is a plain asyncio task owned by the session, NOT by any
  HTTP connection. If the client disconnects, the loop keeps running and
  events keep accumulating in the log; the client replays them on reconnect.
* SSE subscribers get a snapshot replay (everything after their cursor) and
  then live events through an asyncio.Queue. This gives at-least-once
  delivery with ordering guaranteed by the sequence numbers.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from .events import Event, EventLog

# Loop states (the state machine lives in README.md)
STATE_IDLE = "idle"
STATE_SEARCHING = "searching"
STATE_AWAITING_CONFIRMATION = "awaiting_confirmation"
STATE_GENERATING = "generating"
STATE_REPLYING = "replying"

CONFIRM_ACTIONS = ("approve", "edit", "reject")


class Session:
    def __init__(self) -> None:
        self.log = EventLog()
        self.state = STATE_IDLE
        self.loop_task: Optional[asyncio.Task] = None
        # Resolved with ("approve"|"edit", prompt) or ("reject", None)
        self.confirm_future: Optional[asyncio.Future] = None
        self.agent_loop: Optional[Any] = None  # the running AgentLoop, if any
        self.current_prompt: str = ""
        self.fail_image = False  # debug toggle: force image generation to fail
        self._subscribers: list[asyncio.Queue] = []

    # ---- event fan-out -------------------------------------------------

    def emit(self, type_: str, **data: Any) -> Event:
        ev = self.log.append(type_, **data)
        for q in list(self._subscribers):
            q.put_nowait(ev)
        return ev

    def subscribe(self, after_seq: int) -> tuple[list[Event], asyncio.Queue]:
        """Return (replay events, live queue) for a (re)connecting client."""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return self.log.after(after_seq), q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    # ---- state transitions ---------------------------------------------

    def set_state(self, state: str) -> None:
        self.state = state
        self.emit("status", state=state)

    # ---- snapshot for refresh recovery ----------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "last_seq": self.log.last_seq,
            "pending_confirm": {
                "prompt": self.current_prompt
            } if self.state == STATE_AWAITING_CONFIRMATION else None,
            "fail_image": self.fail_image,
        }


class SessionStore:
    """Single-session registry (see module docstring for the multi-session path)."""

    def __init__(self) -> None:
        self._session = Session()

    def get(self) -> Session:
        return self._session


store = SessionStore()
