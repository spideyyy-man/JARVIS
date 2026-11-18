"""Voice assistant: instant replies, Tamil commands and replies, and the fixed prompt that lets a local model reuse
what it already processed (the main cost on a CPU: re-reading ~1000 tokens took 21 s, a cached prefix 1.1 s)."""
import json
import re
from pathlib import Path

import httpx
import pytest

from conftest import ScriptedLLM, make, run
from jarvis import app as app_mod
from jarvis.agents import FLAT_CORE, flat_system, warm_local_model
from jarvis.llm.base import LLMRouter, ToolCall
from jarvis.llm.offline import Env, OfflineProvider, fast_talk, is_tamil, match_rules, strip_context, with_context
from jarvis.llm.providers import OpenAICompatProvider

ENV = Env(Path("/home/u"), lambda: "/proj", lambda: [])

TAMIL = [
    ("சத்தத்தை அதிகமாக்கு", "system_control", {"action": "volume_up"}),
    ("வால்யூம் கொஞ்சம் கூட்டு", "system_control", {"action": "volume_up"}),
    ("சத்தத்தைக் குறை", "system_control", {"action": "volume_down"}),
    ("வால்யூமை 10 குறைச்சு", "system_control", {"action": "volume_down", "value": 10}),
    ("மியூட் பண்ணு", "system_control", {"action": "volume_mute"}),
    ("பாட்டை நிறுத்து", "system_control", {"action": "media_play_pause"}),
    ("அடுத்த பாட்டு போடு", "system_control", {"action": "media_next"}),
    ("முந்தைய பாடல்", "system_control", {"action": "media_previous"}),
    ("பிரைட்னஸ் 40 ஆக வை", "system_control", {"action": "set_brightness", "value": 40}),
    ("கணினியை லாக் பண்ணு", "system_control", {"action": "lock_screen"}),
    ("குரோம் திற", "open_application", {"name": "chrome"}),
    ("குரோமை திறந்து விடு", "open_application", {"name": "chrome"}),
    ("நோட்பேட் ஓபன் பண்ணு", "open_application", {"name": "notepad"}),
    ("கால்குலேட்டரை திறக்கவும்", "open_application", {"name": "calculator"}),
    ("chrome ஓப்பன் பண்ணு", "open_application", {"name": "chrome"}),
    ("யூடியூப் திற", "browser_open", {"url": "youtube.com"}),
    ("என் கம்ப்யூட்டர் எப்படி இருக்கு", "system_stats", {}),
    ("ரேம் எவ்வளவு", "system_stats", {}),
    ("திரையில் என்ன இருக்கு", "screen_read", {}),
    ("நான் என்ன காப்பி பண்ணேன்", "clipboard_read", {}),
    ("10 நிமிடத்தில் மீட்டிங் என்று நினைவூட்டு", "add_dev_task", {"title": "மீட்டிங்", "due_in_minutes": 10}),
    ("2 மணி நேரத்தில் மருந்து சாப்பிட ஞாபகப்படுத்து", "add_dev_task", {"title": "மருந்து சாப்பிட", "due_in_minutes": 120}),
    ("சென்னை வானிலை பற்றி இணையத்தில் தேடு", "web_search", {"query": "சென்னை வானிலை"}),
]


@pytest.mark.parametrize("phrase,tool,args", TAMIL)
def test_tamil_command_routes_to_tool(phrase, tool, args):
    hit = match_rules(phrase, ENV)
    assert hit is not None, f"no rule matched: {phrase}"
    assert hit[1][0] == tool, f"{phrase!r} routed to {hit[1][0]}"
    assert args.items() <= hit[1][1].items(), hit[1][1]


@pytest.mark.parametrize("phrase", ["இந்தியாவின் தலைநகரம் எது?", "ஒரு கவிதை எழுது", "திருக்குறள் பற்றி சொல்லு"])
def test_tamil_questions_are_left_for_the_model(phrase):
    assert match_rules(phrase, ENV) is None and fast_talk(phrase) is None


@pytest.mark.parametrize("phrase", ["turn the volume up", "mute", "open notepad", "lock my laptop"])
def test_tamil_rules_never_take_english_requests(phrase):
    hit = match_rules(phrase, ENV)
    assert hit and not is_tamil(phrase) and hit[0].pattern.pattern.find("சத்த") < 0 and "ஓ" not in hit[0].pattern.pattern


