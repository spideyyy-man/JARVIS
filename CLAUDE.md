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
1366x768 screen, Python 3.13 (system install, the only one), Ollama 0.40.16, model
`qwen3:4b-instruct-2507-q4_K_M` built as `jarvis-model` (num_ctx 8192; since 2026-11-19 - the previous
`qwen2.5:3b` build is kept as `jarvis-model-qwen2.5`), plus the NLLB translation model in
`~/.jarvis/models/nllb-200-distilled-600M-ct2-int8`. JARVIS is installed in this folder; `.venv` and `.env` exist; a desktop
shortcut "JARVIS" starts `scripts\windows\start.bat`.

## Everyday commands (Windows)

```powershell
# from the project folder
backend\.venv\Scripts\python.exe -m jarvis.doctor      # what works on THIS machine; prints a fix per failure
scripts\windows\start.bat                              # start in the background (tray + minimised window); or the desktop shortcut
cd backend; .venv\Scripts\python.exe -m pytest -q      # tests (3 GUI tests skip: they need a Linux display)
.\Install-JARVIS.bat                                   # full (re)install; safe to re-run, keeps an existing .env
```

The doctor's `Model` line matters most: it asks the model to call a tool and reports whether it can
and how long it took. The installer also saves its output to `doctor-report.txt` (git-ignored).

Only one JARVIS may run at a time (port 8765); starting it again just brings the running one's window up.
To restart it, use the tray menu's Quit (it also closes the window), or stop the
`python.exe -m jarvis.desktop_app` processes and the `msedge.exe` processes whose command line contains
`.jarvis\ui-profile` - never the user's other Edge windows.

## How the owner uses it (2026-11-18)

JARVIS is a background assistant, not a dashboard. At start one compact window (460 px wide, docked
bottom-right) opens and is minimised to the taskbar; the page keeps listening for the wake word (Edge is
started with background-throttling off and autoplay allowed so it can listen and speak while minimised).
`Ctrl+Alt+J` or the tray's "Talk to JARVIS" brings that same window back and starts listening without the
wake word (`POST /api/ui/listen` -> `ui.listen` event). There is only ever one window: each window runs its
own recogniser, so two would both answer. The UI is one Assistant screen + Settings; "Show all pages" in
Settings (local storage) restores the full workspace (Command Center, Agents, Coding, ...).

