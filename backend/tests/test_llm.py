import json

import httpx

from conftest import ScriptedLLM, make, run
from jarvis.llm.base import LLMError, LLMRouter, ToolCall
from jarvis.llm.providers import AnthropicProvider, OpenAICompatProvider
from jarvis.voice import OpenAICompatSpeech


def test_agent_loop_with_a_tool_calling_model(home, tmp_path):
    """The path a hosted model takes: orchestrator -> delegate -> specialist tool calls -> merged answer."""
    llm = ScriptedLLM([
        ("", [("delegate", {"agent": "monitoring", "task": "Report CPU and memory usage"})]),   # orchestrator
        ("", [("system_stats", {}), ("list_processes", {"sort_by": "memory", "limit": 3})]),     # monitoring agent, parallel calls
        ("CPU is fine and memory is fine.", []),                                                  # monitoring agent answer
        ("Your machine is healthy: CPU is fine and memory is fine.", []),                         # orchestrator answer
    ])
    j = make(home, tmp_path, providers=[llm])
    r = run(j.chat("how is my machine doing?"))
    assert r["text"].startswith("Your machine is healthy") and r["provider"] == "scripted"
    assert [t["tool"] for t in r["tools"]] == ["system_stats", "list_processes", "delegate"]
    # real tool output reached the model, as two results in ONE tool message
    tool_msg = llm.seen[2]["messages"][-1]
    assert tool_msg["role"] == "tool" and len(tool_msg["results"]) == 2 and '"cpu"' in tool_msg["results"][0]["content"]
    # structured inter-agent messages + shared task state
    kinds = [(m["sender"], m["recipient"], m["type"]) for m in j.db.query("SELECT * FROM agent_messages ORDER BY created")]
    assert kinds == [("user", "orchestrator", "request"), ("orchestrator", "monitoring", "request"), ("monitoring", "orchestrator", "result"), ("orchestrator", "user", "result")]
    sub = j.db.one("SELECT * FROM tasks WHERE agent='monitoring'")
    assert sub["status"] == "done" and sub["parent_id"] == r["task_id"]


def test_tool_error_is_fed_back_so_the_model_can_recover(home, tmp_path):
    llm = ScriptedLLM([
        ("", [("read_file", {"path": str(home / "missing.txt")})]),
        ("", [("list_dir", {"path": str(home)})]),
        ("missing.txt does not exist; the folder contains Downloads.", []),
    ])
    j = make(home, tmp_path, providers=[llm])
    res = run(j.agents.run("computer", "read missing.txt", conversation_id=None))
    err = llm.seen[1]["messages"][-1]["results"][0]
    assert err["is_error"] and "FileNotFoundError" in err["content"]
    assert "does not exist" in res.text and res.steps == 3


def test_step_limit_stops_a_looping_model(home, tmp_path):
    llm = ScriptedLLM([("", [("system_stats", {})])] * 20)
    j = make(home, tmp_path, providers=[llm])
    j.settings.max_agent_steps = 3
    res = run(j.agents.run("monitoring", "loop forever", conversation_id=None))
    assert "step limit" in res.text and res.steps == 3
    assert j.db.one("SELECT status FROM tasks WHERE id=?", (res.task_id,))["status"] == "incomplete"


def test_failover_to_offline_engine_when_api_is_down(home, tmp_path):
    down = ScriptedLLM([LLMError("Cannot reach API", retryable=True)] * 10)
    j = make(home, tmp_path)
    events = []
    j.llm = LLMRouter([down, j.offline], on_event=lambda t, **d: (events.append(d), j._llm_event(t, **d)), retries=2, backoff=0.01)
    r = run(j.chat("analyze my computer's performance"))
    assert "System performance" in r["text"] and r["provider"] == "offline"
    assert len(down.seen) == 3  # 1 try + 2 retries on the first call, then failover
    assert events[0]["retrying"] and events[2]["next"] == "offline"
    assert j.db.one("SELECT COUNT(*) AS n FROM notifications WHERE title LIKE 'Language model%'")["n"] >= 1


def test_non_retryable_error_fails_over_immediately(home, tmp_path):
    bad = ScriptedLLM([LLMError("authentication failed")] * 5)
    j = make(home, tmp_path)
    j.llm = LLMRouter([bad, j.offline], retries=2, backoff=0.01)
    assert "track" in run(j.chat("remind me to rotate the API key"))["text"]
    assert len(bad.seen) == 1  # no retry on auth errors, and the circuit breaker skips it for the rest of the turn


def test_interruption_cancels_turn_and_records_it(home, tmp_path):
    import asyncio

    class Slow:
        name, model = "slow", "slow"

        async def complete(self, system, messages, tools, on_text=None, meta=None):
            on_text("Let me think")
            await asyncio.sleep(30)

    j = make(home, tmp_path, providers=[Slow()])

    async def scenario():
        task = asyncio.create_task(j.chat("tell me a very long story", "c1"))
        await asyncio.sleep(0.1)
        assert j.cancel("c1")
        try:
            await task
        except asyncio.CancelledError:
            return True

    assert run(scenario())
    last = j.db.query("SELECT * FROM messages WHERE conversation_id='c1' ORDER BY created")[-1]
    assert last["role"] == "assistant" and "interrupted" in last["content"] and "Let me think" in last["content"]


