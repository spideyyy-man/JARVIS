# JARVIS - project handoff

Read this first. It is the state of the project as handed over from the cloud session that built it,
updated after the first run on the owner's Windows laptop (2026-11-17).

## What this is

A local, voice-first personal AI assistant for one user. It understands requests, calls tools on the
user's computer under an explicit approval model, remembers context and runs automations.

- Owner's machine: **Windows laptop, no GPU, no hosted-model API key**. Local model via Ollama.
- Repository: `spideyyy-man/JARVIS` (private), branch `main`.
- Intended install location on the laptop: `D:\spideyy\Automation and Agents\Jarvis`.

## The single most important fact

Built and first tested on Linux. **The first Windows run was done on 2026-11-17 on the owner's laptop**
(see "Windows results" below): the installer, doctor, app and a real local model now work there.
Still do not assume the Windows-only parts that were not exercised work (list under "Still untested").

The laptop: Windows 11 Pro, 16 GB RAM (often under 3 GB free - other heavy apps run alongside), no GPU,
1366x768 screen, Python 3.13 (system install, the only one), Ollama 0.40.16, model `qwen2.5:3b` built as
`jarvis-model` (num_ctx 8192). JARVIS is installed in this folder; `.venv` and `.env` exist; a desktop
shortcut "JARVIS" starts `scripts\windows\start.bat`.

## Everyday commands (Windows)

```powershell
# from the project folder
backend\.venv\Scripts\python.exe -m jarvis.doctor      # what works on THIS machine; prints a fix per failure
scripts\windows\start.bat                              # start (tray icon + app window); or the desktop shortcut
cd backend; .venv\Scripts\python.exe -m pytest -q      # tests (3 GUI tests skip: they need a Linux display)
.\Install-JARVIS.bat                                   # full (re)install; safe to re-run, keeps an existing .env
```

The doctor's `Model` line matters most: it asks the model to call a tool and reports whether it can
and how long it took. The installer also saves its output to `doctor-report.txt` (git-ignored).

Only one JARVIS may run at a time (port 8765). To restart it, use the tray menu's Quit, or stop the
`python.exe -m jarvis.desktop_app` processes and the `msedge.exe` processes whose command line contains
`.jarvis\ui-profile` - never the user's other Edge windows.

## Windows results (2026-11-17)

Fixed in commits `5abb543` and `80f0eb7` (each fix has a test):

- `.env` loader kept inline `# comments` as part of values, so the shipped `.env.example` crashed startup;
  blank values (`KEY=`) now mean "use the default" instead of "empty" (blank `JARVIS_ALLOWED_ROOTS` had
  allowed nothing). `tests/test_config.py` loads `.env.example` as-is.
- `localhost` costs ~2 s per connection on Windows (tries `::1` first; Ollama listens on IPv4 only). The
  installer's Ollama check always timed out. Installer, setup, docs and doctor now use `127.0.0.1`.
- Installer printed "Done" after the doctor crashed; it now stops with the error.
- Doctor said "is the model server running?" when Ollama actually failed for lack of memory; it now says so.
- `system_stats` took 15-20 s (psutil status/create_time/memory per process cost 2-3 s each over ~500
  Windows processes); costly fields are now read only for the rows shown (~3 s). "System Idle Process"
  (pid 0, idle time shown as 600 %+ CPU) is excluded.
- `type_text` hint word "write" made "write me a poem" shortlist keyboard typing first, and the 3B model
  called it. Removed; regression test in `test_robust.py`.
- App window was a fixed 1360x900 and ran 23 px off the bottom of the 1366x768 screen; it is now sized to
  the work area and centred.
- New `JARVIS_BLOCKED_PATHS`: folders inside an allowed root that JARVIS must never read, list, search or
  size. Tests that assumed Linux (symlinks need admin on Windows - a junction test was added instead,
  `/` path splitting, a scheduler run finishing within 5 s) were made platform-independent.

Seen working on Windows: installer end to end (with `-NoLaunch`), doctor 14 ok / 0 failures (screen OCR
3.3 s, screen capture, mouse/keyboard, clipboard, Edge via Playwright, live DuckDuckGo search), the app
server, the Edge app window opening on Command Center, `Ctrl+Alt+J` registered (checked by trying to
register it from another process), the tray icon's window created, PowerShell clipboard read, the fast
path (system stats, process list, clipboard, folder listing in 0.2-8 s), a directory-junction escape
blocked by PathGuard, and the real model: doctor tool-call check 3.9 s; "capital of France" 17 s;
a poem 36 s (it wasted 3 planning-tool calls first); first request after a cold load ~50 s.