Since 2026-11-19 (Edge's online recogniser failed on the laptop with "Speech recognition error: network"):
- **Offline listener** (`listen.py`, started by the desktop app only - `settings.listen_on_start`): microphone
  (sounddevice) -> openWakeWord "hey_jarvis" (the phrase is now **"Hey Jarvis"**) -> energy endpointing ->
  Whisper `small` int8 (faster-whisper, `JARVIS_STT_LOCAL_MODEL`, models in `~/.jarvis/models/whisper`) with the
  language picked between English and Tamil automatically. `VoiceService.handle` emits `voice.heard` and runs the
  chat turn; the page adopts that conversation and speaks the reply (it still does all speaking). The page's
  own recogniser stays off while the listener runs. Voice setting `engine`: `local` (default) | `browser`.
  The hotkey/tray call `voice.listen_now()` directly - no window pops up.
- **Faster hearing, spoken by the server** (2026-11-20; the owner asked 3-4 questions and heard nothing - JARVIS had
  answered in text, but the minimised window's page had lost its connection, and only the page spoke):
  Whisper `base` (`JARVIS_STT_FAST_MODEL`) detects the language and writes English (~1.8-2.5 s); `small`
  (`JARVIS_STT_LOCAL_MODEL`) is loaded only for Tamil text and dropped after 15 idle min. `speak.py` says replies
  to spoken requests sentence by sentence as they stream, with Windows' offline voices (WinRT
  `Windows.Media.SpeechSynthesis` -> WAV -> winsound; first audio ~0.2 s after the text). No Tamil voice is
  installed in Windows here, so Tamil replies go to the window (`voice.say` / `voice.heard.server_speaks`).
  "Hey Jarvis" or the hotkey stops it talking and plays a short chime (`Speaker.chime`; the listener skips the
  first 0.24 s so the chime is not taken for speech). One request took 912 s to transcribe while the CPU was full
  (cause not pinned down; standalone runs under the same load took 8-50 s), so: a request heard in over 20 s is
  not acted on (`STALE_AFTER`, spoken apology), the desktop app runs at above-normal priority, the fast model is
  really warmed at start (first request 1.3 s), and each request's `seconds`/`fast_seconds`/`model` are shown. `desktop_app.window_watchdog` reopens the window when it is open
  but its page has been disconnected for a minute. The speaker is created only by `VoiceService.start` (desktop
  app), so tests never play sound.
- **Flaw fixes from the owner's real session** (2026-11-20, all from the request log):
  - Hearing: free language detection heard accented English as Malay/Indonesian (29% word errors on 28 Indian-English
    laptop-mic test clips). Now base decides only en-vs-ta (28/28 + 12/12), English goes to `base.en` with JARVIS's
    vocabulary as context (`listen.VOCABULARY`, 2.3% errors, ~1 s; `JARVIS_STT_ENGLISH_MODEL`), Tamil to `small`.
    The last 30 utterances are kept in `~/.jarvis/voice-samples` (voice setting `keep_samples`) to tune on the real voice.
  - Acting: the flat prompt now says Desktop for unnamed folders (`agents.desktop_folder`, OneDrive-aware), web_search for
    organisations/portals, no promises; plus one deterministic nudge per turn when a local model promises without a tool
    call (`PROMISE`) or claims "no access" without searching (`NO_ACCESS`).
  - `open_url` (new, CONFIRM, trust-eligible): websites open in the user's own browser; rules route "open x.com" there.
    `browser_open` stays for reading pages / local files. `open_application` finds Store apps (`Get-StartApps`, cached),
    strips "which is already open"-style tails (`clean_app_name`), and switches to an already-open window.
  - A new user message cancels the answer still running in that conversation (no more two contradicting replies).
- **Floating icon** (`overlay.py`, tkinter, its own `pythonw` process started by the desktop app with
  `--parent PID`): a 46 px always-on-top circle, coloured by state; hover (or click to pin) shows a panel from
  `GET /api/overlay` - now/heard/last exchange, approvals with Approve/Deny, activity (`EventBus.activity`,
  `events.describe`), a command box, Talk, Window, Self-test. Drag to move (`~/.jarvis/overlay.json`).
- **Self-test** (`selftest.py`, `POST /api/selftest`): real chat turns in a throw-away
  `~/JARVIS-selftest-<time>` folder - create an HTML page with a script, open it in JARVIS's browser
  (`browser_open` now accepts local files inside the allowed roots) and check it shows 42, delete the folder.
  It approves only the step's expected tools on paths inside its folder (audit actor `selftest`) and fails a
  step when JARVIS tries anything else. The browser step needs the `browser_control` capability (the panel has
  a button for the user; nothing turns it on by itself). Results: `~/.jarvis/selftest/last.json`.

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
The rule engine (fast path) understands English and, since 2026-11-18, common Tamil commands (below).

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
  listen.py         offline listener (wake word + Whisper) and VoiceService;  overlay.py  floating icon (tkinter)
  selftest.py       end-to-end self-test (create file -> run in browser -> delete) started from the icon
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
- **Local model speed = prompt caching.** On this CPU, reading ~1200 prompt tokens takes ~20 s; generating a
  short answer takes 1-2 s. Ollama reuses the processed prompt when a request starts with the same text, so in
  flat mode the system prompt (`agents.flat_system`) and the first tools (`agents.FLAT_CORE`, fixed order) must
  never change between requests. Anything per-request (time, recalled memories, summary) goes in a
  `<context>` block in front of the user's words (`offline.with_context`; the rule engine strips it). Extra
  tools are appended after the core only when the request shares words with them. Never put the time, a
  shortlist that varies, or recalled memories into the system prompt - it silently brings back the 20 s.
  `app._keep_model_warm` preloads that prefix at start and every 45 min (`keep_alive` 2h,
  `JARVIS_OPENAI_KEEP_ALIVE`); quitting unloads the model.
- **Instant replies**: time, date, hello, thanks, "who are you" (English and Tamil) are answered by
  `offline.fast_talk` without the model. Tamil commands (`offline.TA_RULES`, only fire on Tamil script) cover
  volume, media, brightness, lock, open app/site, system status, screen/clipboard, reminders, web search;
  Tamil requests get Tamil replies (`TA_RENDERERS`). "சரி"/"வேண்டாம்" approve/deny a pending action.
- **Tamil through English** (`app._run_via_english`, `translate.py`): a Tamil (or other Indian-script) request
  that needs the local model is read into English by the chat model (same cached prefix, ~5-8 s), answered in
  English with all tools, and the answer is written in Tamil by NLLB-200 600M (CTranslate2 int8, ~1 s, ~680 MB
  while loaded, unloads after 10 idle min). Measured: Qwen3 4B reads Tamil 10/10 (qwen2.5:3b 4/10) but writes it
  badly; NLLB writes it well but misread "Who wrote the Thirukkural?" as "Who wrote the crime?". Only the Tamil
  reply is streamed/spoken; `meta.via_english` keeps the English request and answer. Off with
  `JARVIS_TRANSLATE_VIA_ENGLISH=0`; without the model files it is skipped. `python -m jarvis.translate` downloads
  them (setup.ps1 does). NLLB's licence is CC-BY-NC (personal use).
- **Auto speech language** (`frontend/src/lang.ts`, default `language: "auto"`): browsers cannot detect the
  spoken language, so JARVIS listens in the language last used (en-IN/ta-IN, remembered), switches on "Jarvis,
  Tamil" / "Vanakkam Jarvis" / "Jarvis, English" / "இங்கிலீஷ்ல பேசு", on Latin-only text from the Tamil
  recogniser, and on what is typed. Replies are spoken with a voice for the language they are written in.
  Test: `node frontend/e2e/lang.test.mjs`.
- **Local-model replies are capped** (`JARVIS_OPENAI_MAX_TOKENS`, default 1024): one Tamil answer once ran 15+ min.
  `<think>` text is hidden while streaming even when the tag arrives in pieces (`providers.visible_text`).

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
- Background assistant, prompt caching, Tamil commands (2026-11-18): 229 passed, 3 skipped before the last
  tool-selection fix; the affected modules (156 tests) pass after it. UI checked in a 460x704 view against a
  throwaway instance: English/Tamil instant replies, Tamil status reply, inline approval card, barge-in.
  Real laptop (CPU at 100% from other apps, <1 GB free): model preload at start 128 s (background); "capital of
  France" 7-14 s with 1054-1133 of 1140 prompt tokens cached (1.1 s on a quiet machine, was 17-21 s);
  time/date/hello in English and Tamil 0.1-0.4 s; Tamil "is my computer OK" -> Tamil reply in 26 s (system_stats
  itself is slow under load). Tamil open questions from the 3B model are still wrong ("capital of India" -> nonsense).
- Qwen3 4B + Tamil through English + auto speech language (2026-11-19): 236 passed, 3 skipped (then the Tamil
  midnight time-of-day fix; its file passes 64/64); `node frontend/e2e/lang.test.mjs` 6/6, wake 6/6; Auto switching
  checked in a 460x704 view against a throwaway instance (typed "Jarvis, Tamil" -> Tamil, English text -> back).
- Offline listener, floating icon, self-test (2026-11-20): 254 passed, 3 skipped, 4 failed in a 22-min run while the
  CPU was at 100% (all four were `system_stats` 60 s timeouts; they pass when re-run: 78/78 with the routing table).
  Measured: wake word 1.00 on "Hey Jarvis", 0.00 on other speech; Whisper small picked the language on 6/6 Tamil and
  3/3 English clips (Tamil character error ~10%, base: ~33%); 4-16 s per request on this loaded CPU. The full
  pipeline (fake microphone playing "Hey Jarvis. What time is it?") produced "What time is it?". The real self-test
  on a throwaway instance with the real model passed 3/3 (create 244 s, browser "Result: 42" 37 s, delete 2 s) after
  fixing what it found: the flat agent was never offered write_file/delete_path (`agents.file_action_tools`), the
  continuation after an approval lost the file tools (shortlist from the original request), two decisions made
  together continued the task twice (`_decisions`/`_continued`), and "open file:///... in your browser" went to
  open_application. On the real JARVIS: listener running, real microphone delivering audio, icon window visible.
  Not checked: real speech into the microphone, the icon's look and hover (no screen access), Tamil speech.
- Earlier, a full run under heavy memory pressure (0.4 GB free, 72 min) had timing-based failures in the
  failover tests: they pass on an unloaded machine. Expect timeouts when RAM is nearly exhausted.
- Frontend browser checks (`frontend/e2e/*.mjs`) and `scripts/acceptance.py` were not run on Windows.

## Known open items

1. Windows: finish the checks under "Still untested" above (tray menu, hotkey press, voice, media keys,
   window switching, autostart, a double-click install). Voice is now the offline listener: real "Hey Jarvis" and
   Tamil speech through the laptop microphone are untested, and so is the floating icon's appearance.
   Speech is slow while other apps hold the CPU (Whisper small 4-16 s); `JARVIS_STT_LOCAL_MODEL=base` is smaller but was
   no faster under that load, and its Tamil was poor (3/6 languages right). Replies are still spoken by the page (Edge voices; no Tamil voice
   is installed in Windows here - Settings > Time & language > Speech can add one).
2. Real model: with prompt caching a short answer is 1-3 s on a quiet machine, 7-20 s when other apps load
   the CPU (Qwen3 4B is ~30-50% slower than qwen2.5:3b and uses ~3.7 GB instead of 2.3 GB). Tamil open questions
   now answer correctly through English (2026-11-19, real JARVIS: capital of India, Thiruvalluvar, sun rises in the
   east, a rain poem - 14-30 s each with the CPU loaded). Models tried and rejected: qwen3.5:2b (Tamil 0/6, thinking
   could not be turned off, called web_search for a poem); a "think in English" prompt rule (unreliable, and it made
   qwen2.5:3b worse); Edge's on-device Translator API (present in Edge 157 but "unavailable" for every pair on this
   laptop). Still untested: real speech through the microphone (wake word while minimised, Tamil recognition by
   Edge, auto switching by voice), whether Edge has a Tamil voice here, the tray/hotkey bringing the window back.
3. Memory: with other apps open the laptop can drop under 2 GB free; Ollama then fails to load the model
   ("out-of-memory") and JARVIS falls back to the rule engine. Since the switch to Qwen3 4B, JARVIS needs ~3.7 GB
   for the model plus ~0.7 GB for the translator during Tamil replies. To go back: `ollama cp jarvis-model-qwen2.5
   jarvis-model` (English stays fine; Tamil understanding drops to 4/10).
4. Tool shortlist is lexical (hand-written hints in `robust.TOOL_HINTS`). A neural embedder
   (`JARVIS_EMBEDDER=openai:nomic-embed-text`) is supported but untested.
5. Flat mode gives up permission separation between agents; approvals and the untrusted-content
   warning still apply.
6. `mouse_scroll` and `focus_window` have no end-to-end test.
7. Not built: contacts, writing to an external calendar, phone access, a packaged installer.
8. `docs/GAP_ANALYSIS.md` and `docs/WINDOWS.md` still describe Windows as never run; update them from
   this file.

See `docs/GAP_ANALYSIS.md` for the full gap table with the test behind each item.
