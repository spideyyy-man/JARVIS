"""Offline listener (logic, no microphone), floating-icon API, local pages in the browser, and the self-test."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedLLM, make, run
from jarvis.api import create_app
from jarvis.events import EventBus, describe
from jarvis.listen import FRAME_S, Endpointer, VoiceService, clean_transcript, is_noise, pick_language
from jarvis.security import SecurityError
from jarvis.selftest import SelfTest


# -- listener logic ------------------------------------------------------------------------------------------------------
def frames(seconds: float, rms: float) -> list[float]:
    return [rms] * round(seconds / FRAME_S)


def test_endpointer_waits_for_speech_then_ends_after_a_pause():
    ep = Endpointer(noise=100)
    verdicts = [ep.feed(r) for r in frames(1.0, 80) + frames(1.5, 2000) + frames(1.2, 90)]
    assert verdicts[0] == "wait" and "speech" in verdicts and verdicts[-1] == "done"
    done_at = verdicts.index("done") * FRAME_S
    assert 3.0 <= done_at <= 3.4  # 1 s quiet + 1.5 s talking + ~0.7 s pause


def test_endpointer_gives_up_when_nothing_is_said_and_caps_long_speech():
    ep = Endpointer(noise=100, start_timeout=2)
    assert [ep.feed(r) for r in frames(2.0, 90)][-1] == "timeout"
    ep = Endpointer(noise=100, max_length=3)
    assert [ep.feed(r) for r in frames(4, 3000)].index("done") * FRAME_S == pytest.approx(3.0, abs=0.1)


def test_endpointer_learns_a_noisy_room():
    ep = Endpointer(noise=100)
    for _ in range(300):
        ep.learn(400)  # a fan
    assert ep.threshold > 600
    assert ep.feed(450) == "wait"  # the fan alone is not speech


def test_transcript_cleanup_language_choice_and_noise():
    assert clean_transcript("Hey Jarvis, what time is it?") == "what time is it?"
    assert clean_transcript("Jarvis open notepad") == "open notepad"
    assert clean_transcript("ஜார்விஸ் நேரம் என்ன") == "நேரம் என்ன"
    assert clean_transcript("Tell Jarvis's story") == "Tell Jarvis's story"  # only a leading wake word is removed
    assert pick_language("te", {"ta": 0.3, "en": 0.1, "te": 0.4}, ("en", "ta")) == "ta"  # Tamil heard as Telugu
    assert pick_language("en", {"en": 0.9}, ("en", "ta")) == "en"
    assert is_noise("Thank you.", 0.1, -0.2) and is_noise("", 0.0, 0.0)
    assert is_noise("some words", 0.8, -1.2) and not is_noise("open notepad", 0.1, -0.3)


def test_spoken_request_runs_as_a_chat_turn_and_continues_the_conversation(home, tmp_path):
    llm = ScriptedLLM([("Paris.", []), ("About 2.1 million.", []), ("Hello again.", [])])
    j = make(home, tmp_path, providers=[llm])
    j.db.set_setting("fast_path", False)
    q = j.bus.subscribe()

    async def scenario():
        svc = VoiceService(j)
        a = await svc.handle("What is the capital of France?", "en", {"seconds": 1.2})
        b = await svc.handle("How many people live there?", "en")
        svc.last_used = time.time() - VoiceService.CONVERSATION_IDLE - 1
        c = await svc.handle("Hello", "en")
        return a, b, c

    a, b, c = run(scenario())
    assert a["text"] == "Paris." and a["conversation_id"] == b["conversation_id"] != c["conversation_id"]
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    heard = [e for e in events if e["type"] == "voice.heard"]
    assert heard[0]["text"] == "What is the capital of France?" and heard[0]["conversation_id"] == a["conversation_id"] and heard[0]["seconds"] == 1.2
    assert j.db.one("SELECT meta FROM messages WHERE role='user' ORDER BY created LIMIT 1")["meta"]["source"] == "voice"
    assert any(x["text"].startswith("Heard [en") for x in j.bus.activity)
    j.db.close()


def test_listener_off_by_default_outside_the_desktop_app(jarvis):
    assert jarvis.settings.listen_on_start is False and jarvis.voice.listener is None
    assert jarvis.voice.enabled()  # the user has not switched it off
    jarvis.db.set_setting("voice", {"engine": "browser"})
    assert not jarvis.voice.enabled() and jarvis.voice.start(asyncio.new_event_loop()) is False


# -- activity log -----------------------------------------------------------------------------------------------------------
def test_activity_log_keeps_readable_lines_only():
    bus = EventBus()
    bus.emit("metrics", cpu=3)
    bus.emit("tool.executed", tool="write_file", ok=True)
    bus.emit("approval.requested", approval={"tool": "delete_path", "preview": "Delete C:/x\nmore"})
    bus.set_state("executing", "write_file")
    assert [a["text"] for a in bus.activity] == ["Ran: write_file", "Needs approval: delete_path - Delete C:/x", "Working: write_file"]
    assert describe({"type": "chat.delta", "text": "x"}) is None


# -- API for the floating icon -------------------------------------------------------------------------------------------
@pytest.fixture
def client(jarvis):
    with TestClient(create_app(jarvis)) as c:
        c.h = {"Authorization": f"Bearer {jarvis.settings.api_token()}"}
        yield c


def test_overlay_endpoint_and_listen_without_listener(client, jarvis):
    assert client.get("/api/overlay").status_code == 401
    d = client.get("/api/overlay", headers=client.h).json()
    assert {"state", "voice", "approvals", "activity", "last", "selftest", "capabilities", "model"} <= set(d)
    assert d["voice"]["running"] is False and d["selftest"] is None
    r = client.post("/api/voice/listen", headers=client.h).json()
    assert r["ok"] is False and r["reason"]
    assert client.post("/api/ui/listen", headers=client.h).json()["ok"] is True  # falls back to the window's recogniser
    s = client.get("/api/settings", headers=client.h).json()
    assert s["voice"]["engine"] == "local" and "running" in s["voice_local"]
    assert client.put("/api/settings", json={"voice": {"engine": "cloud"}}, headers=client.h).status_code in (400, 422, 500)
    assert client.put("/api/settings", json={"voice": {"engine": "browser"}}, headers=client.h).json()["voice"]["engine"] == "browser"


def test_overlay_shows_a_pending_approval_and_the_last_exchange(client, jarvis, home):
    r = client.post("/api/chat", json={"text": "delete the file notes.txt in my downloads", "wait": True}, headers=client.h).json()
    d = client.get("/api/overlay", headers=client.h).json()
    assert d["last"]["request"].startswith("delete the file") and d["last"]["reply"]
    assert d["history"][0]["request"].startswith("delete the file") and d["history"][0]["reply"] == d["last"]["reply"][:300]
    if r.get("pending_approvals"):
        assert d["approvals"][0]["tool"] == "delete_path"
        assert any("Needs approval" in a["text"] for a in d["activity"])


# -- local pages in JARVIS's browser ----------------------------------------------------------------------------------------
def test_browser_opens_local_pages_only_inside_allowed_folders(jarvis, home, tmp_path):
    page = home / "site" / "index.html"
    page.parent.mkdir()
    page.write_text("<h1>hi</h1>")
    assert jarvis.browser.check_url(page.as_uri()) == page.resolve().as_uri()
    assert jarvis.browser.check_url(str(page)) == page.resolve().as_uri()  # a plain Windows/Linux path works too on Windows
    outside = tmp_path / "outside.html"
    outside.write_text("x")
    with pytest.raises(SecurityError):
        jarvis.browser.check_url(outside.as_uri())
    with pytest.raises(SecurityError):
        jarvis.browser.check_url((home / "missing.html").as_uri())
    with pytest.raises(SecurityError):
        jarvis.browser.check_url("javascript:alert(1)")


# -- self-test --------------------------------------------------------------------------------------------------------------
def test_selftest_approves_only_its_own_actions(jarvis, home):
    st = SelfTest(jarvis)
    st._allowed = {"write_file"}
    assert st.acceptable("write_file", {"path": str(st.page), "content": "x"})
    assert not st.acceptable("write_file", {"path": str(home / "elsewhere.txt"), "content": "x"})
    assert not st.acceptable("delete_path", {"path": str(st.folder)})  # not expected in this step
    assert not st.acceptable("write_file", {"content": "x"})  # no path at all
    st._allowed = {"delete_path"}
    assert st.acceptable("delete_path", {"path": str(st.folder)})
    assert not st.acceptable("delete_path", {"path": str(home)})


def test_selftest_end_to_end_with_a_scripted_model(home, tmp_path):
    j = make(home, tmp_path, providers=[])
    st = SelfTest(j)
    html = "<h1>JARVIS self-test</h1><p id='result'></p><script>document.getElementById('result').textContent = 'Result: ' + 6 * 7</script>"
    stray = home / "Downloads" / "notes.txt"
    llm = ScriptedLLM([
        # create: the model also tries something the test did not ask for - it must be denied
        ("", [("delegate", {"agent": "computer", "task": "create the page"})]),
        ("", [("write_file", {"path": str(st.page), "content": html}), ("delete_path", {"path": str(stray)})]),
        ("Waiting for approval.", []),
        ("I asked to create the file.", []),
        ("The page is created; I did not delete anything.", []),  # automatic continuation once both decisions are in
        # delete
        ("", [("delegate", {"agent": "computer", "task": "delete the folder"})]),
        ("", [("delete_path", {"path": str(st.folder)})]),
        ("Waiting for approval.", []),
        ("I asked to delete the folder.", []),
        ("The folder is deleted.", []),  # continuation
    ])
    j.llm.providers.insert(0, llm)
    state = run(st.run())
    steps = {s["name"]: s for s in state["steps"]}
    assert steps["create"]["status"] == "failed"  # the page was written, but the stray delete fails the step
    assert "has a script" in steps["create"]["detail"] and "Unexpected action denied: delete_path" in steps["create"]["detail"] and stray.exists()
    assert steps["browser"]["status"] == "skipped" and "Browser control is off" in steps["browser"]["detail"]
    assert steps["delete"]["status"] == "passed" and not st.folder.exists()
    assert state["running"] is False and state["leftover"] is False
    assert steps["delete"]["reply"] == "The folder is deleted." and not llm.script  # the continuations ran (no deadlock)
    assert (j.settings.data_dir / "selftest" / "last.json").is_file()
    audit = j.db.query("SELECT actor, tool, decision FROM audit_log WHERE actor='selftest'")
    assert {(a["tool"], a["decision"]) for a in audit} >= {("write_file", "approved"), ("delete_path", "denied"), ("delete_path", "approved")}
    j.db.close()


# -- file actions offered to the local model -------------------------------------------------------------------------------
@pytest.mark.parametrize("task, expected", [
    (r"Create the file C:\Users\me\JARVIS-selftest\hello.html with a small HTML page", {"write_file", "create_directory"}),
    ("make a file notes.txt on my desktop", {"write_file", "create_directory"}),
    (r"Delete the folder C:\Users\me\JARVIS-selftest-1", {"delete_path"}),
    ("rename report.docx to final.docx", {"move_path"}),
    ("write me a two line poem about rain", set()),  # no file named: never offer write_file
    ("delete my worries", set()),
    ("what is the capital of France?", set()),
])
def test_file_actions_are_offered_only_for_a_named_file(task, expected):
    from jarvis.agents import file_action_tools

    assert file_action_tools(task) == expected


def test_continuation_after_an_approval_still_offers_the_next_file_tool(home, tmp_path):
    """Found by the real self-test: after create_directory was approved, the continuation was not offered write_file."""
    from jarvis.llm.base import LLMRouter

    folder, page = home / "site", home / "site" / "hello.html"
    llm = ScriptedLLM([
        ("", [("create_directory", {"path": str(folder)})]),
        ("The folder is waiting for your approval.", []),
        ("", [("write_file", {"path": str(page), "content": "<script>1</script>"})]),  # continuation
        ("The page is waiting for your approval.", []),
        ("Done: hello.html is created.", []),  # continuation after the second approval
    ])
    llm.name = "openai_compat"
    j = make(home, tmp_path)
    j.llm = LLMRouter([llm, j.offline], backoff=0.01)
    j.db.set_setting("fast_path", False)

    async def scenario():
        r = await j.chat(f"Create the file {page} with a small HTML page", "c1")
        await j.executor.decide(r["pending_approvals"][0], True)
        await j.idle()
        ap = j.db.one("SELECT id FROM approvals WHERE status='pending' AND tool='write_file'")
        await j.executor.decide(ap["id"], True)
        await j.idle()

    run(scenario())
    continuation = llm.seen[2]
    assert "Continue the original task" in continuation["messages"][-1]["content"]
    assert "write_file" in continuation["tools"], continuation["tools"]
    assert page.read_text() == "<script>1</script>"
    j.db.close()


def test_two_decisions_together_continue_the_task_only_once(home, tmp_path):
    """Found by the self-test: deciding two approvals back to back started two continuations (the model ran twice)."""
    a, b = home / "a.txt", home / "b.txt"
    llm = ScriptedLLM([
        ("", [("delegate", {"agent": "computer", "task": "write two files"})]),
        ("", [("write_file", {"path": str(a), "content": "a"}), ("write_file", {"path": str(b), "content": "b"})]),
        ("Both files wait for approval.", []),
        ("I asked to write both files.", []),
        ("Both files are written.", []),  # the one continuation
        ("SECOND CONTINUATION", []),  # must never be used
    ])
    j = make(home, tmp_path, providers=[llm])

    async def scenario():
        r = await j.chat("write a.txt and b.txt in my home folder", "c1")
        ids = [x["id"] for x in j.db.query("SELECT id FROM approvals WHERE status='pending' ORDER BY created")]
        assert len(ids) == 2 and r["pending_approvals"]
        await j.executor.decide(ids[0], True)
        await j.executor.decide(ids[1], False)
        await j.idle()

    run(scenario())
    assert llm.script == [("SECOND CONTINUATION", [])]
    replies = [m["content"] for m in j.db.query("SELECT content FROM messages WHERE role='assistant' ORDER BY created")]
    assert replies[-1] == "Both files are written." and a.read_text() == "a" and not b.exists()
    j.db.close()