@pytest.mark.parametrize("phrase,expect", [
    ("what time is it?", r"^It's \d{1,2}:\d\d [AP]M\.$"), ("Jarvis, what's the time", r"^It's "), ("what day is today", r"^Today is \w+day, "),
    ("hello", r"^Hello"), ("thank you jarvis", r"welcome"), ("who are you?", r"JARVIS"),
    ("நேரம் என்ன", r"^இப்போது (காலை|மதியம்|மாலை|இரவு) \d{1,2}:\d\d\.$"), ("இப்போ என்ன நேரம்?", r"^இப்போது "), ("மணி என்ன ஆச்சு", r"^இப்போது "),
    ("இன்னைக்கு என்ன நாள்", r"^இன்று \S+கிழமை, "), ("வணக்கம் ஜார்விஸ்", r"^வணக்கம்"), ("நன்றி", r"^மகிழ்ச்சி"), ("நீ யார்?", r"JARVIS"),
])
def test_instant_replies(phrase, expect):
    reply = fast_talk(phrase)
    assert reply and re.search(expect, reply), reply


@pytest.mark.parametrize("phrase", ["hi, write me a poem about rain", "help me plan a trip", "what time does the pharmacy close",
                                    "thanks to you the build passed, now run the tests", "மணி"])
def test_instant_replies_leave_real_requests_alone(phrase):
    assert fast_talk(phrase) is None


def _after(question, tool, args, result, is_error=False):
    turn = [{"role": "user", "content": question}, {"role": "assistant", "content": "", "tool_calls": [ToolCall("c1", tool, args)]},
            {"role": "tool", "results": [{"id": "c1", "name": tool, "content": json.dumps(result), "is_error": is_error}]}]
    return run(OfflineProvider(ENV).complete("", turn, [{"name": tool}], meta={"agent": "computer"})).text


def test_tamil_requests_get_tamil_replies():
    assert _after("சத்தத்தை அதிகமாக்கு", "system_control", {"action": "volume_up"}, {"done": "volume_up", "value": 5}) == "சத்தத்தை அதிகரித்தேன்."
    assert _after("குரோம் திற", "open_application", {"name": "chrome"}, {"opened": "chrome", "launched": ["cmd"], "pid": 1}) == "chrome திறக்கிறேன்."
    assert _after("நான் என்ன காப்பி பண்ணேன்", "clipboard_read", {}, {"chars": 0, "text": ""}) == "கிளிப்போர்டு காலியாக உள்ளது."
    assert _after("குரோம் திற", "open_application", {"name": "chrome"}, {"error": "No installed application matches 'chrome'"}, True).startswith("மன்னிக்கவும்")
    pending = _after("கணினியை லாக் பண்ணு", "system_control", {"action": "lock_screen"}, {"status": "pending_approval", "approval_id": "ap_1", "preview": "Lock the screen"})
    assert "அனுமதி" in pending and "ap_1" in pending


def test_english_replies_are_short_enough_to_speak():
    assert _after("turn the volume up", "system_control", {}, {"done": "volume_up", "value": 5}) == "Volume up."
    assert _after("open notepad", "open_application", {}, {"opened": "notepad", "launched": ["cmd", "/c", "start", "", "C:/x/notepad.exe"], "pid": 1}) == "Opening notepad."


def test_rule_engine_ignores_the_context_block_a_local_model_gets():
    wrapped = with_context("open notepad", "Long-term memory relevant to this request:\n- The user likes tea")
    assert wrapped.startswith("<context>\nCurrent time: ") and wrapped.endswith("\n\nopen notepad")
    assert strip_context(wrapped) == "open notepad"
    resp = run(OfflineProvider(ENV).complete("", [{"role": "user", "content": wrapped}], [{"name": "open_application"}], meta={"agent": "assistant"}))
    assert resp.tool_calls[0].name == "open_application" and resp.tool_calls[0].args == {"name": "notepad"}


def test_tamil_yes_and_no_decide_a_pending_approval():
    assert all(app_mod.AFFIRM.match(w) for w in ("சரி", "ஆமா", "சரி செய்", "ஜார்விஸ், சரி"))
    assert all(app_mod.DENY.match(w) for w in ("வேண்டாம்", "இல்லை", "ரத்து பண்ணு"))
    assert not app_mod.AFFIRM.match("சரியான பதில் சொல்லு")


def _local(script):
    llm = ScriptedLLM(script)
    llm.name = "openai_compat"
    return llm


def _with(home, tmp_path, llm):
    j = make(home, tmp_path)
    j.llm = LLMRouter([llm, j.offline], backoff=0.01)
    return j


def test_quick_questions_take_the_fast_path_even_with_a_model(home, tmp_path):
    llm = _local([])  # any model call would fail: the script is empty
    j = _with(home, tmp_path, llm)
    try:
        for q in ("what time is it?", "நேரம் என்ன", "வணக்கம்"):
            assert j.fast_path_applies(q)
            r = run(j.chat(q))
            assert r["provider"] == "offline" and r["text"]
        assert not j.fast_path_applies("write me a two line poem about rain")
        assert llm.seen == []
    finally:
        j.db.close()


