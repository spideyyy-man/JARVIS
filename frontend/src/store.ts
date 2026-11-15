// Small global store (useSyncExternalStore). Fed by the REST bootstrap and by live WebSocket events.
import { installServerSpeech } from "./serverSpeech";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api, ApiError, newConversationId, setUnauthorizedHandler } from "./api";
import type { Approval, AssistantState, AgentMessage, Message, MetricPoint, Metrics, Notification, RunningAgent, Settings, Status } from "./api";
import { bus } from "./ws";
import { VoiceEngine } from "./voice";
import type { VoiceState } from "./voice";

export interface LiveChat { messages: Message[]; streamText: string; streaming: boolean }
export interface Toast { id: number; kind: "approval" | "info" | "warning" | "error"; title: string; body: string }
export type RevKey = "chat" | "approval" | "agent" | "workflow" | "notification" | "tool" | "plan";

export interface State {
  auth: "unknown" | "ok" | "locked";
  connected: boolean;
  status: Status | null;
  settings: Settings | null;
  assistantState: AssistantState;
  stateDetail: string;
  metrics: Metrics | null;
  history: MetricPoint[];
  approvals: Record<string, Approval>;
  notifications: Notification[];
  runningAgents: Record<string, RunningAgent>;
  agentFeed: AgentMessage[];
  live: Record<string, LiveChat>;
  toasts: Toast[];
  rev: Record<RevKey, number>;
}

let state: State = {
  auth: "unknown", connected: false, status: null, settings: null, assistantState: "idle", stateDetail: "", metrics: null, history: [],
  approvals: {}, notifications: [], runningAgents: {}, agentFeed: [], live: {}, toasts: [],
  rev: { chat: 0, approval: 0, agent: 0, workflow: 0, notification: 0, tool: 0, plan: 0 },
};
const listeners = new Set<() => void>();

export const getState = () => state;
export function setState(patch: Partial<State>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
export function useStore<T>(selector: (s: State) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state));
}
const bump = (...keys: RevKey[]) => {
  const rev = { ...state.rev };
  keys.forEach((k) => rev[k]++);
  return rev;
};

const EMPTY_LIVE: LiveChat = { messages: [], streamText: "", streaming: false };
export const liveOf = (s: State, cid: string | null): LiveChat => (cid && s.live[cid]) || EMPTY_LIVE;
function patchLive(cid: string, fn: (l: LiveChat) => LiveChat, extra: Partial<State> = {}) {
  setState({ live: { ...state.live, [cid]: fn(state.live[cid] ?? EMPTY_LIVE) }, ...extra });
}

let toastSeq = 1;
export function pushToast(kind: Toast["kind"], title: string, body: string) {
  const id = toastSeq++;
  setState({ toasts: [...state.toasts.slice(-3), { id, kind, title, body }] });
  window.setTimeout(() => dismissToast(id), 7000);
}
export const dismissToast = (id: number) => setState({ toasts: state.toasts.filter((t) => t.id !== id) });

export function usePendingApprovals(): Approval[] {
  const map = useStore((s) => s.approvals);
  return useMemo(() => Object.values(map).filter((a) => a.status === "pending").sort((a, b) => b.created - a.created), [map]);
}

function applyStatus(status: Status) {
  const running: Record<string, RunningAgent> = {};
  status.running_agents.forEach((a) => (running[a.task_id] = a));
  setState({ status, assistantState: status.state, runningAgents: running });
}

/** Send a chat message. The id is generated client-side so stream events can never race the HTTP reply. */
export async function sendChat(text: string, conversationId?: string | null, source: "text" | "voice" = "text"): Promise<string> {
  const cid = conversationId || newConversationId();
  patchLive(cid, (l) => ({ ...l, streaming: true, streamText: "" }));
  try {
    await api.chat(text, cid, source);
  } catch (e) {
    patchLive(cid, (l) => ({ ...l, streaming: false }));
    throw e;
  }
  return cid;
}

export async function decideApproval(id: string, approve: boolean): Promise<void> {
  const ap = await api.decide(id, approve);
  setState({ approvals: { ...state.approvals, [ap.id]: ap }, rev: bump("approval") });
}

export async function bootstrap() {
  try {
    const [status, settings, approvals, notifications, hist] = await Promise.all([api.status(), api.settings(), api.approvals("all"), api.notifications(), api.systemHistory()]);
    const map: Record<string, Approval> = {};
    approvals.forEach((a) => (map[a.id] = a));
    voice.configure(settings.voice);
    // Local speech models, if the backend has them configured, replace the browser's speech service.
    await installServerSpeech().then(() => voice.detect(), () => undefined);
    if (settings.voice.wake_on_start) voice.setWakeMode(true);
    setState({ auth: "ok", settings, approvals: { ...state.approvals, ...map }, notifications, history: hist.points.slice(-150) });
    applyStatus(status);
    bus.connect();
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 401)) window.setTimeout(bootstrap, 3000);
  }
}

