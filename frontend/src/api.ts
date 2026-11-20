/** Typed client for the backend REST + SSE API. */

export interface ChatEvent {
  seq: number;
  type: string;
  data: Record<string, unknown>;
  ts: number;
}

export interface SessionState {
  state: string;
  last_seq: number;
  pending_confirm: { prompt: string } | null;
  fail_image: boolean;
}

async function jsonOrThrow(res: Response): Promise<unknown> {
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      /* ignore */
    }
    throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status, detail });
  }
  return res.json();
}

export async function sendMessage(content: string): Promise<unknown> {
  const res = await fetch("/api/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return jsonOrThrow(res);
}

export async function confirm(action: "approve" | "edit" | "reject", prompt?: string): Promise<unknown> {
  const res = await fetch("/api/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, prompt }),
  });
  return jsonOrThrow(res);
}

export async function cancel(): Promise<unknown> {
  const res = await fetch("/api/cancel", { method: "POST" });
  return jsonOrThrow(res);
}

export async function getState(): Promise<SessionState> {
  return (await jsonOrThrow(await fetch("/api/state"))) as SessionState;
}

export async function setFailImage(enabled: boolean): Promise<void> {
  await jsonOrThrow(
    await fetch("/api/debug/fail-image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }),
  );
}

export interface StreamHandlers {
  onEvent: (ev: ChatEvent) => void;
  onOpen: () => void;
  onError: () => void;
}

export interface StreamHandle {
  close: () => void;
}

/**
 * Open the SSE stream from a given sequence number.
 *
 * We manage reconnection manually (close + reopen with `after_seq=cursor`)
 * instead of relying on EventSource's built-in reconnect: this way the
 * cursor logic lives in one place and works after tab sleep, server restart
 * or proxy hiccups. The backend also honors Last-Event-ID for native
 * reconnects — both paths deliver the same guarantee.
 */
export function connectStream(afterSeq: number, handlers: StreamHandlers): StreamHandle {
  const KNOWN = [
    "message",
    "status",
    "progress",
    "confirm",
    "token",
    "image",
    "done",
    "error",
    "cancelled",
  ];
  const es = new EventSource(`/api/stream?after_seq=${afterSeq}`);
  let closedByUs = false;

  for (const type of KNOWN) {
    es.addEventListener(type, (e) => {
      const ev = JSON.parse((e as MessageEvent).data) as ChatEvent;
      handlers.onEvent(ev);
    });
  }
  es.onopen = handlers.onOpen;
  es.onerror = () => {
    if (closedByUs) return;
    handlers.onError();
  };

  return {
    close: () => {
      closedByUs = true;
      es.close();
    },
  };
}
