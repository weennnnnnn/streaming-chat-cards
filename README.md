# streaming-chat-cards

A small full-stack demo of a **streaming chat UI with human-in-the-loop confirmation cards**.

The backend runs a multi-step loop (search → confirm → generate → reply) and pushes
structured events to the browser over SSE. The user can approve, edit, or reject a
proposed action before the loop continues. The event stream is resumable: every event
carries a monotonically increasing sequence number, and a reconnecting client can ask
for everything it missed — including after a full page refresh.

## Stack

- **Backend**: Python 3.11+ · FastAPI · SSE (Server-Sent Events) · in-memory event log
- **Frontend**: TypeScript · Vite · native `EventSource` (strict mode, zero UI framework)
- **No external services**: search results are mocked, the "image" is a deterministic
  SVG placeholder generated from the prompt, and the final reply is streamed token by
  token.

## Quick start

Requirements: Python 3.11+ and Node 18+.

```bash
# 1. backend deps
pip install -r backend/requirements.txt

# 2. frontend deps
cd frontend && npm install && cd ..

# 3. run both (backend :8000, frontend :5173 with /api proxy)
python run.py
```

Then open http://localhost:5173.

You can also run the two sides manually in two terminals:

```bash
uvicorn app.main:app --app-dir backend --port 8000
cd frontend && npm run dev
```

## Feature checklist

- Token-by-token streaming replies over SSE
- Confirmation cards: approve / edit-and-approve / reject
- Resumable event stream: reconnect with `?after_seq=N` (or `Last-Event-ID`) and
  replay missed events in order
- The loop keeps running server-side while the client is disconnected
- One active loop per session; concurrent messages are rejected with `409`
- Cancel a running loop at any point (search / confirmation / generation / reply)
- Refresh recovery: the UI is a pure fold over the event log, so reloading the page
  rebuilds the exact prior state — including a pending confirmation card
- Debug panel: simulate disconnect, force image-generation failure, fire concurrent
  sends (exactly one must land, the other gets `409`)
- Heartbeat comments keep idle SSE connections alive through proxies

## The three behavioral rules

1. **Message while a confirmation is pending** → rejected with `409 LOOP_ACTIVE`;
   the UI explains it instead of silently queueing.
2. **Image generation fails** → surfaced as an `error` event; **no automatic retry** —
   the user decides whether to try again.
3. **Refresh / reconnect** → `GET /api/state` snapshot + `GET /api/stream?after_seq=N`
   replay; a pending confirmation stays actionable because the loop is still waiting
   on the server.

## Documentation

- [`docs/DESIGN.md`](docs/DESIGN.md) — full architecture notes: event catalog, state
  machine diagram, loop/connection decoupling, and the reasoning behind each key
  decision.
- [`scripts/smoke_test.py`](scripts/smoke_test.py) — end-to-end test that drives the
  SSE stream and asserts the happy path, `409` on active loop, reject, mid-generate
  cancel, forced failure, and replay ordering:

  ```bash
  uvicorn app.main:app --app-dir backend --port 8000   # in one terminal
  python scripts/smoke_test.py                          # in another
  ```

## AI usage

Development was AI-assisted; the full usage log lives in [`ai-logs/`](ai-logs/).
