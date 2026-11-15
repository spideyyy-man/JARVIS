// Typed client for the JARVIS backend. The auth token lives in an HttpOnly cookie; this file never sees it.

export type AssistantState = "idle" | "listening" | "thinking" | "speaking" | "executing" | "error";
export type Level = "read" | "confirm";

export interface LlmInfo {
  chain: { name: string; model: string }[];
  last_used: string | null;
  degraded: boolean;
  recent_failures: unknown[];
}
export interface RunningAgent { task_id: string; agent: string; title: string; started: number; parent_id?: string | null }
export interface ActiveTask { conversation_id: string; title: string; started: number }
export interface VoiceSettings { wake_word: string; continuous: boolean; voice_name: string; rate: number; speak_replies: boolean; pitch?: number }
export interface Status {
  state: AssistantState;
  active_task: ActiveTask | null;
  llm: LlmInfo;
  running_agents: RunningAgent[];
  pending_approvals: number;
  voice: Partial<VoiceSettings> & { stt: string; tts: string; wake_word: string };
  version: string;
  time: number;
}
export interface Metrics {
  ts: number;
  cpu: { percent: number; cores: number; load_avg: number[] };
  memory: { percent: number; used_gb: number; total_gb: number; available_gb: number };
  swap: { percent: number; used_gb: number };
  disk: { percent: number; free_gb: number; total_gb: number };
  net: { sent_mb: number; recv_mb: number };
}
export interface MetricPoint { ts: number; cpu: number; memory: number; disk: number }
export interface Proc { pid: number; name: string; user: string; cpu_percent: number; memory_percent: number; status: string; command: string }
export interface SystemStats extends Metrics {
  platform: string;
  uptime_hours: number;
  top_cpu: Proc[];
  top_memory: Proc[];
  assessment: string[];
  history: HistorySummary;
}
export interface HistorySummary { window_minutes?: number; samples?: number; cpu_avg?: number; memory_avg?: number; cpu_max?: number; memory_max?: number }
export interface ToolUse { agent: string; tool: string; ok: boolean; pending: boolean }
export interface MessageMeta {
  source?: string; actor?: string; task_id?: string; provider?: string; tools?: ToolUse[]; pending_approvals?: string[];
  errors?: string[]; agents?: string[]; interrupted?: boolean; approval?: string; status?: string;
}
export interface Message { id: string; conversation_id: string; role: "user" | "assistant" | "event"; content: string; meta: MessageMeta; created: number }
export interface ConversationSummary { id: string; title: string; summary?: string; created?: number; updated: number; messages?: number }
export interface PlanStep { n: number; title: string; detail: string; status: string }
export interface Citations { cited: number; verified: string[]; unverified: string[]; retrieved_sources: number }
export interface Evaluation { ok?: boolean; issues?: string[]; pending_approvals?: number; citations?: Citations }
export interface Task {
  id: string; conversation_id?: string | null; parent_id: string | null; agent: string; title: string; status: string;
  plan: PlanStep[]; result?: string; evaluation: Evaluation; created: number; updated?: number;
}
export interface AgentMessage { id: string; task_id: string; sender: string; recipient: string; type: string; content: string; data: Record<string, unknown>; created: number }
export interface TaskDetail extends Task { messages: AgentMessage[]; subtasks: Task[] }
export interface Conversation extends ConversationSummary { messages: Message[]; tasks: Task[] }
export interface SearchHit { id: string; conversation_id: string; role: string; created: number; title: string; snippet: string }
export interface AgentInfo {
  name: string; title: string; responsibility: string; status: string; active: RunningAgent[];
  tools: { name: string; level: Level; trusted: boolean }[];
  stats: { total: number; done: number | null; failed: number | null; last: number | null };
  recent_tasks: { id: string; title: string; status: string; created: number; updated: number }[];
}
export interface Approval {
  id: string; tool: string; args: Record<string, unknown>; preview: string; agent: string; task_id: string | null;
  conversation_id: string | null; origin: Record<string, unknown>; status: "pending" | "approved" | "executed" | "failed" | "denied";
  result: string; created: number; decided: number | null;
}
export interface AuditEntry { id: number; ts: number; actor: string; tool: string; level: string; decision: string; args: unknown; outcome: string; detail: string }
export interface ToolInfo { name: string; description: string; level: Level; trust_eligible: boolean; trusted: boolean; agents: string[] }
export interface Settings {
  trusted_tools: string[]; default_project: string; voice: VoiceSettings; allowed_roots: string[]; data_dir: string; llm: LlmInfo;
  embedder: string; search_provider: string; speech: { stt: string; tts: string };
  levels: { read: number; confirm: number; trust_eligible: string[] };
}
export interface Schedule { type: "interval" | "cron" | "once" | "manual"; seconds?: number; expr?: string; at?: number }
export interface WorkflowStep { id?: string; tool?: string; args?: Record<string, unknown>; notify?: string; agent_prompt?: string; [k: string]: unknown }
export interface Workflow {
  id: string; name: string; description?: string; schedule: Schedule; steps?: WorkflowStep[]; enabled: number;
  next_run: number | null; last_run: number | null; created?: number;
}
export interface RunLogEntry { step: string; kind: string; started: number; attempts: number; status: string; detail?: string; duration_s?: number }
export interface WorkflowRun {
  id: string; workflow_id: string; workflow_name?: string | null; status: string; trigger: string; cursor: number;
  log: RunLogEntry[]; started: number; finished: number | null;
}
export interface Notification { id: string; level: string; title: string; body: string; read: number; created: number }
export interface FileActivity { ts: number; actor: string; tool: string; decision: string; args: unknown; outcome: string }
export interface DailySummary {
  date: string; conversations: number; requests: number; topics: string[]; actions_executed: { tool: string; n: number }[];
  approved_changes: string[]; tasks_completed: string[]; tasks_open: string[]; workflow_runs: { status: string; n: number }[];
  documents_indexed: number; commits_today?: string[];
}
export interface Dashboard {
  status: Status; metrics: Metrics; metrics_history: MetricPoint[]; recent_conversations: ConversationSummary[];
  running_agents: RunningAgent[]; recent_tasks: { id: string; agent: string; title: string; status: string; updated: number }[];
  workflows: Workflow[]; pending_approvals: Approval[]; file_activity: FileActivity[];
  projects: { default: string; dev_tasks_open: number }; daily_summary: DailySummary; notifications: Notification[];
}
export interface DirEntry { name: string; path: string; is_dir: boolean; size: number; modified: string }
export interface DirListing { path: string; count: number; entries: DirEntry[] }
export interface FileContent { path: string; binary?: boolean; size?: number; total_chars?: number; offset?: number; content?: string; truncated?: boolean }
export interface FileSearch { root: string; scanned: number; matched: number; results: { path: string; size: number; modified: string; match?: string }[]; timed_out: boolean }
export interface Disks { partitions: { mount: string; fs: string; total_gb: number; free_gb: number; percent: number }[] }
export interface Apps { desktop_applications: { name: string; exec: string; source: string }[]; developer_tools_on_path: { name: string; path: string }[] }
export interface DevEnv { toolchains: Record<string, string>; editors: string[]; shell: string; projects_root: string; projects: { path: string; stack: string[]; git: boolean }[] }
export interface RepoOverview {
  path: string;
  git?: { branch: string; uncommitted_changes: number; changed_files: string[]; recent_commits: string[]; remotes: string[] } | null;
  languages: { language: string; files: number; lines: number }[];
  todo_markers: number; top_level: string[]; manifests: Record<string, string>; readme_excerpt: string | null;
  test_command: string | null; entry_points: string[];
}
export interface DevTask { id: string; project: string; title: string; status: string; notes: string; due: number | null; created: number; updated: number; overdue?: boolean }
export interface KbSpace { space: string; documents: number; chunks: number }
export interface KbDocument { id: string; space: string; title: string; source: string; kind: string; chunks: number; created: number }
export interface KbResult { chunk_id: string; title: string; source: string; locator: string; space: string; score: number; similarity: number; text: string }
export interface MemoryItem { id: string; kind: string; content: string; created?: number }
export interface ResearchTask { id: string; title: string; status: string; result: string; evaluation: Evaluation; created: number }
export interface Source { id: string; task_id: string; url: string; title: string; kind: string; snippet: string; fetched: number }
export interface Report { name: string; size: number; modified: number }

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((e) => (e && typeof e === "object" && "msg" in e ? `${(e as { loc?: unknown[] }).loc?.slice(1).join(".") ?? ""}: ${(e as { msg: string }).msg}` : JSON.stringify(e))).join("; ");
    return JSON.stringify(d);
  }
  return fallback;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Cannot reach the JARVIS backend.");
  }
  let data: unknown = null;
  const text = await res.text();
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (res.status === 401) onUnauthorized();
  if (!res.ok) throw new ApiError(res.status, detailOf(data, text.slice(0, 200) || `${res.status} ${res.statusText}`));
  return data as T;
}

