// WebSocket event bus with auto-reconnect (exponential backoff). Auth is the HttpOnly cookie.

export interface WsEvent {
  type: string;
  ts?: number;
  [key: string]: unknown;
}
type Handler = (e: WsEvent) => void;

class Bus {
  private handlers = new Map<string, Set<Handler>>();
  private sock: WebSocket | null = null;
  private retry = 0;
  private timer: number | undefined;
  private wanted = false;
  connected = false;

  /** Subscribe to one event type, or "*" for all. Returns an unsubscribe function. */
  on(type: string, fn: Handler): () => void {
    let set = this.handlers.get(type);
    if (!set) this.handlers.set(type, (set = new Set()));
    set.add(fn);
    return () => set.delete(fn);
  }

  emit(e: WsEvent) {
    this.handlers.get(e.type)?.forEach((fn) => fn(e));
    this.handlers.get("*")?.forEach((fn) => fn(e));
  }

  connect() {
    this.wanted = true;
    if (this.sock && this.sock.readyState <= WebSocket.OPEN) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${location.host}/ws`);
    this.sock = sock;
    sock.onopen = () => {
      this.retry = 0;
      this.connected = true;
      this.emit({ type: "ws.open" });
    };
    sock.onmessage = (m) => {
      try {
        const e = JSON.parse(String(m.data)) as WsEvent;
        if (e && typeof e.type === "string") this.emit(e);
      } catch {
        /* ignore malformed frames */
      }
    };
    sock.onclose = (ev) => {
      if (this.sock !== sock) return;
      this.sock = null;
      const was = this.connected;
      this.connected = false;
      if (was || ev.code === 4401) this.emit({ type: "ws.close", code: ev.code });
      if (!this.wanted) return;
      const delay = Math.min(15000, 500 * 2 ** this.retry++) + Math.random() * 250;
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
    sock.onerror = () => sock.close();
  }

  disconnect() {
    this.wanted = false;
    window.clearTimeout(this.timer);
    this.sock?.close();
  }

  send(msg: Record<string, unknown>) {
    if (this.sock?.readyState === WebSocket.OPEN) this.sock.send(JSON.stringify(msg));
  }
}

export const bus = new Bus();