def test_local_model_gets_the_same_prompt_start_every_time(home, tmp_path):
    llm = _local([("Paris.", []), ("Rain taps the glass.", []), ("Here you go.", []), ("Paris.", [])])
    j = _with(home, tmp_path, llm)
    j.db.set_setting("fast_path", False)
    j.memory.remember("The user's favourite city is Paris", "fact")
    try:
        run(j.chat("what is the capital of France?"))
        run(j.chat("write a two line poem about rain"))
        run(j.chat("read my notes file in Downloads"))
        systems = {s["system"] for s in llm.seen}
        assert systems == {flat_system(j)}, "the system prompt must not change between requests"
        assert "Current time" not in flat_system(j) and "create_plan" not in flat_system(j)
        core = [n for n in FLAT_CORE if n in {t.name for t in j.tools.all()}]
        for s in llm.seen:
            assert s["tools"][: len(core)] == core, s["tools"]
        assert llm.seen[0]["tools"] == core, "a plain question adds no request-specific tools"
        user = llm.seen[0]["messages"][-1]["content"]
        assert user.startswith("<context>\nCurrent time: ") and user.endswith("what is the capital of France?")
        run(j.chat("what is my favourite city?"))
        assert "favourite city is Paris" in llm.seen[-1]["messages"][-1]["content"]  # recalled memory travels with the request
        assert llm.seen[-1]["system"] == flat_system(j)
    finally:
        j.db.close()


def test_tools_that_change_things_are_not_offered_on_shared_words_alone(jarvis):
    """Measured on the owner's laptop: 'write a two line poem' put write_file in front of the 3B model, which called it."""
    from jarvis.agents import flat_core, flat_extras
    from jarvis.security import Level

    tools = [t for t in jarvis.tools.all() if t.name != "delegate"]
    core = flat_core(tools)
    by_name = {t.name: t for t in tools}
    for q in ("write a two line poem about rain", "write me an email to my boss", "delete the boring parts of this story"):
        extras = flat_extras(q, tools, core, jarvis.embedder, set(), 4)
        assert all(t.level == Level.READ for t in extras), [t.name for t in extras]
    # ...but when the rule engine recognises the request, its tool is offered whatever its level
    hinted = flat_extras("create a folder /home/u/new", tools, core, jarvis.embedder, {"create_directory"}, 4)
    assert by_name["create_directory"] in hinted


def test_warm_up_preloads_exactly_the_prompt_requests_start_with(home, tmp_path):
    seen = []

    class Local:
        name, model = "openai_compat", "m"

        async def warm(self, system, tools, keep_alive=None):
            seen.append((system, [t["name"] for t in tools]))
            return {"prompt_tokens": 900}

        async def complete(self, *a, **k):
            raise AssertionError("not used")

    j = _with(home, tmp_path, Local())
    try:
        assert run(warm_local_model(j)) == {"prompt_tokens": 900}
        core = [n for n in FLAT_CORE if n in {t.name for t in j.tools.all()}]
        assert seen == [(flat_system(j), core)]
    finally:
        j.db.close()
    j2 = make(home, tmp_path / "b")
    try:
        assert run(warm_local_model(j2)) is None  # nothing to warm without a local model
    finally:
        j2.db.close()


def test_provider_sends_keep_alive_and_a_one_token_warm_up():
    bodies = []

    def handler(req: httpx.Request):
        bodies.append((req.url.path, json.loads(req.content)))
        if req.url.path.endswith("/api/generate"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 5, "prompt_tokens_details": {"cached_tokens": 4}}})

    p = OpenAICompatProvider("http://local:11434/v1", "m", transport=httpx.MockTransport(handler), stream=False, keep_alive="2h")
    usage = run(p.warm("sys", [{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}]))
    assert usage["prompt_tokens_details"]["cached_tokens"] == 4
    path, body = bodies[-1]
    assert path == "/v1/chat/completions" and body["max_tokens"] == 1 and body["keep_alive"] == "2h" and body["tools"][0]["function"]["name"] == "t"
    run(p.complete("sys", [{"role": "user", "content": "hi"}], []))
    assert bodies[-1][1]["keep_alive"] == "2h"
    run(p.unload())
    assert bodies[-1] == ("/api/generate", {"model": "m", "keep_alive": 0})
    plain = OpenAICompatProvider("http://other/v1", "m", transport=httpx.MockTransport(handler), stream=False)
    run(plain.complete("sys", [{"role": "user", "content": "hi"}], []))
    assert "keep_alive" not in bodies[-1][1]  # only sent when configured (servers other than Ollama)
