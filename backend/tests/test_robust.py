"""A weak local model misbehaves in predictable ways. These tests script those misbehaviours and
check the agent still gets the job done - or stops cleanly."""
import json

import httpx

from conftest import ScriptedLLM, make, run
from jarvis.llm.base import LLMResponse, ToolCall
from jarvis.llm.providers import OpenAICompatProvider
from jarvis.robust import coerce_args, extract_text_calls, repair_response, resolve_name

ALLOWED = {"system_stats", "list_processes", "search_files", "read_file"}


def test_tool_calls_written_as_text_are_recovered():
    for text in ['I will check.\n```json\n{"name": "list_processes", "arguments": {"sort_by": "memory"}}\n```',
                 '<tool_call>{"name": "list_processes", "arguments": {"sort_by": "memory"}}</tool_call>',
                 'Calling list_processes({"sort_by": "memory"}) now',
                 'Sure: {"tool": "list_processes", "args": {"sort_by": "memory"}}',
                 '{"function": {"name": "list_processes", "arguments": "{\\"sort_by\\": \\"memory\\"}"}}']:
        calls, rest = extract_text_calls(text, ALLOWED)
        assert calls == [("list_processes", {"sort_by": "memory"})], text
        assert "{" not in rest
    # ordinary JSON in an answer is NOT a tool call
    assert extract_text_calls('The config is {"name": "production", "port": 80}.', ALLOWED)[0] == []


def test_names_and_arguments_are_repaired_but_never_invented():
    assert resolve_name("functions.System-Stats", ALLOWED) == "system_stats"
    assert resolve_name("serach_files", ALLOWED) == "search_files"
    assert resolve_name("format_hard_drive", ALLOWED) is None  # no fuzzy match onto something unrelated
    schema = {"properties": {"limit": {"type": "integer"}, "keywords": {"type": "array"}, "show_hidden": {"type": "boolean"}, "root": {"type": "string"}}}
    assert coerce_args({"limit": "5", "keywords": "budget, report", "show_hidden": "true", "rooot": 7, "made_up": 1}, schema) == \
        {"limit": 5, "keywords": ["budget", "report"], "show_hidden": True, "root": "7"}
    assert coerce_args('{"limit": 3}', schema) == {"limit": 3}
    resp, fixes = repair_response(LLMResponse(tool_calls=[ToolCall("1", "List_Processes", {"limit": "3", "bogus": 1})]),
                                  {"list_processes": {"properties": {"limit": {"type": "integer"}}}})
    assert resp.tool_calls[0].name == "list_processes" and resp.tool_calls[0].args == {"limit": 3} and len(fixes) == 2


def test_agent_completes_despite_a_sloppy_model(home, tmp_path):
    llm = ScriptedLLM([
        ('Let me look.\n```json\n{"name": "delegate", "arguments": {"agent": "monitoring", "task": "check memory"}}\n```', []),  # call as text
        ("", [("List_Processes", {"sort_by": "memory", "limit": "3", "verbose": True})]),  # wrong case, string int, invented arg
        ("Top process found.", []),
        ("Your top memory user was found.", []),
    ])
    j = make(home, tmp_path, providers=[llm])
    r = run(j.chat("what is using my memory?"))
    assert r["text"] == "Your top memory user was found." and not r["errors"]
    assert [t["tool"] for t in r["tools"]] == ["list_processes", "delegate"]
    sub = j.db.one("SELECT trace FROM tasks WHERE agent='monitoring'")
    assert sub["trace"][0]["repaired"] and sub["trace"][1] == {"step": 1, "tool": "list_processes", "args": {"sort_by": "memory", "limit": 3}}
    assert "```" not in j.db.one("SELECT content FROM messages WHERE role='assistant'")["content"]


def test_hallucinated_tool_gets_a_helpful_error_listing_real_tools(home, tmp_path):
    llm = ScriptedLLM([("", [("get_weather", {"city": "Paris"})]), ("I can't check the weather.", [])])
    j = make(home, tmp_path, providers=[llm])
    run(j.agents.run("monitoring", "weather?", conversation_id=None))
    err = llm.seen[1]["messages"][-1]["results"][0]["content"]
    assert "no tool named 'get_weather'" in err and "system_stats" in err


