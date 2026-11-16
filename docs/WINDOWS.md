# JARVIS on a Windows laptop (no GPU, no API key)

Everything below was written and tested on Linux. The Windows-specific parts (tray icon, global hotkey,
autostart, volume/brightness keys, PowerShell clipboard, Edge app window) have **not been run on Windows
yet** - expect to hit and report a few first-run issues.

## 1. Install

Prerequisites: [Python 3.11 or 3.12, 64-bit](https://www.python.org/downloads/) (tick "Add to PATH") and
[Ollama](https://ollama.com/download) for the language model. Node.js is not needed; the built UI ships in
`frontend/dist`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
```

This creates the Python environment, installs dependencies, writes `.env` pointing at Ollama, and downloads
the model. Then:

```powershell
scripts\windows\start.bat
```

A JARVIS window opens, with a tray icon. `Ctrl+Alt+J` opens the Voice screen from anywhere. To start with
Windows:

```powershell
backend\.venv\Scripts\python.exe -m jarvis.desktop_app --install-autostart
```

Then check what actually works on your machine:

```powershell
backend\.venv\Scripts\python.exe -m jarvis.doctor
```

It tests the model (including whether it can really call tools, and how long that takes), screen reading,
mouse/keyboard, browser, speech, calendar and email, and prints a fix for anything that fails. The same
report is in Settings > Diagnostics. **Send me this output if something does not work.**

## 2. Choosing a model without a GPU

| Model | Download | RAM needed | Speed on a laptop CPU | Use it when |
|---|---|---|---|---|
| `qwen2.5:3b` (default) | ~2 GB | 8 GB | usable | you want replies in seconds; expect tool-calling mistakes |
| `qwen2.5:7b` | ~4.7 GB | 16 GB | slow (often 10-40 s per step) | accuracy matters more than speed |
| `llama3.2:3b` | ~2 GB | 8 GB | usable | alternative if qwen misbehaves |

**Context window.** An agent's prompt carries tool definitions, so it needs more room than a chat prompt.
Ollama's default context can be as small as 2048-4096 tokens, which cuts the prompt off without any error
and makes the model behave erratically. The setup script therefore builds `jarvis-model` (your model with
`num_ctx 8192`) and points `.env` at it. To do it by hand: put `FROM qwen2.5:3b` and `PARAMETER num_ctx 8192`
in a file called `Modelfile`, then run `ollama create jarvis-model -f Modelfile`. JARVIS also keeps its own
prompt lean for local models (12 shortlisted tools, short descriptions, trimmed history and tool results).

The model must support tool calling; all three do. Change it with `JARVIS_OPENAI_MODEL` in `.env`.
Be realistic: a 3B model on CPU handles one- or two-step requests. Long multi-step jobs ("open the report,
find the total, paste it into the email") will often go wrong or stall. When the model is unreachable,
JARVIS falls back to its rule engine, which handles the request shapes listed in the README instantly.

## 3. What JARVIS can do on the laptop

| Ability | Example | Needs |
|---|---|---|
| Files, folders, apps, git, tests | "organize my downloads folder", "open notepad" | approval for changes |
| Timed reminders, spoken | "remind me in 20 minutes to call Sam" | Voice "speak replies" on |
| Daily spoken briefing | Settings > Voice > briefing time, or "brief me" | - |
| Clipboard | "what's on my clipboard", "copy ... to the clipboard" | approval to write |
| Volume, brightness, media, lock | "turn the volume up", "set brightness to 40%", "lock my laptop" | approval (can be pre-trusted) |
| Read the screen | "what's on my screen", "read the error on my screen" | Capability: screen access |
| Click and type in any app | "click Save", "type hello world", "press ctrl+s" | Capabilities: screen access + desktop control; approval each time |
| Drive a browser | "open example.org", then click / fill forms | Capability: browser control; approval for clicks and input |

### How screen control works without a GPU

JARVIS takes a screenshot and runs text recognition on the CPU (well under a second on the test machine).
It then clicks things **by the words on them**. Consequences:

- It cannot click icons or images that have no text; use `press ctrl+s`-style shortcuts or coordinates.
- It reads whatever is on screen, including anything private that is visible. Text that looks like a
  password or key is masked before the model sees it, but ordinary private content is not.
- The screen is re-read at the moment of the click, after you approve. If the label is gone, nothing is clicked.

### Multi-step tasks

- With a model configured, JARVIS keeps going after you approve a step instead of stopping.
- On an approval card, **"Approve all ... for this task"** stops it asking again for that one tool in that
  conversation for 30 minutes (say "approve all" by voice). Delete, run-command, git-commit and send-email
  always ask every time. Revoke in Settings.
- Simple commands ("turn the volume up") skip the model and run instantly.
- "Do that every day at 9" saves what JARVIS just did as a scheduled workflow. "Undo that" restores a delete.
- An approval that follows a web page, email, clipboard or screen read carries a warning: that content can
  contain planted instructions.

### Calendar and email (optional)

Set `JARVIS_CALENDAR_ICS` to your calendar's secret iCal link, and the `JARVIS_EMAIL_*` values in `.env`
(use an app password). Then: "what's on my calendar today", "schedule a meeting with Priya tomorrow at 3pm",
"check my email", "email sam@example.com saying ...". Sending always shows you the full message first.

## 4. Safety switches

- **Settings > Capabilities**: screen access, desktop control and browser control are all off until you
  switch them on. JARVIS has no tool that can switch them on.
- Every click, keystroke and form entry shows exactly what it will do and waits for "approve". These
  actions can never be added to the trusted list.
- The automated browser uses its own profile (not your logged-in Edge) and refuses to open JARVIS's own
  control page, so a model cannot approve its own requests.
- Everything is in the audit log (Settings).

## 5. Voice

In the JARVIS window, open Voice and switch on the wake word; allow the microphone once. Enable
"Listen for the wake word as soon as JARVIS opens" in Settings to keep it always on while the window is
open or minimised. By default speech recognition is Edge's built-in service (audio goes to Microsoft).
For fully local speech, see [LOCAL_MODELS.md](LOCAL_MODELS.md).

## 6. Not included

Contacts; writing to your external calendar (JARVIS keeps its own events); controlling JARVIS from a phone; a packaged installer (.exe/.msi).
