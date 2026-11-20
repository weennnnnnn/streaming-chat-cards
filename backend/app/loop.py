"""The simulated agent loop: search → confirm → generate → reply.

The loop is deliberately decoupled from HTTP connections: it runs as an
asyncio task owned by the session. Disconnecting the browser does not stop
it; events accumulate in the session's event log and are replayed when the
client reconnects. A cancel request cancels the task, which emits a
`cancelled` event and returns the session to idle.
"""

from __future__ import annotations

import asyncio
import random

from .imagegen import generate_placeholder_svg
from .store import (
    STATE_AWAITING_CONFIRMATION,
    STATE_GENERATING,
    STATE_IDLE,
    STATE_REPLYING,
    STATE_SEARCHING,
    Session,
)

SEARCH_DELAY_RANGE = (1.0, 2.0)
GENERATE_DELAY_RANGE = (5.0, 10.0)
TOKEN_DELAY = 0.03

FAKE_RESULTS = [
    {"title": "Studio Ghibli style — visual characteristics", "snippet": "soft palettes, painterly skies, ..."},
    {"title": "Prompt engineering patterns for image models", "snippet": "subject, style, lighting, composition"},
    {"title": "Example references", "snippet": "ghibli-style landscape, warm light, film grain"},
]


class AgentLoop:
    def __init__(self, session: Session, user_message: str) -> None:
        self.session = session
        self.user_message = user_message
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.get_running_loop().create_task(self.run())

    def cancel(self) -> bool:
        """Cancel the running loop. Returns False if nothing is running."""
        if self.task and not self.task.done():
            self.task.cancel()
            return True
        return False

    # ---- the loop -------------------------------------------------------

    async def run(self) -> None:
        s = self.session
        try:
            # Step 1: search ------------------------------------------------
            s.set_state(STATE_SEARCHING)
            await asyncio.sleep(random.uniform(*SEARCH_DELAY_RANGE))
            s.emit("progress", step="search", detail=f"found {len(FAKE_RESULTS)} results")

            # Draft the image prompt from the user message.
            prompt = self.user_message.strip() or "an image"

            # Step 2: confirmation card -------------------------------------
            s.set_state(STATE_AWAITING_CONFIRMATION)
            s.current_prompt = prompt
            s.emit("confirm", prompt=prompt, results=FAKE_RESULTS)

            # Wait for the user's decision. The future is resolved by
            # POST /api/confirm; a cancel cancels this task outright.
            assert s.confirm_future is not None
            action, final_prompt = await s.confirm_future

            if action == "reject":
                s.emit("message", role="assistant", text="好的，已取消本次生成。需要的话再发一条消息给我。")
                s.emit("done", reason="rejected")
                s.state = STATE_IDLE
                return

            # Step 3: generate image ----------------------------------------
            s.set_state(STATE_GENERATING)
            await asyncio.sleep(random.uniform(*GENERATE_DELAY_RANGE))

            if s.fail_image:
                # Rule: image-generation failures are surfaced as an error
                # event and NOT retried automatically — the user decides
                # whether to try again by sending a new message.
                raise RuntimeError("image generation failed (debug forced failure)")

            svg = generate_placeholder_svg(final_prompt)
            s.emit("image", svg=svg, prompt=final_prompt)

            # Step 4: stream the final reply token by token ------------------
            s.set_state(STATE_REPLYING)
            reply = (
                f"这是根据「{final_prompt}」生成的占位图。"
                "真实实现里这里会调用图像模型；本 Demo 用确定性 SVG 占位图代替。"
                "你可以随时发起新一轮生成，或打开调试面板模拟断连、失败和并发场景。"
            )
            for token in reply:
                s.emit("token", text=token)
                await asyncio.sleep(TOKEN_DELAY)

            s.emit("done", reason="completed")
            s.state = STATE_IDLE

        except asyncio.CancelledError:
            s.emit("cancelled")
            s.state = STATE_IDLE
            # Swallow the cancellation: the task ends cleanly rather than
            # propagating CancelledError into task-result handling.
        except Exception as exc:  # noqa: BLE001 - demo boundary
            s.emit("error", message=str(exc), step="generate_image")
            s.state = STATE_IDLE
        finally:
            s.confirm_future = None
            s.current_prompt = ""
