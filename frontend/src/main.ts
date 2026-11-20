/**
 * Frontend application logic.
 *
 * Rendering model: every SSE event is applied to the DOM in order. On load
 * (or refresh) we reconnect with after_seq=0 and replay the whole session
 * log, so the UI rebuilds itself from the event stream alone — the DOM is a
 * pure fold over the event log. That is what makes refresh-recovery work
 * with zero client-side persistence.
 */

import {
  ChatEvent,
  StreamHandle,
  cancel,
  confirm as confirmDecision,
  connectStream,
  getState,
  sendMessage,
  setFailImage,
} from "./api";

const chatEl = document.getElementById("chat") as HTMLElement;
const inputEl = document.getElementById("input") as HTMLInputElement;
const sendBtn = document.getElementById("send") as HTMLButtonElement;
const cancelBtn = document.getElementById("cancel-btn") as HTMLButtonElement;
const connEl = document.getElementById("conn-status") as HTMLElement;
const dbgStateEl = document.getElementById("dbg-state") as HTMLElement;
const dbgDisconnectBtn = document.getElementById("dbg-disconnect") as HTMLButtonElement;
const dbgFailImageBtn = document.getElementById("dbg-fail-image") as HTMLButtonElement;
const dbgConcurrentBtn = document.getElementById("dbg-concurrent") as HTMLButtonElement;

let cursor = 0;
let stream: StreamHandle | null = null;
let reconnectTimer: number | null = null;
let streamingBubble: HTMLElement | null = null;
let liveConfirmCard: HTMLElement | null = null;

const STATUS_LABELS: Record<string, string> = {
  searching: "🔍 searching…",
  awaiting_confirmation: "⏸ waiting for your confirmation",
  generating: "🎨 generating image…",
  replying: "✍️ writing reply…",
  idle: "idle",
};

// ---- event rendering -------------------------------------------------------

function seqTag(ev: ChatEvent): string {
  return `<span class="seq-tag">#${ev.seq}</span>`;
}

function render(ev: ChatEvent): void {
  cursor = Math.max(cursor, ev.seq);
  switch (ev.type) {
    case "message": {
      const role = ev.data.role as string;
      const text = ev.data.text as string;
      if (role === "user") {
        addBubble("user", text, ev);
      } else {
        addBubble("assistant", text, ev);
      }
      break;
    }
    case "status": {
      setSessionState(ev.data.state as string);
      if (ev.data.state === "replying") streamingBubble = null; // start fresh bubble
      addSystemLine(STATUS_LABELS[ev.data.state as string] ?? String(ev.data.state), ev);
      break;
    }
    case "progress": {
      addSystemLine(`${ev.data.step}: ${ev.data.detail}`, ev);
      break;
    }
    case "confirm": {
      liveConfirmCard = renderConfirmCard(ev);
      break;
    }
    case "token": {
      if (!streamingBubble) {
        streamingBubble = addBubble("assistant", "", null);
      }
      streamingBubble.textContent += ev.data.text as string;
      scrollDown();
      break;
    }
    case "image": {
      const block = document.createElement("div");
      block.className = "image-block";
      const holder = document.createElement("div");
      holder.innerHTML = ev.data.svg as string; // server-generated trusted SVG
      block.appendChild(holder);
      const caption = document.createElement("div");
      caption.className = "caption";
      caption.innerHTML = `placeholder image ${seqTag(ev)}`;
      block.appendChild(caption);
      chatEl.appendChild(block);
      scrollDown();
      break;
    }
    case "done": {
      const reason = ev.data.reason as string;
      addSystemLine(reason === "rejected" ? "✅ loop finished (rejected)" : "✅ loop finished", ev);
      liveConfirmCard?.classList.add("resolved");
      liveConfirmCard = null;
      setSessionState("idle");
      break;
    }
    case "error": {
      addErrorCard(`image generation failed: ${ev.data.message} — not retried; send a new message to try again.`, ev);
      setSessionState("idle");
      break;
    }
    case "cancelled": {
      addSystemLine("🚫 cancelled by user", ev);
      liveConfirmCard?.classList.add("resolved");
      liveConfirmCard = null;
      setSessionState("idle");
      break;
    }
  }
}

// ---- DOM helpers ------------------------------------------------------------

function addBubble(role: "user" | "assistant", text: string, ev: ChatEvent | null): HTMLElement {
  const el = document.createElement("div");
  el.className = `bubble ${role}`;
  el.textContent = text;
  if (ev) el.innerHTML += seqTag(ev);
  chatEl.appendChild(el);
  scrollDown();
  return el;
}

function addSystemLine(text: string, ev: ChatEvent): void {
  const el = document.createElement("div");
  el.className = "system-line";
  el.innerHTML = `${escapeHtml(text)} ${seqTag(ev)}`;
  chatEl.appendChild(el);
  scrollDown();
}

function addErrorCard(text: string, ev: ChatEvent): void {
  const el = document.createElement("div");
  el.className = "error-card";
  el.innerHTML = `⚠️ ${escapeHtml(text)} ${seqTag(ev)}`;
  chatEl.appendChild(el);
  scrollDown();
}

