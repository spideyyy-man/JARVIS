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

## 2. Choosing a model without a GPU

| Model | Download | RAM needed | Speed on a laptop CPU | Use it when |
|---|---|---|---|---|
| `qwen2.5:3b` (default) | ~2 GB | 8 GB | usable | you want replies in seconds; expect tool-calling mistakes |
| `qwen2.5:7b` | ~4.7 GB | 16 GB | slow (often 10-40 s per step) | accuracy matters more than speed |
| `llama3.2:3b` | ~2 GB | 8 GB | usable | alternative if qwen misbehaves |

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

Email, calendar and contacts; controlling JARVIS from a phone; a packaged installer (.exe/.msi).