Languages (2026-11-18): Settings > Voice > "Speech language" sets speech recognition (`hi-IN`, `ta-IN`, ...;
empty = Edge's own language) and, with no voice chosen, picks a voice for that language. Wake-word matching
(`frontend/src/wake.ts`, test `frontend/e2e/wake.test.mjs`) is Unicode-aware - the old matcher kept only a-z,
so Tamil/Hindi speech normalised to "" - and accepts "Jarvis" written in the language's script. The prompt
asks the model to reply in the user's language. Measured: `qwen2.5:3b` does reply in Hindi/Tamil, but got
"capital of India" wrong in both (84-118 s each). Treat non-English answers from the 3B model as unreliable.
The rule engine (fast path) is English-only.

Fixed at the same time: in flat mode tool results are cut to 3000 chars for the local model; a longer result
(system_stats with long Windows command lines) no longer crashes the rule engine's summary after a failover -
the untrimmed result is kept as `full` on the tool-result entry for the engine.

Owner's `.env` (not committed): `JARVIS_ALLOWED_ROOTS=C:\Users\GOD;D:\spideyy` and
`JARVIS_BLOCKED_PATHS=D:\spideyy\.account` (private account storage - keep it blocked).

Still untested on Windows: the tray icon's appearance and its menu (the owner declined screen access, so
nothing was seen on screen), pressing `Ctrl+Alt+J`, autostart, volume/brightness/media keys, window
listing/switching, voice (wake word, microphone - including real Tamil/Hindi speech through Edge), the 3 GUI tests, and a plain double-click of
`Install-JARVIS.bat` after the fixes (only `install.ps1 -NoLaunch` was run, before them).

Untested against anything real: the Anthropic provider, real IMAP/SMTP, real calendar feeds, real
speech/image model servers (stand-ins in `frontend/e2e/stub_*.py` and mocks only).

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
  tests/            175 tests (3 need a display + Chromium: xvfb-run on Linux, skipped on Windows)
frontend/           React + TS (Vite). Built UI is committed in frontend/dist so Windows needs no Node.js
frontend/e2e/       smoke.mjs (UI), model_loop.mjs (model in the loop), local_models.mjs, stub servers,
                    wake.test.mjs (wake-word unit test: `node frontend/e2e/wake.test.mjs`, no install needed)
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
- **File scope**: `PathGuard` (security.py) allows only `JARVIS_ALLOWED_ROOTS` (default: home), refuses
  credential files anywhere, and refuses `JARVIS_BLOCKED_PATHS`. Any new code that walks folders must
  filter with `guard.is_sensitive()` (which includes blocked folders) - `files.walk` prunes them.
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
  On Windows: `cd backend; .venv\Scripts\python.exe -m pytest -q` (about 2-4 min on an idle laptop).
- Windows PowerShell 5.1 is what the laptop has: no `&&`, and `Set-Content` writes the ANSI code page
  (the `.env` loader handles both UTF-8 and ANSI).
- After UI changes: `cd frontend && npm run build`, and commit `frontend/dist` (`git add -f frontend/dist`).
- Commits: do not put a model identifier in commit messages or code.

## Verified state

Linux (original handoff):
- 168 backend tests pass (97 behavioural + 71 routing cases), including real screen read / click /
  type and a multi-step agentic desktop task on a virtual display.
- Browser checks: 37/37 UI, 9/9 model-in-the-loop (stand-in model), 7/7 local speech and image models.
- Acceptance: 9 of 10 workflows pass; web research is not verified against the live internet.

Windows laptop (2026-11-17, Python 3.13):
- After `5abb543`: 168 passed, 3 skipped (2 min 4 s). Doctor: 14 ok, 3 optional warnings, 0 failures.
- With the language setting and failover fix (175 tests, 2026-11-18): **172 passed, 3 skipped, 0 failed**
  (7 min, ~1.8 GB free RAM); `node frontend/e2e/wake.test.mjs`: 6/6; UI type-check and build pass.
- Earlier, a full run under heavy memory pressure (0.4 GB free, 72 min) had timing-based failures in the
  failover tests: they pass on an unloaded machine. Expect timeouts when RAM is nearly exhausted.
- Frontend browser checks (`frontend/e2e/*.mjs`) and `scripts/acceptance.py` were not run on Windows.

## Known open items

1. Windows: finish the checks under "Still untested" above (tray menu, hotkey press, voice, media keys,
   window switching, autostart, a double-click install).
2. Real model: works, but slow (17-50 s per model reply on this CPU) and it over-uses tools on simple
   requests (three planning calls before a two-line poem). Try it on real one- and two-step tasks and tune
   prompts/hints from what goes wrong; don't expect long chains from a 3B model. In Hindi/Tamil it answers
   in the right language but with wrong facts; `qwen2.5:7b` is the next thing to try (needs ~5 GB free RAM).
3. Memory: with other apps open the laptop can drop under 2 GB free; Ollama then fails to load the model
   ("out-of-memory") and JARVIS falls back to the rule engine. A smaller model (`qwen2.5:1.5b`) is an
   option if this keeps happening.
4. Tool shortlist is lexical (hand-written hints in `robust.TOOL_HINTS`). A neural embedder
   (`JARVIS_EMBEDDER=openai:nomic-embed-text`) is supported but untested.
5. Flat mode gives up permission separation between agents; approvals and the untrusted-content
   warning still apply.
6. `mouse_scroll` and `focus_window` have no end-to-end test.
7. Not built: contacts, writing to an external calendar, phone access, a packaged installer.
8. `docs/GAP_ANALYSIS.md` and `docs/WINDOWS.md` still describe Windows as never run; update them from
   this file.

See `docs/GAP_ANALYSIS.md` for the full gap table with the test behind each item.
