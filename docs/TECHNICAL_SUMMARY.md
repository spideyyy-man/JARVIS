# JARVIS - technical summary (2026-11-21)

## What it is
- A local, voice-first personal assistant for one user on a Windows 11 laptop: no GPU, 16 GB RAM, no cloud API key.
- Everything runs on the laptop: speech in, thinking, tools, speech out. Only web search, weather and pages the user
  asks for go online.
- Understands English and Tamil; replies in the language it was spoken to in.
- Code: Python backend (`backend/jarvis`, ~11,400 lines) + React/TypeScript window (`frontend/src`, ~5,100 lines),
  ~3,700 lines of tests (224 test functions, ~420 cases with the routing tables). Repository: 132 files.

## How a spoken request flows
1. Microphone, 16 kHz, 80 ms frames (`listen.py`, sounddevice).
2. Wake: "Jarvis" by a streaming keyword spotter; "Hey Jarvis" also by openWakeWord. Barge-in stops JARVIS talking.
3. End of speech: Silero voice-activity detector (0.5 s pause; 0.75 s after the wake word).
4. Language check on every request (Whisper base): English or Tamil only.
5. Text: English by Whisper base.en (vocabulary prompt, beam 5); Tamil by Whisper small trained on Tamil.
6. Unclear speech (low confidence) is not acted on: "Sorry, I didn't catch that", in English or Tamil.
7. Instant path: ~110 ordered rules (`llm/offline.py`) answer common commands in 0.0-0.4 s without the model.
8. Otherwise the local language model plans and calls tools; Tamil requests go through English (NLLB writes Tamil).
9. The reply is spoken sentence by sentence as it streams; then a 5 s window takes a follow-up without the wake word.

## Models: what each does, size on disk, speed on this laptop
| Job | Model | Disk | Speed / notes |
|---|---|---|---|
| Thinking, tool use | Qwen3 4B Instruct 2507, q4_K_M, via Ollama as `jarvis-model` (8k context, 8 threads) | 2.5 GB | ~3.7 GB RAM; 1-3 s first word on a quiet CPU, 7-20 s when busy; prompt caching |
| Fallback model | qwen2.5:3b (`jarvis-model-qwen2.5`) | 1.9 GB | kept to switch back (`ollama cp`) |
| "Jarvis" wake word (personal) | logistic regression on openWakeWord speech embeddings, trained on the owner's own "Jarvis" ("learn my voice") | <1 MB + 176 MB training data | ~0.05 ms per frame; retrains itself from confirmed live calls |
| "Jarvis" wake word (generic) | sherpa-onnx keyword spotter, zipformer GigaSpeech 3.3M (int8); then a Whisper check of each utterance | 20 MB | spotter missed the owner's live calls (0/2); the Whisper check caught them in ~1 s |
| "Hey Jarvis" wake word | openWakeWord `hey_jarvis` | ~6 MB | per 80 ms frame |
| Speech detector | Silero VAD (ships with openWakeWord) | <2 MB | per frame |
| Language check | Whisper base (faster-whisper, int8) | 142 MB | ~0.3-1 s |
| English text | Whisper base.en (int8, beam 5, vocabulary) | 141 MB | ~1-2.5 s; 2.3% word errors on Indian-English test clips |
| Tamil text | Whisper small trained on Tamil (IIT Madras, `vasista22/whisper-tamil-small`, int8) | 242 MB | 2.0% character errors on Tamil clips (plain small: 9.9%); loaded when needed |
| English second opinion | Whisper small (int8) | 464 MB | only for unsure English; released after 15 idle minutes |
| English voice | Piper `en_US-lessac-medium` (ONNX) | 61 MB | ~10x real time, first audio ~0.5 s |
| Tamil voice | Meta MMS Tamil (VITS, ONNX) | 109 MB | ~3x real time; English words inside Tamil said by Piper |
| Optional voice | Kokoro v1.0 int8 (+ voices) | 116 MB | most natural, ~1x real time here; `JARVIS_LOCAL_TTS=kokoro` |
| Fallback voice | Windows WinRT voices | 0 (built in) | instant, robotic |
| Tamil writing | NLLB-200 distilled 600M (CTranslate2 int8) | 601 MB | ~1 s; ~680 MB RAM while loaded, unloads after 10 idle min |
| Screen reading | RapidOCR (ONNX, CPU) | in the venv | screen text in ~3 s; clicks by the words on screen |

## Storage
- `~/.jarvis` (models, database, profiles, samples): ~2.4 GB, of which models ~1.9 GB.
- Ollama models used by JARVIS: 2.5 GB (+1.9 GB for the qwen2.5 fallback). Not used and removable: `qwen3.5:2b-q4_K_M`
  (1.9 GB, rejected in testing) and `qwen3:0.6b` (0.5 GB).
