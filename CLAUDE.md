# JARVIS - project handoff

Read this first. It is the state of the project as handed over from the cloud session that built it.

## What this is

A local, voice-first personal AI assistant for one user. It understands requests, calls tools on the
user's computer under an explicit approval model, remembers context and runs automations.

- Owner's machine: **Windows laptop, no GPU, no hosted-model API key**. Local model via Ollama.
- Repository: `spideyyy-man/JARVIS` (private), branch `main`.
- Intended install location on the laptop: `D:\spideyy\Automation and Agents\Jarvis`.

## The single most important fact

**Everything was built and tested on Linux. It has never been run on Windows and never with a real
language model.** The first job in this folder is to run the installer and the doctor, then fix what
breaks. Do not assume the Windows-specific code works.

Untested on Windows (most likely to need fixes): `Install-JARVIS.bat`, `scripts/windows/*.ps1` (never
even syntax-checked - no PowerShell was available), tray icon, `Ctrl+Alt+J` hotkey, autostart,
volume/brightness/media keys, PowerShell clipboard, window listing/switching, the Edge app window.

Untested against anything real: the Anthropic provider, the OpenAI-compatible provider against real
Ollama, live web search, real IMAP/SMTP, real calendar feeds, real speech/image model servers. These
were only exercised against stand-in servers in `frontend/e2e/stub_*.py` and mocks.

## First steps on the laptop

```powershell
# from the project folder
.\Install-JARVIS.bat                                   # Python + Ollama + deps + model + doctor + shortcut + launch
backend\.venv\Scripts\python.exe -m jarvis.doctor      # what works on THIS machine; prints a fix per failure
scripts\windows\start.bat                              # start (tray icon + app window)
```

The doctor's `Model` line matters most: it asks the model to call a tool and reports whether it can
and how long it took. Output is also saved to `doctor-report.txt` by the installer.

## Layout

```
backend/jarvis/
  app.py            Jarvis object: conversation loop, fast path, continuation, auto-memory, agent mode
  agents.py         7 specialist agents + flat "assistant"; the agent loop; delegate/plan tools
  robust.py         small-model survival: tool-call repair, loop guard, stream filter, tool shortlist
  tools/registry.py ToolExecutor - THE permission choke point (validate -> decide -> run -> redact -> audit)
  tools/            system, files, coding, research, desktop, browser, personal (calendar/email), images
  llm/              base (router, failover), providers (Anthropic, OpenAI-compatible), offline (rule engine)
  automation.py     workflows, scheduler, folder-watch triggers, Monitor, Proactive (reminders/briefing)
  knowledge.py      ingestion, hybrid retrieval, memory
  security.py       levels, PathGuard, redaction, audit log
  api.py            FastAPI REST + WebSocket;  desktop_app.py  tray/hotkey/window launcher;  doctor.py
  tests/            168 tests (run under xvfb-run on Linux; two need a display + Chromium)
frontend/           React + TS (Vite). Built UI is committed in frontend/dist so Windows needs no Node.js
frontend/e2e/       smoke.mjs (UI), model_loop.mjs (model in the loop), local_models.mjs, stub servers
scripts/windows/    install.ps1, setup.ps1, start.bat
docs/               ARCHITECTURE, GAP_ANALYSIS, WINDOWS, LOCAL_MODELS, ACCEPTANCE, E2E
```

## How it works (the parts that are easy to get wrong)

- **Three execution levels**, enforced in `ToolExecutor.call`, never in prompts. READ runs. CONFIRM is
  queued with a preview until the user approves. A CONFIRM tool marked `trust_eligible` can be
  allowlisted. Tools marked not `grantable` (delete, run_command, git_commit, email_send) always ask.
- **No tool may approve, change trust, or flip a capability.** Approval by text ("approve",
  "approve all") is handled in `app.chat` on the user's own words only (`actor == "chat"`).
- **Capabilities** (`screen_access`, `desktop_control`, `browser_control`) are off by default and only
  the user can switch them (Settings / `PUT /api/settings`).
- **Task grants**: "approve all X for this task" = one tool, one conversation, 30 minutes.
- **Continuation**: after an approval decision the task resumes automatically (real models only).
- **Agent modes**: `team` (orchestrator delegates; least privilege; 4+ model calls per action) and
  `flat` (one agent, shortlist of 12 tools, 2 calls). `JARVIS_AGENT_MODE=auto` picks flat for local
  models. The rule engine always uses team.
- **Fast path**: when a model is configured, simple recognisable commands run on the rule engine
  instantly. `NOT_FAST` in `app.py` lists tools that should still go to the model.
- **Rule engine** (`llm/offline.py`): ordered regex rules. Order matters - a new rule can steal an old
  phrase. `tests/test_routing.py` is the regression table; extend it whenever a rule is added.
- **No GPU**: the screen is read with CPU text recognition (rapidocr) and clicked by the words on it.
  Icons without text cannot be clicked.
- **Local model context**: Ollama's default context is small. `setup.ps1` builds `jarvis-model`
  (`num_ctx 8192`). Prompts are kept lean for `openai_compat` (short tool descriptions, 3000-char tool
  results, 5000-char history).

## Conventions

- Test what you change; never report something as working that was not run. Say what is untested.
- Every new tool goes through `ToolRegistry` with an honest level, and a `preview` if it is CONFIRM.
- Any tool that returns third-party content sets `untrusted_output = True`.
- Secrets come from environment or OS keyring only (`config.get_secret`); never the database or logs.
- Backend tests on Linux: `cd backend && xvfb-run -a .venv/bin/python -m pytest -q`.
- After UI changes: `cd frontend && npm run build`, and commit `frontend/dist` (`git add -f frontend/dist`).
- Commits: do not put a model identifier in commit messages or code.

## Verified state at handoff (Linux)

- 168 backend tests pass (97 behavioural + 71 routing cases), including real screen read / click /
  type and a multi-step agentic desktop task on a virtual display.
- Browser checks: 37/37 UI, 9/9 model-in-the-loop (stand-in model), 7/7 local speech and image models.
- Acceptance: 9 of 10 workflows pass; web research is not verified against the live internet.

## Known open items

1. First Windows run of the installer and the app; fix what breaks.
2. First run with a real model (expect a 3B CPU model to manage one- or two-step tasks, not long chains).
3. Tool shortlist is lexical (hand-written hints in `robust.TOOL_HINTS`). A neural embedder
   (`JARVIS_EMBEDDER=openai:nomic-embed-text`) is supported but untested.
4. Flat mode gives up permission separation between agents; approvals and the untrusted-content
   warning still apply.
5. `mouse_scroll` and `focus_window` have no end-to-end test.
6. Not built: contacts, writing to an external calendar, phone access, a packaged installer.

See `docs/GAP_ANALYSIS.md` for the full gap table with the test behind each item.
