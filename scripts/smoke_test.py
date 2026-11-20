"""End-to-end smoke test for the streaming-chat-cards backend.

Requires a running server on http://127.0.0.1:8000:

    uvicorn app.main:app --app-dir backend --port 8000
    python scripts/smoke_test.py

Drives the SSE stream, reacts to confirmation cards, and checks:
1. health
2. happy path: message -> search -> confirm -> approve -> image -> tokens -> done
3. 409 while a loop is active
4. reject path
5. cancel mid-generate
6. forced image failure (debug flag) -> error, no image, no retry
7. reconnect replay after_seq=0 returns the full ordered log
"""

import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"
TERMINAL = {"done", "error", "cancelled"}
PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


async def run_loop(client: httpx.AsyncClient, after_seq: int, on_confirm, cancel_after=None):
    """Consume the stream to a terminal event, reacting to confirm cards.

    on_confirm: None | "approve" | "reject" | callable(events_so_far) -> None
    cancel_after: optional (event_type, delay) — after seeing event_type,
                  wait delay seconds then POST /api/cancel.
    """
    seen, cancel_task = [], None
    async with client.stream("GET", f"{BASE}/api/stream", params={"after_seq": after_seq}) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            seen.append(ev)
            if ev["type"] == "confirm" and on_confirm is not None:
                if callable(on_confirm):
                    on_confirm(seen)
                elif on_confirm == "approve":
                    await client.post(f"{BASE}/api/confirm", json={"action": "approve"})
                elif on_confirm == "reject":
                    await client.post(f"{BASE}/api/confirm", json={"action": "reject"})
            if cancel_after and ev["type"] == cancel_after[0] and cancel_task is None:
                delay = cancel_after[1]
                cancel_task = asyncio.create_task(_cancel_later(client, delay))
            if ev["type"] in TERMINAL:
                break
    if cancel_task:
        await cancel_task
    return seen


async def _cancel_later(client: httpx.AsyncClient, delay: float) -> None:
    await asyncio.sleep(delay)
    await client.post(f"{BASE}/api/cancel")


async def main() -> None:
    async with httpx.AsyncClient(trust_env=False, timeout=60.0) as client:
        # 1. health
        r = await client.get(f"{BASE}/healthz")
        check("healthz", r.status_code == 200)
        r = await client.post(f"{BASE}/api/cancel")  # reset any stale state
        state = (await client.get(f"{BASE}/api/state")).json()
        check("session idle at start", state["state"] == "idle", str(state["state"]))
        last_seq = state["last_seq"]

        # 2. happy path
        r = await client.post(f"{BASE}/api/messages", json={"content": "a ghibli style landscape"})
        check("start loop", r.status_code == 200, f"status={r.status_code}")
        events = await run_loop(client, last_seq, on_confirm="approve")
        types = [e["type"] for e in events]
        seqs = [e["seq"] for e in events]
        check("reaches done", "done" in types)
        check("confirm card present", "confirm" in types)
        check("image present", "image" in types)
        check("tokens streamed", types.count("token") > 20, f"{types.count('token')} tokens")
        check("seq strictly increasing", all(b > a for a, b in zip(seqs, seqs[1:])))
        img = next((e for e in events if e["type"] == "image"), None)
        check("image is svg", img is not None and img["data"]["svg"].startswith("<svg"))
        last_seq = events[-1]["seq"]

        # 3. 409 while a loop is active
        await client.post(f"{BASE}/api/messages", json={"content": "hold the confirm open"})
        r = await client.post(f"{BASE}/api/messages", json={"content": "conflict"})
        check("409 while loop active", r.status_code == 409, str(r.status_code))
        events = await run_loop(client, last_seq, on_confirm="approve")
        last_seq = events[-1]["seq"]
        state = (await client.get(f"{BASE}/api/state")).json()
        check("idle after happy path 2", state["state"] == "idle")

        # 4. reject path
        await client.post(f"{BASE}/api/messages", json={"content": "reject me"})
        events = await run_loop(client, last_seq, on_confirm="reject")
        types = [e["type"] for e in events]
        check("reject ends with done", types[-1] == "done" and "image" not in types, str(types[-3:]))
        last_seq = events[-1]["seq"]

        # 5. cancel mid-generate
        await client.post(f"{BASE}/api/messages", json={"content": "cancel me"})
        events = await run_loop(client, last_seq, on_confirm="approve", cancel_after=("status", 1.0))
        types = [e["type"] for e in events]
        check("cancelled emitted", "cancelled" in types, str(types[-3:]))
        state = (await client.get(f"{BASE}/api/state")).json()
        check("idle after cancel", state["state"] == "idle")
        last_seq = events[-1]["seq"]

        # 6. forced image failure -> error, no retry, no image
        await client.post(f"{BASE}/api/debug/fail-image", json={"enabled": True})
        await client.post(f"{BASE}/api/messages", json={"content": "fail please"})
        events = await run_loop(client, last_seq, on_confirm="approve")
        types = [e["type"] for e in events]
        check("error emitted on failure", "error" in types, str(types[-3:]))
        check("no image on failure", "image" not in types)
        await client.post(f"{BASE}/api/debug/fail-image", json={"enabled": False})
        last_seq = events[-1]["seq"]

        # 7. replay from 0: ordered log, includes everything
        total = (await client.get(f"{BASE}/api/state")).json()["last_seq"]
        replay = []
        async with client.stream("GET", f"{BASE}/api/stream", params={"after_seq": 0}) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    replay.append(json.loads(line[6:]))
                    if len(replay) >= total:
                        break
        rseqs = [e["seq"] for e in replay]
        check("replay ordered from seq 1", rseqs[0] == 1 and all(b > a for a, b in zip(rseqs, rseqs[1:])), f"len={len(replay)}/{total}")
        replay_types = {e["type"] for e in replay}
        check("replay covers all kinds", {"message", "status", "confirm", "image", "token", "done", "error", "cancelled"} <= replay_types, str(replay_types))

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed", flush=True)
    if FAIL:
        print("FAILED:", FAIL, flush=True)
        sys.exit(1)


asyncio.run(main())
