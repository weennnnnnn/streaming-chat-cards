# streaming-chat-cards

A small full-stack demo of a **streaming chat UI with human-in-the-loop confirmation cards**.

The backend runs a multi-step loop (search → confirm → generate → reply) and pushes
structured events to the browser over SSE. The user can approve, edit, or reject a
proposed action before the loop continues. The event stream is resumable: every event
carries a monotonically increasing sequence number, and a reconnecting client can ask
for everything it missed.

## Stack

- **Backend**: Python 3.11+ · FastAPI · SSE (Server-Sent Events) · in-memory event log
- **Frontend**: TypeScript · Vite · native `EventSource`
- **No external services**: search results are mocked, the "image" is a generated SVG
  placeholder, and the final reply is a canned text streamed token by token.

## Quick start

Requirements: Python 3.11+ and Node 18+.

```bash
# 1. backend deps
pip install -r backend/requirements.txt

# 2. frontend deps
cd frontend && npm install && cd ..

# 3. run both (backend :8000, frontend :5173 with proxy)
python run.py
```

Then open http://localhost:5173.

You can also run the two sides manually in two terminals:

```bash
uvicorn app.main:app --app-dir backend --port 8000
cd frontend && npm run dev
```

## Features

- Token-by-token streaming replies over SSE
- Confirmation cards: approve / edit-and-approve / reject
- Resumable event stream: reconnect with `?after_seq=N` and replay missed events
- The loop keeps running server-side even while the client is disconnected
- One active loop per session; concurrent messages are rejected with `409`
- Cancel a running loop at any time
- Debug panel: simulate disconnect, force image-generation failure, fire concurrent sends

More details (event catalog, state machine, design rules) live in the sections below —
they are expanded as the project grows.