- Database: SQLite `~/.jarvis/jarvis.db` (conversations, memories, tasks, approvals, audit log, settings; a few MB).
- Voice samples: the last 30 utterances in `~/.jarvis/voice-samples` (local only; voice setting `keep_samples`).
- RAM at work: model 3.7 GB + speech models ~0.5-1 GB + translator 0.7 GB during Tamil replies.

## Capabilities (about 80 tools, each through one permission choke point)
- Files: list, search, read, write, move, rename, organise, delete to a recoverable trash; folder scope enforced.
- Apps and system: open apps (incl. Store apps) and websites, switch windows, volume, brightness, media keys, lock,
  system status and processes, clipboard.
- Screen and desktop control: read the screen (OCR), click and type by the words on screen (off until the user enables it).
- Browser: its own Edge/Playwright browser to read and act on pages (off until enabled); websites open in the user's browser.
- Research: web search (DuckDuckGo), fetch pages, a local knowledge base with hybrid retrieval.
- Coding: repository overview, run tests, git status/commit (always asks), dev task list, plans.
- Everyday: timers, alarms, weather (Open-Meteo), maths, unit conversion, lists, play music (YouTube search),
  reminders, morning briefing, workflows on a schedule or on folder changes. All work in English and Tamil.
- Personal: long-term memory (learns stated facts), calendar feeds (read), email (IMAP/SMTP, not set up).
- Self-test: creates a page, opens it in the browser, checks it, deletes it.

## Safety model
- Three levels enforced in code (`tools/registry.py`), never in prompts: READ runs; CONFIRM waits for the user's
  approval with a preview; some actions (delete, run_command, git_commit, email_send) always ask.
- Capabilities (screen, desktop, browser) are off by default; only the user switches them. No tool can approve itself.
- Folder scope (`JARVIS_ALLOWED_ROOTS`), blocked folders (`JARVIS_BLOCKED_PATHS`), credential files refused anywhere.
- Content from the web is marked untrusted; secrets come only from the environment or the OS keyring; audit log.

## Interface
- A floating orb (Pillow-rendered, per-pixel alpha, ~30 fps) shows state, timers and approvals; hover for the panel.
- A compact window (Edge app mode, React) with the conversation, timers and quick-try chips; tray icon; `Ctrl+Alt+J`.
- REST + WebSocket API on 127.0.0.1:8765 with a local token.

## Measured performance (owner's laptop, often under heavy load from other apps)
- Wake: "Jarvis" is caught as it is said (~0.25 s after the word ends); the Whisper check it replaced took 15 s median.
- Instant commands (time, volume, timers, weather...): 0.0-0.4 s (weather ~2 s, it is online).
- English request heard: ~1-2.5 s after you stop speaking; Tamil: ~3-7 s.
- Model answer: ~4 s to the first spoken word in a conversation on a quiet CPU; much longer when the CPU is full.
- Speaking: first audio ~0.5 s (Piper), sentences back to back with 0.02-0.06 s gaps.
- Tests: 417 passed, 3 skipped (GUI tests need a Linux display), ~6 min.

## Main settings (`.env` or environment; blank = default)
- `JARVIS_ALLOWED_ROOTS`, `JARVIS_BLOCKED_PATHS` - what JARVIS may touch.
- `JARVIS_AGENT_MODE` (auto | flat | team), `JARVIS_OPENAI_MAX_TOKENS`, `JARVIS_OPENAI_KEEP_ALIVE` - the local model.
- `JARVIS_STT_ENGLISH_MODEL` (base.en), `JARVIS_STT_FAST_MODEL` (base), `JARVIS_STT_LOCAL_MODEL` (small),
  `JARVIS_STT_TAMIL_MODEL` (tamil-small) - speech recognition.
- `JARVIS_LOCAL_TTS` (piper | kokoro | windows), `JARVIS_TRANSLATE_VIA_ENGLISH` (1) - voice and Tamil replies.
- In the app: voice engine, speaking on/off, speed, keep samples, capabilities, fast path, auto memory.

## Key files
- `listen.py` hearing and conversation; `speak.py` voices; `app.py` the conversation loop; `agents.py` agents and
  prompts; `llm/offline.py` instant rules; `tools/registry.py` permissions; `security.py` scope, redaction, audit;
  `overlay.py` / `orb.py` the floating orb; `desktop_app.py` tray, hotkey and window; `doctor.py` health check.

## Known limits
- Speed depends on CPU load: with other heavy apps, transcription and answers take several times longer.
- The 4B model makes small-model mistakes; open questions are slower than cloud assistants.
- The Tamil voice says loanwords poorly; real Tamil speech through the microphone is still being checked.
- Not built: contacts, phone, writing to calendars, smart home, a packaged installer.