const q = (params: Record<string, string | number | boolean | undefined>) => {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
};
const enc = encodeURIComponent;

export const newConversationId = () => {
  const b = new Uint8Array(6);
  crypto.getRandomValues(b);
  return "c_" + Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
};

export const api = {
  status: () => req<Status>("GET", "/api/status"),
  dashboard: () => req<Dashboard>("GET", "/api/dashboard"),
  chat: (text: string, conversation_id: string, source: "text" | "voice" = "text") =>
    req<{ conversation_id: string; streaming: boolean }>("POST", "/api/chat", { text, conversation_id, source }),
  cancelChat: (id: string) => req<{ cancelled: boolean }>("POST", `/api/chat/${enc(id)}/cancel`),
  conversations: () => req<ConversationSummary[]>("GET", "/api/conversations"),
  searchConversations: (query: string) => req<SearchHit[]>("GET", `/api/conversations/search${q({ q: query })}`),
  conversation: (id: string) => req<Conversation>("GET", `/api/conversations/${enc(id)}`),
  deleteConversation: (id: string) => req<unknown>("DELETE", `/api/conversations/${enc(id)}`),
  agents: () => req<AgentInfo[]>("GET", "/api/agents"),
  tasks: () => req<Task[]>("GET", "/api/tasks"),
  task: (id: string) => req<TaskDetail>("GET", `/api/tasks/${enc(id)}`),
  agentMessages: () => req<AgentMessage[]>("GET", "/api/agent-messages"),
  systemStats: () => req<SystemStats>("GET", "/api/system/stats"),
  systemHistory: () => req<{ points: MetricPoint[]; summary: HistorySummary }>("GET", "/api/system/history"),
  processes: (sort_by: string, query: string, limit = 25) => req<{ total: number; processes: Proc[] }>("GET", `/api/system/processes${q({ sort_by, q: query, limit })}`),
  disks: () => req<Disks>("GET", "/api/system/disks"),
  apps: () => req<Apps>("GET", "/api/system/apps"),
  devEnv: () => req<DevEnv>("GET", "/api/system/dev-environment"),
  listDir: (path: string, hidden = false) => req<DirListing>("GET", `/api/files/list${q({ path, hidden })}`),
  readFile: (path: string) => req<FileContent>("GET", `/api/files/read${q({ path })}`),
  searchFiles: (p: { q?: string; root?: string; ext?: string; days?: number; text?: string }) => req<FileSearch>("GET", `/api/files/search${q(p)}`),
  approvals: (status: "pending" | "all") => req<Approval[]>("GET", `/api/approvals${q({ status })}`),
  decide: (id: string, approve: boolean) => req<Approval>("POST", `/api/approvals/${enc(id)}/decide`, { approve }),
  audit: (limit = 100) => req<AuditEntry[]>("GET", `/api/audit${q({ limit })}`),
  tools: () => req<ToolInfo[]>("GET", "/api/tools"),
  settings: () => req<Settings>("GET", "/api/settings"),
  putSettings: (body: { trusted_tools?: string[]; default_project?: string; voice?: Partial<VoiceSettings> }) => req<Settings>("PUT", "/api/settings", body),
  kbSpaces: () => req<KbSpace[]>("GET", "/api/knowledge/spaces"),
  kbDocuments: (space?: string) => req<KbDocument[]>("GET", `/api/knowledge/documents${q({ space })}`),
  kbIngest: (path: string, space: string) => req<Record<string, unknown>>("POST", "/api/knowledge/ingest", { path, space }),
  kbUpload: (file: File, space: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("space", space);
    return req<Record<string, unknown>>("POST", "/api/knowledge/upload", fd);
  },
  kbSearch: (query: string, space?: string, limit = 8) => req<{ query: string; results: KbResult[] }>("POST", "/api/knowledge/search", { query, space: space || null, limit }),
  kbDelete: (id: string) => req<unknown>("DELETE", `/api/knowledge/documents/${enc(id)}`),
  memory: () => req<MemoryItem[]>("GET", "/api/memory"),
  addMemory: (content: string, kind: string) => req<MemoryItem>("POST", "/api/memory", { content, kind }),
  deleteMemory: (id: string) => req<unknown>("DELETE", `/api/memory/${enc(id)}`),
  reports: () => req<Report[]>("GET", "/api/research/reports"),
  report: (name: string) => req<{ name: string; markdown: string }>("GET", `/api/research/reports/${enc(name)}`),
  sources: () => req<Source[]>("GET", "/api/research/sources"),
  researchTasks: () => req<ResearchTask[]>("GET", "/api/research/tasks"),
  codingOverview: (path = "") => req<RepoOverview>("GET", `/api/coding/overview${q({ path })}`),
  codingDiff: (path = "") => req<{ diff: string }>("GET", `/api/coding/diff${q({ path })}`),
  devTasks: () => req<{ tasks: DevTask[]; open: number }>("GET", "/api/dev-tasks"),
  addDevTask: (title: string, project: string, notes = "") => req<DevTask>("POST", "/api/dev-tasks", { title, project, notes }),
  updateDevTask: (id: string, status: string) => req<DevTask>("PATCH", `/api/dev-tasks/${enc(id)}`, { status }),
  deleteDevTask: (id: string) => req<unknown>("DELETE", `/api/dev-tasks/${enc(id)}`),
  workflows: () => req<Workflow[]>("GET", "/api/workflows"),
  createWorkflow: (body: { name: string; description: string; schedule: Schedule; steps: WorkflowStep[] }) => req<Workflow>("POST", "/api/workflows", body),
  runWorkflow: (id: string) => req<WorkflowRun>("POST", `/api/workflows/${enc(id)}/run`),
  toggleWorkflow: (id: string, enabled: boolean) => req<Workflow>("PATCH", `/api/workflows/${enc(id)}${q({ enabled })}`),
  deleteWorkflow: (id: string) => req<unknown>("DELETE", `/api/workflows/${enc(id)}`),
  workflowRuns: () => req<WorkflowRun[]>("GET", "/api/workflow-runs"),
  notifications: () => req<Notification[]>("GET", "/api/notifications"),
  markNotificationsRead: () => req<unknown>("POST", "/api/notifications/read"),
};
