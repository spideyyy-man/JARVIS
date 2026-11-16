"""Stand-in for a local language-model server (Ollama / LM Studio protocol: POST /v1/chat/completions, SSE).
It is NOT intelligent: a few keyword rules decide what to do. Its purpose is to exercise JARVIS's whole
model path over real HTTP - streaming, tool-call fragments, and the sloppy output small models produce
(a tool call written as text, a mis-cased tool name, a number sent as a string, private <think> text).

    python frontend/e2e/stub_llm.py 9922
"""
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = []


def decide(messages, tools):
    names = {t["function"]["name"] for t in tools}
    last = messages[-1]
    users = [m["content"] for m in messages if m["role"] == "user" and isinstance(m.get("content"), str)]
    path = next((m.group(1) for u in reversed(users) for m in [re.search(r"((?:/|[A-Za-z]:\\)[^\s'\"]+\.txt)", u)] if m), None)
    if last["role"] == "tool":
        body = last.get("content") or ""
        if "pending_approval" in body:
            return {"text": "I have prepared that step. It is waiting for your approval."}
        if '"cpu"' in body:
            cpu = re.search(r'"cpu": \{"percent": ([\d.]+)', body)
            return {"text": f"Your CPU is at {cpu.group(1) if cpu else '?'} percent and nothing looks wrong."}
        if '"content"' in body:
            content = re.search(r'"content": "([^"]*)"', body)
            return {"text": f"Verified: the note now contains '{content.group(1) if content else ''}'. The task is complete."}
        return {"text": "Done: " + body[:160]}
    text = users[-1] if users else ""
    if "Continue the original task" in text and path:
        return {"call": ("Read_File", {"path": path}), "style": "native"}          # mis-cased tool name
    if "struggling" in text:
        return {"call": ("system_stats", {}), "style": "text"}                      # tool call written as text
    if "write the note" in text and path:
        said = re.search(r"saying (.+?)(?:,| then|$)", text)
        return {"call": ("write_file", {"path": path, "content": said.group(1).strip() if said else "note", "overwrite": "false"}), "style": "native"}  # bool as string
    if "list_tools" in names and "trash" in text:
        return {"call": ("list_tools", {"query": "trash deleted"}), "style": "native"}
    return {"text": "<think>the user is just chatting</think>Hello. I am the stand-in model."}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps(LOG if self.path == "/log" else {"data": [{"id": "stub-model"}]}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        tools = req.get("tools") or []
        action = decide(req["messages"], tools)
        LOG.append({"stream": req.get("stream"), "tools_offered": len(tools), "has_delegate": any(t["function"]["name"] == "delegate" for t in tools),
                    "action": action.get("call", [action.get("text", "")[:40]])[0]})
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()

        def send(delta):
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': delta}]})}\n\n".encode()); self.wfile.flush(); time.sleep(0.01)

        if "call" in action and action["style"] == "native":
            name, args = action["call"]
            raw = json.dumps(args)
            send({"tool_calls": [{"index": 0, "id": f"call_{len(LOG)}", "function": {"name": name, "arguments": ""}}]})
            for i in range(0, len(raw), 9):  # arguments arrive in fragments, as real servers send them
                send({"tool_calls": [{"index": 0, "function": {"arguments": raw[i:i + 9]}}]})
        else:
            text = action.get("text") or ("Let me check that.\n```json\n" + json.dumps({"name": action["call"][0], "arguments": action["call"][1]}) + "\n```")
            for piece in re.findall(r"\S+\s*", text):
                send({"content": piece})
        self.wfile.write(b"data: [DONE]\n\n")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9922), Handler).serve_forever()