def test_history_and_memory_reach_the_model(home, tmp_path):
    llm = ScriptedLLM([("Noted.", []), ("You said blue.", [])])
    j = make(home, tmp_path, providers=[llm])
    j.memory.remember("The user's favourite colour is blue", "preference")
    run(j.chat("my favourite colour is blue", "c1"))
    run(j.chat("what is my favourite colour?", "c1"))
    second = llm.seen[1]
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
    assert "favourite colour is blue" in second["system"]  # long-term memory injected


def test_openai_compatible_provider_round_trip():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "system_stats", "arguments": "{}"}}]}}]})

    p = OpenAICompatProvider("http://local/v1", "llama3", "k", transport=httpx.MockTransport(handler), stream=False)
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "", "tool_calls": [ToolCall("c0", "x", {"a": 1})]},
            {"role": "tool", "results": [{"id": "c0", "name": "x", "content": "{}", "is_error": False}]}]
    resp = run(p.complete("sys", msgs, [{"name": "system_stats", "description": "d", "input_schema": {"type": "object", "properties": {}}}]))
    assert resp.tool_calls[0].name == "system_stats" and seen["auth"] == "Bearer k"
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user", "assistant", "tool"]
    assert seen["body"]["tools"][0]["function"]["name"] == "system_stats"

    failing = OpenAICompatProvider("http://local/v1", "m", transport=httpx.MockTransport(lambda r: httpx.Response(503, text="busy")))
    try:
        run(failing.complete("s", [{"role": "user", "content": "x"}], []))
        raise AssertionError("expected LLMError")
    except LLMError as e:
        assert e.retryable


def test_anthropic_adapter_message_conversion():
    p = AnthropicProvider("claude-opus-5-5", api_key="test-key-not-real")
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "tool_use", "id": "tu1", "name": "system_stats", "input": {}}]
    out = p._convert([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [ToolCall("tu1", "system_stats", {})], "raw": raw, "provider": "anthropic"},
        {"role": "tool", "results": [{"id": "tu1", "name": "system_stats", "content": "{}", "is_error": False}, {"id": "tu2", "name": "x", "content": "boom", "is_error": True}]},
        {"role": "assistant", "content": "from another provider", "tool_calls": [], "raw": None, "provider": "offline"},
    ])
    assert out[1]["content"] is raw  # thinking blocks passed back unchanged
    assert out[2]["role"] == "user" and [b["type"] for b in out[2]["content"]] == ["tool_result", "tool_result"] and out[2]["content"][1]["is_error"]
    assert out[3]["content"] == [{"type": "text", "text": "from another provider"}]


def test_server_side_speech_provider_protocol():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/audio/transcriptions"):
            assert b"whisper-1" in request.content and b"RIFFdata" in request.content
            return httpx.Response(200, json={"text": "jarvis what time is it"})
        assert json.loads(request.content)["input"] == "Hello"
        return httpx.Response(200, content=b"ID3audio", headers={"content-type": "audio/mpeg"})

    s = OpenAICompatSpeech("http://voice/v1", None, transport=httpx.MockTransport(handler))
    assert run(s.transcribe(b"RIFFdata", "a.wav")) == "jarvis what time is it"
    assert run(s.synthesize("Hello")) == (b"ID3audio", "audio/mpeg")


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_local_image_generation_both_protocols(jarvis, home):
    import base64

    from conftest import run as _run

    r = _run(jarvis.chat("draw me a red fox in the snow"))
    assert "not configured" in r["text"]  # honest when no image server is set

    seen = {}

    def a1111(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={"images": [base64.b64encode(PNG).decode()]})

    jarvis.settings.image_provider, jarvis.settings.image_base_url = "automatic1111", "http://127.0.0.1:7860"
    jarvis.http_transport = httpx.MockTransport(a1111)
    r = _run(jarvis.chat("generate an image of a red fox in the snow"))
    assert seen["path"] == "/sdapi/v1/txt2img" and seen["body"]["prompt"] == "a red fox in the snow"
    files = list(jarvis.settings.images_dir.iterdir())
    assert len(files) == 1 and files[0].read_bytes() == PNG and f"](/api/images/{files[0].name})" in r["text"]

    jarvis.settings.image_provider, jarvis.settings.image_base_url = "openai_compat", "http://127.0.0.1:8080/v1"
    jarvis.http_transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}) if req.url.path == "/v1/images/generations" else httpx.Response(404))
    assert _run(jarvis.executor.call("generate_image", {"prompt": "a lighthouse", "width": 5000}, __import__("jarvis.tools.registry", fromlist=["ToolContext"]).ToolContext(jarvis))).data["width"] == 1536

    jarvis.http_transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"<html>not an image</html>").decode()}]}))
    bad = _run(jarvis.chat("generate an image of a cat"))
    assert "did not return" in bad["text"] and len(list(jarvis.settings.images_dir.iterdir())) == 2