export function initStore() {
  setUnauthorizedHandler(() => {
    if (state.auth !== "locked") setState({ auth: "locked" });
    bus.disconnect();
  });
  bus.on("ws.open", () => setState({ connected: true }));
  bus.on("ws.close", () => {
    setState({ connected: false });
    api.status().catch(() => {}); // a 401 here flips the UI to the locked gate
  });
  bus.on("hello", (e) => {
    applyStatus(e.status as Status);
    // resync anything that may have changed while disconnected
    api.approvals("all").then((list) => {
      const map = { ...state.approvals };
      list.forEach((a) => (map[a.id] = a));
      setState({ approvals: map });
    }).catch(() => {});
  });
  bus.on("assistant.state", (e) => setState({ assistantState: e.state as AssistantState, stateDetail: String(e.detail ?? "") }));
  bus.on("metrics", (e) => {
    const m = e.metrics as Metrics;
    setState({ metrics: m, history: [...state.history.slice(-149), { ts: m.ts, cpu: m.cpu.percent, memory: m.memory.percent, disk: m.disk.percent }] });
  });
  bus.on("chat.start", (e) => {
    const msg = e.message as Message;
    patchLive(String(e.conversation_id), (l) => ({ messages: [...l.messages, msg], streamText: "", streaming: true }), { rev: bump("chat") });
  });
  bus.on("chat.delta", (e) => patchLive(String(e.conversation_id), (l) => ({ ...l, streaming: true, streamText: l.streamText + String(e.text ?? "") })));
  bus.on("chat.done", (e) => {
    const msg = e.message as Message;
    patchLive(String(e.conversation_id), (l) => ({ messages: [...l.messages, msg], streamText: "", streaming: false }), { rev: bump("chat") });
  });
  bus.on("chat.event", (e) => {
    const msg = e.message as Message;
    patchLive(String(e.conversation_id), (l) => ({ ...l, messages: [...l.messages, msg] }), { rev: bump("chat") });
  });
  bus.on("agent.status", (e) => {
    const running = { ...state.runningAgents };
    const id = String(e.task_id);
    if (e.status === "running") running[id] = { task_id: id, agent: String(e.agent), title: String(e.title ?? ""), started: Number(e.ts) || Date.now() / 1000 };
    else delete running[id];
    setState({ runningAgents: running, rev: bump("agent") });
  });
  bus.on("agent.message", (e) => setState({ agentFeed: [e.message as AgentMessage, ...state.agentFeed].slice(0, 80) }));
  bus.on("tool.executed", () => setState({ rev: bump("tool") }));
  bus.on("plan.created", () => setState({ rev: bump("plan") }));
  bus.on("approval.requested", (e) => {
    const ap = e.approval as Approval;
    setState({ approvals: { ...state.approvals, [ap.id]: ap }, rev: bump("approval") });
    pushToast("approval", `Approval needed: ${ap.tool}`, ap.preview.split("\n")[0]);
  });
  bus.on("approval.decided", (e) => {
    const ap = e.approval as Approval;
    setState({ approvals: { ...state.approvals, [ap.id]: ap }, rev: bump("approval") });
  });
  bus.on("workflow.run", () => setState({ rev: bump("workflow") }));
  bus.on("workflow.changed", () => setState({ rev: bump("workflow") }));
  bus.on("notification", (e) => {
    const n = e.notification as Notification;
    setState({ notifications: [n, ...state.notifications.filter((x) => x.id !== n.id)].slice(0, 40), rev: bump("notification") });
    pushToast(n.level === "error" ? "error" : n.level === "warning" ? "warning" : "info", n.title, n.body);
  });
  bus.on("assistant.announce", (e) => voice.announce(String(e.text ?? "")));
  bus.on("llm.failover", (e) => pushToast("warning", `Language model '${String(e.provider)}' failed`, `${String(e.error ?? "")}${e.next ? ` — switching to '${String(e.next)}'` : ""}`));
  void bootstrap();
}

/** Fetch with reload-on-deps. Keeps the previous data visible while refreshing. */
export function useApi<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fnRef.current().then(
      (d) => {
        if (!alive) return;
        setData(d);
        setError(null);
        setLoading(false);
      },
      (e: unknown) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      },
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

// -- voice engine singleton ------------------------------------------------------------------
export const voice = new VoiceEngine({
  sendChat: (text, cid) => sendChat(text, cid, "voice"),
  cancelChat: (cid) => api.cancelChat(cid),
  newConversationId,
  on: (type, fn) => bus.on(type, fn),
  report: (s) => bus.send({ type: "state", state: s }),
});
export function useVoice(): VoiceState {
  return useSyncExternalStore(voice.subscribe, voice.getState);
}
/** What the orb should show: local voice activity wins over the server-reported state. */
export function useAssistantState(): AssistantState {
  const server = useStore((s) => s.assistantState);
  const v = useVoice();
  if (v.phase === "speaking" || v.phase === "listening") return v.phase;
  if (v.phase === "thinking" && server === "idle") return "thinking";
  return server;
}
