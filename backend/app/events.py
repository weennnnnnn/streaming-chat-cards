"""Event model for the streaming chat session.

Every event that happens in a session is appended to an ordered, in-memory
log with a monotonically increasing sequence number. Clients consume the log
over SSE and can reconnect with `after_seq` to replay anything they missed.

Event catalog
-------------
message    : a user message was accepted and a new loop started
status     : loop step changed (searching / awaiting_confirmation / generating / replying)
progress   : coarse progress inside a step (e.g. "searching: 2 results found")
confirm    : a confirmation card asking the user to approve / edit / reject
token      : one token of the final streamed reply
image      : the generated SVG placeholder image (inline SVG markup)
done       : the loop finished successfully
error      : the loop failed (e.g. image generation error), no auto-retry
cancelled  : the loop was cancelled by the user
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Event:
    seq: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type, "data": self.data, "ts": self.ts}

    def to_sse(self) -> str:
        """Serialize as a Server-Sent Event frame.

        `id:` carries the sequence number so browsers resend it as the
        Last-Event-ID header on automatic reconnect.
        """
        import json

        payload = json.dumps(self.to_dict(), ensure_ascii=False)
        return f"id: {self.seq}\nevent: {self.type}\ndata: {payload}\n\n"


class EventLog:
    """Append-only event log with monotonically increasing sequence numbers."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._next_seq = 1

    def append(self, type_: str, **data: Any) -> Event:
        ev = Event(seq=self._next_seq, type=type_, data=data)
        self._next_seq += 1
        self._events.append(ev)
        return ev

    def after(self, seq: int) -> list[Event]:
        """All events with sequence number greater than `seq`."""
        return [e for e in self._events if e.seq > seq]

    @property
    def last_seq(self) -> int:
        return self._next_seq - 1