def test_repeating_model_is_warned_then_stopped(home, tmp_path):
    llm = ScriptedLLM([("", [("system_stats", {})])] * 12)
    j = make(home, tmp_path, providers=[llm])
    res = run(j.agents.run("monitoring", "loop", conversation_id=None))
    assert "repeating the same step" in res.text and res.steps == 5
    warned = llm.seen[3]["messages"][-1]["results"][0]
    assert warned["is_error"] and "already made this exact call" in warned["content"]
    assert j.db.one("SELECT COUNT(*) AS n FROM audit_log WHERE tool='system_stats'")["n"] == 2  # only the first two actually ran
    assert j.db.one("SELECT status FROM tasks WHERE id=?", (res.task_id,))["status"] == "incomplete"


def test_small_models_see_a_relevant_shortlist_of_tools(home, tmp_path):
    llm = ScriptedLLM([("ok", [])])
    j = make(home, tmp_path, providers=[llm])
    j.settings.max_tools = 8
    run(j.agents.run("computer", "turn the volume up and tell me what is on my clipboard", conversation_id=None))
    shown = llm.seen[0]["tools"]
    assert len(shown) == 8 and {"system_control", "clipboard_read"} <= set(shown)
    assert {"list_dir", "search_files", "read_file", "screen_read"} <= set(shown)  # the always-shown core
    assert len(j.tools.subset(__import__("jarvis.agents", fromlist=["AGENTS"]).AGENTS["computer"].tools)) > 25


def test_slow_model_times_out_and_fails_over(home, tmp_path):
    import asyncio

    class Hung:
        name, model = "hung", "hung"

        async def complete(self, *a, **k):
            await asyncio.sleep(60)

    from jarvis.llm.base import LLMRouter

    j = make(home, tmp_path)
    j.llm = LLMRouter([Hung(), j.offline], timeout=0.2)
    j.db.set_setting("fast_path", False)
    r = run(j.chat("how is my computer performing"))
    assert r["provider"] == "offline" and "System performance" in r["text"]
    assert "did not answer within" in j.llm.failures[0]["error"]


def sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def test_openai_compatible_streaming_text_tool_calls_and_think_tags():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        if body["messages"][-1]["content"] == "tool":
            return httpx.Response(200, text=sse(
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "search_files", "arguments": ""}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"keywords": ["bud'}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'get"]}'}}, {"index": 1, "id": "c2", "function": {"name": "system_stats", "arguments": "{}"}}]}}]},
            ), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "<think>"}}]}, {"choices": [{"delta": {"content": "secret plan"}}]},
                                            {"choices": [{"delta": {"content": "</think>"}}]}, {"choices": [{"delta": {"content": "Hello "}}]},
                                            {"choices": [{"delta": {"content": "there."}}]}, {"choices": []}), headers={"content-type": "text/event-stream"})

    p = OpenAICompatProvider("http://local/v1", "m", transport=httpx.MockTransport(handler))
    pieces = []
    resp = run(p.complete("s", [{"role": "user", "content": "hi"}], [], pieces.append))
    assert pieces == ["Hello ", "there."] and resp.text == "Hello there."  # tokens streamed, private reasoning never shown
    resp = run(p.complete("s", [{"role": "user", "content": "tool"}], []))
    assert [(c.id, c.name, c.args) for c in resp.tool_calls] == [("c1", "search_files", {"keywords": ["budget"]}), ("c2", "system_stats", {})]


def test_stream_filter_hides_text_form_tool_calls_but_not_ordinary_text():
    from jarvis.robust import StreamFilter

    def run_stream(pieces, final, had_calls):
        out = []
        f = StreamFilter(out.append)
        for p in pieces:
            f.feed(p)
        f.finish(final, had_calls)
        return "".join(out)

    sloppy = ["Let me ", "check that.\n", "``", "`json\n", '{"name": "system_stats", ', '"arguments": {}}\n', "```"]
    assert run_stream(sloppy, "Let me check that.", True) == "Let me check that.\n"          # JSON never reaches the user
    assert run_stream(["Here is ", "the plan: ", "step one, ", "then step two."], "Here is the plan: step one, then step two.", False) == "Here is the plan: step one, then step two."
    code = ["Use this:\n", "```python\n", "print('hi')\n", "```", " Done."]
    assert run_stream(code, "".join(code), False) == "".join(code)                           # a real code answer is delivered in full, just later
    assert run_stream(['<tool_call>{"name": "x", "arguments": {}}</tool_call>'], "", True) == ""
