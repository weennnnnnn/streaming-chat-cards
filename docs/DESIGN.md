# Streaming chat cards — architecture & design notes

This document expands the overview in the top-level README: the full event
catalog, the state machine, the three behavioral rules, and the reasoning
behind the key design decisions.

## 1. Architecture

```
Browser (TS/Vite)                    Backend (FastAPI, asyncio)
┌─────────────────┐                 ┌──────────────────────────────┐
│ UI (pure fold   │   POST /api/*   │  Session                     │
│  over events)   │ ──────────────► │   ├─ EventLog (seq, in-mem)  │
│                 │                 │   ├─ state machine           │
│ SSE client      │ ◄────────────── │   └─ AgentLoop (async task)  │
│  cursor=seq     │  GET /api/stream│        search → confirm →    │
│  + reconnect    │   ?after_seq=N  │        generate → reply      │
└─────────────────┘                 └──────────────────────────────┘
```

The central idea: **the DOM is a pure fold over the event log.** Every UI
state the user sees is the result of applying events 1..N in order. Nothing
client-side is authoritative — which is what makes refresh recovery and
reconnect-replay trivially correct.

### Loop ↔ connection decoupling

The agent loop is an `asyncio.Task` owned by the `Session`, never by an HTTP
handler or a websocket/SSE connection. Consequences:

- If the browser disconnects mid-loop, the loop keeps running; events pile
  up in the log; when the client reconnects with `after_seq`, it receives
  everything it missed, in order.
- N clients could watch the same session concurrently (each subscriber gets
  a replay + a live queue).
- "Simulate disconnect" in the debug panel really closes the EventSource —
  the demo then shows tokens continuing to accumulate server-side and being
  replayed on reconnect.

## 2. Event catalog

| type       | emitted when                | payload                       |
| ---------- | --------------------------- | ----------------------------- |
| `message`  | user msg accepted / assistant text | `role`, `text`         |
| `status`   | loop step changed           | `state`                       |
| `progress` | coarse detail inside a step | `step`, `detail`              |
| `confirm`  | confirmation card shown     | `prompt`, `results[]`         |
| `token`    | one token of final reply    | `text`                        |
| `image`    | SVG placeholder generated   | `svg`, `prompt`               |
| `done`     | loop finished               | `reason: completed\|rejected` |
| `error`    | loop failed                 | `message`, `step`             |
| `cancelled`| user cancelled              | —                             |

Every event carries `seq` (monotonic, starts at 1) and `ts` (epoch ms).
SSE frames use `id: <seq>` so native EventSource reconnects also resume
correctly via the `Last-Event-ID` header.

## 3. State machine

```
            post /api/messages
   ┌──────┐ ─────────────────────► ┌───────────┐
   │ idle │                        │ searching │
   └──────┘ ◄──────────────┐      └─────┬─────┘
      ▲                    │            │ confirm card
      │ done / error /     │            ▼
      │ cancelled          │      ┌───────────────────────┐
      │                    └──────┤ awaiting_confirmation │
      │                    reject │  approve │ edit       │
      │                    ┌──────┴───────────┴───────────┐
      │                    │        ▼                     │
      │                    │  ┌────────────┐  error       │
      └────────────────────┼──│ generating │──────────────► (error event → idle)
                           │  └─────┬──────┘              │
                           │        │ image ok            │
                           │        ▼                     │
                           │  ┌────────────┐              │
                           └──│  replying  │─── done ────►┘
                              └────────────┘
   cancel: legal from searching / awaiting_confirmation / generating / replying
           → cancelled event → idle
```

Invariants:

- exactly one loop per session; `POST /api/messages` returns **409** unless state == `idle`
- `POST /api/confirm` returns **409** unless state == `awaiting_confirmation`
- cancel from `idle` returns **409**
- every transition emits a `status` event, so the UI can never drift from the server

## 4. The three rules

**Rule 1 — new message while a confirmation is pending.**
Rejected with `409 LOOP_ACTIVE`. One active loop keeps the protocol simple
and the UX honest: a second message has no well-defined meaning while the
loop is blocked on the user's decision. The UI explains this instead of
silently queueing.

**Rule 2 — image generation fails.**
Emitted as an `error` event; **no automatic retry**. Auto-retry is exactly
the kind of hidden behavior that erodes trust in agent systems — and it
would double the latency before surfacing a second failure. The user is
told what happened and decides whether to send a new message. The debug
panel can force this failure to demo the path.

**Rule 3 — refresh / reconnect.**
`GET /api/state` gives a snapshot (current state + last_seq +
pending_confirm), and `GET /api/stream?after_seq=N` replays the missed log.
On load the client always replays the full log from seq 1, so the rebuilt
UI is exactly what the user saw before refreshing — including the pending
confirmation card, which stays actionable because the loop is still waiting
on the server.

## 5. Other decisions worth explaining

**SSE over WebSocket.** One-way server→client push is all we need (actions
go over plain POSTs). SSE gives automatic native reconnect semantics
(`Last-Event-ID`), plain-text frames, and trivial proxying. WS would force
us to reinvent ack/replay on top of it.

**In-memory log.** Correct for the demo scope and honest about the
production path: swap `EventLog` for a durable ring buffer (e.g. Redis
stream / Postgres table keyed by session) — the sequence-number contract
doesn't change.

**Manual reconnect on the client.** EventSource auto-reconnects, but the
cursor logic then splits between the browser (Last-Event-ID) and the app.
Closing + reopening with `after_seq=cursor` keeps one code path; the
backend supports both.

**Cancellation.** `POST /api/cancel` cancels the asyncio task; the task
converts `CancelledError` into a `cancelled` event and resets state —
cancel is also legal while the loop is waiting on the confirmation future.

## 6. Running & verifying

```bash
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
python run.py                # http://localhost:5173

# end-to-end checks (needs the backend on :8000)
python scripts/smoke_test.py
```

The smoke test drives the SSE stream and asserts: happy path ordering,
409-on-active-loop, reject, mid-generate cancel, forced failure (error, no
image, no retry), and full-log replay ordering.
