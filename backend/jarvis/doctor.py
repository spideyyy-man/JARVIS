"""Diagnostics: checks, on THIS machine, every dependency JARVIS's features rely on.

    python -m jarvis.doctor            human-readable report (exit code 1 if anything failed)
    python -m jarvis.doctor --json     machine-readable

The most important check is the model probe: it asks your configured model to call a tool and
verifies that it really does, because a model that cannot call tools can talk but cannot act."""
from __future__ import annotations

import asyncio
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

OK, WARN, FAIL = "ok", "warn", "fail"


def _r(name: str, status: str, detail: str, fix: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail, **({"fix": fix} if fix and status != OK else {})}


async def probe_model(provider) -> dict:
    """Ask the model to use a tool. Returns latency and whether a correct tool call came back."""
    from .robust import repair_response

    tool = {"name": "add_numbers", "description": "Add two integers and return the sum.",
            "input_schema": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"], "additionalProperties": False}}
    start = time.time()
    resp = await asyncio.wait_for(provider.complete("You are a helpful assistant. Use tools when they are available.",
                                                    [{"role": "user", "content": "What is 17 plus 25? Use the add_numbers tool."}], [tool]), 180)
    seconds = round(time.time() - start, 1)
    native = bool(resp.tool_calls)
    resp, fixes = repair_response(resp, {"add_numbers": tool["input_schema"]})
    call = next((c for c in resp.tool_calls if c.name == "add_numbers"), None)
    correct = bool(call) and call.args.get("a") == 17 and call.args.get("b") == 25
    return {"seconds": seconds, "native_tool_call": native, "needed_repair": bool(fixes), "correct": correct, "text": (resp.text or "")[:120]}


async def diagnose(app, online: bool = True) -> list[dict]:
    from .tools import desktop

    s, out = app.settings, []
    py = sys.version_info
    out.append(_r("Python", OK if py >= (3, 11) else FAIL, f"{platform.python_version()} on {platform.system()} {platform.release()} ({platform.machine()})",
                  "Install Python 3.11 or 3.12 (64-bit)."))
    try:
        probe = s.data_dir / ".write-test"
        probe.write_text("x")
        probe.unlink()
        out.append(_r("Data directory", OK, str(s.data_dir)))
    except OSError as e:
        out.append(_r("Data directory", FAIL, f"{s.data_dir}: {e}", "Set JARVIS_DATA_DIR to a writable folder."))
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"
    out.append(_r("User interface", OK if dist.is_file() else FAIL, "built" if dist.is_file() else "frontend/dist is missing", "Run `npm install && npm run build` in frontend/."))

    # ---- language model
    chain = app.llm.providers
    if chain[0].name == "offline":
        out.append(_r("Language model", WARN, "None configured: JARVIS is running on the built-in rule engine (fixed request shapes, no reasoning).",
                      "Install Ollama, `ollama pull qwen2.5:3b`, and set JARVIS_LLM_PROVIDER=openai_compat, JARVIS_OPENAI_BASE_URL=http://localhost:11434/v1, JARVIS_OPENAI_MODEL=qwen2.5:3b."))
    for p in chain:
        if p.name == "offline":
            continue
        if p.name == "anthropic" and not online:
            out.append(_r("Model: anthropic", OK, f"{p.model} (key present; not called)"))
            continue
        try:
            res = await probe_model(p)
            speed = f"{res['seconds']} s for a one-tool request"
            if res["correct"] and not res["needed_repair"]:
                out.append(_r(f"Model: {p.name}", OK if res["seconds"] < 60 else WARN, f"{p.model} calls tools correctly. {speed}.",
                              "This model is slow on this machine; consider a smaller one (e.g. qwen2.5:3b)."))
            elif res["correct"]:
                out.append(_r(f"Model: {p.name}", WARN, f"{p.model} only produced a usable tool call after JARVIS repaired it ({speed}). Expect occasional mistakes.",
                              "A model with native tool calling (qwen2.5, llama3.1/3.2, mistral-nemo) will be more reliable."))
            else:
                out.append(_r(f"Model: {p.name}", FAIL, f"{p.model} did not call the tool when asked (it replied: {res['text']!r}). It can chat but cannot act.",
                              "Use a model that supports tool calling, e.g. `ollama pull qwen2.5:3b`."))
        except Exception as e:
            out.append(_r(f"Model: {p.name}", FAIL, f"{p.model}: {type(e).__name__}: {str(e)[:200]}",
                          "Is the model server running? For Ollama: start it and run `ollama list` to confirm the model name."))
    mode = app.root_agent()
    out.append(_r("Agent mode", OK, f"{'flat (single agent, fewer model calls)' if mode == 'assistant' else 'team (orchestrator + specialists)'}; "
                                    f"tool shortlist {s.max_tools}; embedder {app.embedder.name}"))

    # ---- desktop abilities
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (640, 160), "white")
        try:
            font = ImageFont.load_default(size=48)
        except TypeError:
            font = ImageFont.load_default()
        ImageDraw.Draw(img).text((20, 40), "JARVIS check 4821", fill="black", font=font)
        start = time.time()
        lines = await asyncio.to_thread(desktop.ocr_lines, img)
        text = " ".join(l["text"] for l in lines)
        good = "4821" in text
        out.append(_r("Screen text recognition", OK if good else WARN, f"read {text!r} in {time.time() - start:.1f} s" if lines else "ran but recognised nothing",
                      "Reinstall: pip install rapidocr-onnxruntime"))
    except Exception as e:
        out.append(_r("Screen text recognition", FAIL, f"{type(e).__name__}: {e}", 'Install desktop extras: pip install -e "backend[desktop]"'))
    try:
        shot = await asyncio.to_thread(app.capture_screen)
        out.append(_r("Screen capture", OK, f"{shot.size[0]}x{shot.size[1]}"))
    except Exception as e:
        out.append(_r("Screen capture", WARN, f"{type(e).__name__}: {str(e)[:120]}", "Needs a desktop session (not available over a headless connection)."))
    try:
        import pynput.keyboard  # noqa: F401
        import pynput.mouse  # noqa: F401

        out.append(_r("Mouse and keyboard control", OK, "available"))
    except Exception as e:
        out.append(_r("Mouse and keyboard control", WARN, f"{type(e).__name__}: {str(e)[:120]}", 'pip install -e "backend[desktop]" (needs a desktop session)'))
    try:
        desktop.clipboard_commands()
        out.append(_r("Clipboard", OK, "available"))
    except Exception as e:
        out.append(_r("Clipboard", WARN, str(e)))
    from .desktop_app import find_browser

    browser = find_browser()
    try:
        import playwright  # noqa: F401

        out.append(_r("Browser automation", OK if browser else WARN, f"Playwright installed; browser: {browser or 'none found'}", "Install Microsoft Edge or Google Chrome."))
    except ImportError:
        out.append(_r("Browser automation", WARN, "Playwright is not installed", 'pip install -e "backend[desktop]"'))
    caps = app.capabilities()
    out.append(_r("Capabilities", OK, ", ".join(f"{k.replace('_', ' ')}: {'ON' if v else 'off'}" for k, v in caps.items()) + " (change in Settings)"))

    # ---- services
    if app.stt or app.tts:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(s.voice_base_url.rstrip("/") + "/models")
            out.append(_r("Local speech server", OK if r.status_code < 500 else WARN, f"{s.voice_base_url} answered {r.status_code}"))
        except Exception as e:
            out.append(_r("Local speech server", FAIL, f"{s.voice_base_url}: {type(e).__name__}", "Start your speech server or set JARVIS_STT_PROVIDER/JARVIS_TTS_PROVIDER back to browser."))
    else:
        out.append(_r("Speech", OK, "using the browser's speech recognition and voices (Edge/Chrome)"))
    if s.image_provider != "none":
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(s.image_base_url)
            out.append(_r("Image generation", OK, f"{s.image_provider} at {s.image_base_url} answered {r.status_code}"))
        except Exception as e:
            out.append(_r("Image generation", FAIL, f"{s.image_base_url}: {type(e).__name__}", "Start the image server (Stable Diffusion WebUI needs --api)."))
    else:
        out.append(_r("Image generation", WARN, "not configured", "Optional: set JARVIS_IMAGE_PROVIDER and JARVIS_IMAGE_BASE_URL."))
    from .tools import personal
    from .tools.registry import ToolContext

    if s.calendar_ics.strip():
        try:
            data = await asyncio.to_thread(personal.calendar_events, ToolContext(app), 7)
            out.append(_r("Calendar", WARN if data.get("problems") else OK, f"{len(data['events'])} event(s) in the next 7 days" + (f"; could not read: {'; '.join(data['problems'])}" if data.get("problems") else "")))
        except Exception as e:
            out.append(_r("Calendar", FAIL, f"{type(e).__name__}: {e}"))
    else:
        out.append(_r("Calendar", WARN, "no external calendar connected (JARVIS's own events still work)", "Optional: set JARVIS_CALENDAR_ICS to your calendar's .ics link."))
    try:
        personal.mail_config(ToolContext(app))
        try:
            data = await asyncio.to_thread(personal.email_inbox, ToolContext(app), 1)
            out.append(_r("Email", OK, f"signed in as {s.email_address}; {data['count']} unread shown"))
        except Exception as e:
            out.append(_r("Email", FAIL, f"could not sign in to {s.imap_host}: {type(e).__name__}: {str(e)[:120]}", "Check the app password and that IMAP is enabled for the account."))
    except RuntimeError:
        out.append(_r("Email", WARN, "not configured", "Optional: set JARVIS_EMAIL_ADDRESS, JARVIS_IMAP_HOST, JARVIS_SMTP_HOST, JARVIS_EMAIL_PASSWORD."))
    if online:
        from .tools import research

        try:
            data = await asyncio.wait_for(asyncio.to_thread(research.web_search, ToolContext(app, task_id="doctor"), "test", 1), 25)
            out.append(_r("Web search", OK, f"reachable via {data['engine']}"))
        except Exception as e:
            out.append(_r("Web search", WARN, f"unreachable: {str(e)[:140]}", "Web research needs internet access to DuckDuckGo or Wikipedia."))
    return out


def render(results: list[dict]) -> str:
    mark = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    lines = []
    for r in results:
        lines.append(f"{mark[r['status']]} {r['name']}: {r['detail']}")
        if r.get("fix"):
            lines.append(f"         -> {r['fix']}")
    fails, warns = sum(r["status"] == FAIL for r in results), sum(r["status"] == WARN for r in results)
    lines.append(f"\n{len(results) - fails - warns} ok, {warns} warning(s), {fails} failure(s)")
    return "\n".join(lines)


def main() -> int:
    from .app import Jarvis

    app = Jarvis()
    results: list[dict[str, Any]] = asyncio.run(diagnose(app, online="--offline" not in sys.argv))
    print(json.dumps(results, indent=1) if "--json" in sys.argv else render(results))
    return 1 if any(r["status"] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
