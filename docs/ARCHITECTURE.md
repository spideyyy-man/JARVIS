# JARVIS architecture

## 1. Stack decisions

| Concern | Choice | Why | Rejected / deferred |
|---|---|---|---|
| Orchestration, system access | Python + FastAPI (asyncio) | Best ecosystem for LLM SDKs, `psutil`, document parsing; one async process holds agents, scheduler and monitor | Node backend: weaker system/doc tooling |
| UI | React + TypeScript (Vite), served as static files by the backend | One origin, no CORS, one process to run | Electron: +150 MB and a second runtime for no capability the browser lacks today |
| Desktop shell | Deferred. The UI is a local web app | A Tauri wrapper (tray icon, global hotkey, autostart) is a thin later addition because the UI only talks to `localhost` | Shipping an untested native shell now |
| Realtime | One WebSocket (`/ws`) fed by an in-process event bus | Token streaming, live metrics, agent/approval events share one channel | SSE (one-way; the voice UI also reports mic state) |
| Structured data | SQLite (WAL) | Single user, zero administration, FTS5 built in | PostgreSQL: operational cost with no benefit at this scale |
| Vector search | float32 vectors in SQLite + brute-force cosine (numpy), fused with FTS5 BM25 by reciprocal rank | Milliseconds up to ~10^5 chunks, no extra service | ChromaDB: heavy dependency tree for the same result at personal scale. Swap in when the corpus outgrows brute force |
| Embeddings | Pluggable. Default: dependency-free hashing embedder (lexical) | Works offline with no model download | Neural embeddings: supported through any OpenAI-compatible `/embeddings` endpoint (`JARVIS_EMBEDDER=openai:<model>`) |
| LLM | Provider interface + failover router: Anthropic (official SDK), OpenAI-compatible (local models), offline engine | No lock-in; degrades instead of failing | - |
| Speech | Browser Web Speech API by default; optional server-side STT/TTS over the OpenAI audio protocol | No audio leaves the UI process by default; no model download | Bundled Whisper/Piper: large downloads, platform-specific audio |

## 2. Components

```
Browser UI (React)  --HTTP /api, WS /ws-->  FastAPI (api.py)
   voice: STT / wake word / TTS                 |
                                            Jarvis (app.py): conversation loop, context, memory
                                                |
                    AgentRuntime (agents.py) -- LLMRouter (llm/): anthropic -> openai_compat -> offline
                                                |
                    ToolExecutor (tools/registry.py)   <- the single choke point
                      validate -> permission decision -> run -> redact -> audit
                                                |
   system | files | coding/git | research | knowledge | automation tools
                                                |
        SQLite: conversations, tasks, agent_messages, approvals, audit_log, kb_*, memories,
                workflows, workflow_runs, sources, dev_tasks, notifications, settings
   Background: Monitor (metrics + alerts), AutomationEngine (scheduler)
```

## 3. Agents

| Agent | Responsibility | Tools (level) |
|---|---|---|
| Orchestrator | Answer vs. act, decompose, delegate, merge | delegate, create_plan, memory, tasks |
| Research | Web research, GitHub evaluation, sourced reports | web_search, fetch_url, github_repo_info, list_sources, save_report (all READ) |
| Coding | Inspect, explain, edit, test, git | repo_overview, search_code, git_* (READ); write/replace, run_tests, run_command, git_commit (CONFIRM) |
| Computer | Files, folders, applications | list/search/read (READ); organize, move, copy, delete, open app (CONFIRM) |
| Knowledge | Index and answer from documents with citations | kb_ingest, kb_search, notes, conversation search |
| Automation | Scheduled and conditional workflows | create_workflow (CONFIRM), run, logs |
| Monitoring | Health and performance | system_stats, processes, disk, history |

Each agent only receives its own tools, and the runtime rejects calls to anything else. This is the
main defence against prompt injection: the research agent, which reads untrusted web pages, has no
tool that can modify anything.

Communication is structured: every delegation writes `request` / `result` / `error` rows to
`agent_messages` (sender, recipient, task id, payload) and the shared state lives in `tasks`
(status, plan, result, self-evaluation, parent task).

Self-evaluation is deliberately mechanical: failed tool calls, empty answers, and - for research -
any cited URL that was never retrieved are flagged on the task and appended to the answer.

## 4. Security

- **Three levels** enforced in `ToolExecutor.call`, not in prompts. CONFIRM calls create an approval
  row with a human-readable preview and return `pending_approval` to the model; nothing runs until
  `decide()` is called from the UI or by the user's own words ("approve"). Approvals are claimed
  atomically, so a double click cannot run an action twice.
- **No tool can approve, change trust settings or delegate inside a workflow.** Text approval only
  works for a human chat turn with exactly one pending action in that conversation.
- **Trusted allowlist** only accepts tools flagged `trust_eligible` in code.
- **Workflows get no extra privilege**: a CONFIRM step pauses the run until approved, then resumes.
- **Path guard**: all paths are resolved (symlinks included) and must sit inside
  `JARVIS_ALLOWED_ROOTS`; credential locations and key files are unreadable; JARVIS will not write to
  its own data directory or inside `.git`.
- **Deletes and overwrites are reversible**: the previous version goes to `~/.jarvis/trash`.
- **Commands**: no shell (no pipes/injection), administrator executables refused, git runs with hooks,
  fsmonitor and external diff disabled so inspecting a hostile repository cannot execute its config.
- **Secrets**: only from environment or OS keyring; redacted from tool output and the audit log.
- **Network**: API binds to loopback, requires a per-install token (HttpOnly, SameSite=Strict cookie),
  checks the Host header (DNS rebinding). `fetch_url` refuses private, loopback and metadata addresses
  on every redirect hop (SSRF).
- **Audit**: every call records actor, tool, level, decision, arguments and outcome.

## 5. Reliability

- Provider failover with retry/backoff and a circuit breaker; the offline engine is always last.
- Tool errors return to the model as error results so it can take another route; a step limit stops loops.
- Tool timeouts; the scheduler survives a failing workflow; workflow steps have retries with backoff.
- A turn can be interrupted (barge-in); the partial reply is kept and marked.

## 6. Limits and dependencies

| Item | Status | What is needed |
|---|---|---|
| Open-ended reasoning and conversation | Needs a model. Offline engine covers a fixed set of request shapes | `ANTHROPIC_API_KEY`, or a local OpenAI-compatible model |
| Anthropic provider | Implemented against the SDK docs; adapter logic unit-tested; **not run against the live API here** | An API key, then one manual conversation |
| Microphone / speaker | Works through the browser; cannot be exercised headlessly | Chrome or Edge, mic permission |
| Wake word | Browser recognition running continuously; audio goes to the browser vendor's speech service | For fully local wake word: a server-side STT provider |
| Neural semantic search | Default embedder is lexical | An embeddings endpoint |
| Web research | Needs outbound access to the search engine | Network access to `html.duckduckgo.com` / `en.wikipedia.org` |
| Desktop app (tray, hotkey, autostart) | Python launcher + Edge app window; untested on Windows | First run on a Windows machine |
| Screen reading, mouse/keyboard, browser control | Built; tested on a Linux virtual display. CPU text recognition, so only text-labelled targets can be clicked | Capabilities switched on in Settings |
| Email, calendar, purchases | Not built, by design | Would be CONFIRM-level tools |
| Multi-user | Not supported; single local user | - |
