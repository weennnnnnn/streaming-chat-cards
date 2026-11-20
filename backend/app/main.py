"""FastAPI application: HTTP endpoints + SSE event stream.

Endpoint map
------------
POST /api/messages          start a new agent loop (409 if one is active)
POST /api/confirm           resolve the pending confirmation card (409 otherwise)
POST /api/cancel            cancel the running loop (409 if idle)
GET  /api/state             session snapshot for refresh recovery
GET  /api/stream            SSE; `?after_seq=N` (or Last-Event-ID header) replays
POST /api/debug/fail-image  toggle the forced image-failure debug flag
GET  /healthz               liveness probe
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .loop import AgentLoop
from .store import (
    CONFIRM_ACTIONS,
    STATE_AWAITING_CONFIRMATION,
    STATE_IDLE,
    store,
)

app = FastAPI(title="streaming-chat-cards backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

HEARTBEAT_SECONDS = 15.0


class MessageIn(BaseModel):
    content: str


class ConfirmIn(BaseModel):
    action: str
    prompt: Optional[str] = None


class FailImageIn(BaseModel):
    enabled: bool


# ---- SSE ---------------------------------------------------------------


async def sse_generator(session, after_seq: int):
    replay, q = session.subscribe(after_seq)
    try:
        for ev in replay:
            yield ev.to_sse()
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
                yield ev.to_sse()
            except asyncio.TimeoutError:
                # Comment frame keeps intermediaries from closing an idle
                # connection and lets the client detect a dead link.
                yield ": ping\n\n"
    finally:
        session.unsubscribe(q)


@app.get("/api/stream")
async def stream(request: Request, after_seq: int = 0, last_event_id: Optional[str] = Header(None)):
    # Browsers resend the last received `id:` as Last-Event-ID on auto-reconnect.
    if after_seq == 0 and last_event_id and last_event_id.isdigit():
        after_seq = int(last_event_id)
    session = store.get()
    return _sse_response(sse_generator(session, after_seq))


def _sse_response(generator):
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disable proxy buffering (nginx et al.) so events arrive instantly.
            "X-Accel-Buffering": "no",
        },
    )


# ---- loop control -------------------------------------------------------


@app.post("/api/messages")
async def post_message(body: MessageIn):
    session = store.get()
    if session.state != STATE_IDLE:
        # Rule 1: while a loop is running or a confirmation is pending, new
        # messages are rejected — the client must confirm or cancel first.
        raise HTTPException(
            status_code=409,
            detail={"code": "LOOP_ACTIVE", "state": session.state},
        )
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail={"code": "EMPTY_MESSAGE"})

    session.emit("message", role="user", text=content)
    loop = AgentLoop(session, content)
    session.agent_loop = loop
    session.confirm_future = asyncio.get_running_loop().create_future()
    loop.start()
    return {"ok": True, "state": session.state}


@app.post("/api/confirm")
async def confirm(body: ConfirmIn):
    session = store.get()
    if session.state != STATE_AWAITING_CONFIRMATION or session.confirm_future is None:
        raise HTTPException(status_code=409, detail={"code": "NO_PENDING_CONFIRM"})

    action = body.action
    if action not in CONFIRM_ACTIONS:
        raise HTTPException(status_code=422, detail={"code": "BAD_ACTION", "allowed": list(CONFIRM_ACTIONS)})

    if action == "approve":
        session.confirm_future.set_result(("approve", session.current_prompt))
    elif action == "edit":
        edited = (body.prompt or "").strip()
        if not edited:
            raise HTTPException(status_code=422, detail={"code": "EMPTY_PROMPT"})
        session.confirm_future.set_result(("approve", edited))
    else:  # reject
        session.confirm_future.set_result(("reject", None))
    return {"ok": True}


@app.post("/api/cancel")
async def cancel():
    session = store.get()
    if session.state == STATE_IDLE:
        raise HTTPException(status_code=409, detail={"code": "NOTHING_TO_CANCEL"})
    session.emit("message", role="user", text="(请求取消)")
    if session.confirm_future is not None and not session.confirm_future.done():
        session.confirm_future.cancel()
    cancelled = session.agent_loop.cancel() if session.agent_loop else False
    if not cancelled and session.confirm_future is not None:
        # Loop may already be gone; make sure we do not hang on the future.
        session.confirm_future.cancel()
    return {"ok": True}


# ---- state & debug -------------------------------------------------------


@app.get("/api/state")
async def state():
    return store.get().snapshot()


@app.post("/api/debug/fail-image")
async def fail_image(body: FailImageIn):
    store.get().fail_image = body.enabled
    return {"ok": True, "fail_image": body.enabled}


@app.get("/healthz")
async def healthz():
    return {"ok": True}