function renderConfirmCard(ev: ChatEvent): HTMLElement {
  const card = document.createElement("div");
  card.className = "card";
  const prompt = ev.data.prompt as string;
  const results = (ev.data.results as { title: string }[] | undefined) ?? [];

  card.innerHTML = `
    <h3>Confirm before generating ${seqTag(ev)}</h3>
    <div class="prompt">"${escapeHtml(prompt)}"</div>
    <div class="results">search hints: ${results.map((r) => escapeHtml(r.title)).join(" · ") || "—"}</div>
    <textarea style="display:none"></textarea>
    <div class="actions">
      <button data-act="approve">✅ Approve</button>
      <button data-act="edit">✏️ Edit & approve</button>
      <button data-act="reject">🚫 Reject</button>
    </div>`;
  chatEl.appendChild(card);
  scrollDown();

  card.addEventListener("click", async (e) => {
    const btn = (e.target as HTMLElement).closest("button");
    if (!btn || card.classList.contains("resolved")) return;
    const act = btn.dataset.act;
    try {
      if (act === "approve") {
        await confirmDecision("approve");
      } else if (act === "edit") {
        const ta = card.querySelector("textarea") as HTMLTextAreaElement;
        if (ta.style.display === "none") {
          // First click reveals the editor, second click submits.
          ta.style.display = "block";
          ta.value = prompt;
          ta.focus();
          btn.textContent = "✅ Approve edited";
          return;
        }
        await confirmDecision("edit", ta.value.trim() || prompt);
      } else if (act === "reject") {
        await confirmDecision("reject");
      }
      card.classList.add("resolved");
    } catch (err) {
      addErrorCard(`confirm failed: ${(err as Error).message}`, { seq: cursor, type: "error", data: {}, ts: 0 });
    }
  });
  return card;
}

function setSessionState(state: string): void {
  dbgStateEl.textContent = `state: ${state}`;
  dbgStateEl.classList.toggle("dirty", state !== "idle");
}

function scrollDown(): void {
  chatEl.scrollTop = chatEl.scrollHeight;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
}

// ---- connection management ---------------------------------------------------

function connect(): void {
  stream?.close();
  stream = connectStream(cursor, {
    onOpen: () => {
      connEl.textContent = "connected";
      connEl.className = "conn-status online";
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    },
    onError: () => {
      connEl.textContent = "connection lost — retrying…";
      connEl.className = "conn-status offline";
      stream?.close();
      if (reconnectTimer === null) {
        reconnectTimer = window.setTimeout(connect, 1500);
      }
    },
    onEvent: render,
  });
}

// ---- UI wiring ---------------------------------------------------------------

async function trySend(content: string): Promise<void> {
  try {
    await sendMessage(content);
    // The user bubble itself is rendered from the server's `message` event —
    // drawing it here too would duplicate it (UI stays a pure fold over events).
  } catch (err) {
    const e = err as Error & { status?: number };
    if (e.status === 409) {
      addErrorCard("409 — a loop is already active. Confirm or cancel it first (rule: one loop per session).", {
        seq: cursor,
        type: "error",
        data: {},
        ts: 0,
      });
    } else {
      addErrorCard(`send failed: ${e.message}`, { seq: cursor, type: "error", data: {}, ts: 0 });
    }
  }
}

sendBtn.addEventListener("click", () => {
  const content = inputEl.value.trim();
  if (content) {
    inputEl.value = "";
    void trySend(content);
  }
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendBtn.click();
});

cancelBtn.addEventListener("click", async () => {
  try {
    await cancel();
  } catch (err) {
    addErrorCard(`cancel failed: ${(err as Error).message}`, { seq: cursor, type: "error", data: {}, ts: 0 });
  }
});

// Debug panel ------------------------------------------------------------------

dbgDisconnectBtn.addEventListener("click", () => {
  stream?.close();
  stream = null;
  connEl.textContent = "disconnected (simulated)";
  connEl.className = "conn-status offline";
  addSystemLine("🔌 debug: connection closed — the loop keeps running server-side", {
    seq: cursor,
    type: "local",
    data: {},
    ts: 0,
  });
  reconnectTimer = window.setTimeout(connect, 3000);
});

let failImageOn = false;
dbgFailImageBtn.addEventListener("click", async () => {
  failImageOn = !failImageOn;
  await setFailImage(failImageOn);
  dbgFailImageBtn.textContent = failImageOn ? "Image failure: ON (click to disable)" : "Toggle image failure";
});

dbgConcurrentBtn.addEventListener("click", () => {
  // Fire two sends at once — the server must reject exactly one with 409.
  void Promise.allSettled([
    sendMessage("concurrent A"),
    sendMessage("concurrent B"),
  ]).then(([a, b]) => {
    const statuses = [a, b].map((r) => (r.status === "fulfilled" ? "accepted" : `rejected(${(r.reason as Error & { status: number }).status})`));
    addSystemLine(`debug concurrent sends → [${statuses.join(", ")}] — exactly one 409 expected`, {
      seq: cursor,
      type: "local",
      data: {},
      ts: 0,
    });
  });
});

// ---- boot: replay the whole log so refresh fully restores the UI ---------------

async function boot(): Promise<void> {
  try {
    const s = await getState();
    setSessionState(s.state);
    if (s.pending_confirm) {
      // The confirm event itself is in the log and will be replayed.
    }
  } catch {
    /* backend not up yet — the stream will reconnect */
  }
  connect();
}

void boot();
