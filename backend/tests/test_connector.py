"""The JARVIS Connector browser extension, JARVIS's side: "fill this form" in the owner's own browser tab, from their saved
details, only after they approve; never passwords, OTPs, card, bank or ID numbers; never submitting on its own."""
import asyncio
from pathlib import Path

import pytest

from conftest import make, run
from jarvis.connector import ConnectorError, ConnectorHub
from jarvis.llm.offline import Env, match_rules
from jarvis.security import SecurityError
from jarvis.tools.tab import detail_key, iso_date, parse_answers, pick_option, propose, questions_of, with_derived

ENV = Env(Path("/home/u"), lambda: "/proj", lambda: [])

FORM = [  # what the extension reads from a typical college registration form
    {"id": "j1", "kind": "text", "label": "Full Name *", "name": "fullname", "required": True, "options": [], "value": ""},
    {"id": "j2", "kind": "email", "label": "Email address", "name": "email", "autocomplete": "email", "required": True, "options": [], "value": ""},
    {"id": "j3", "kind": "tel", "label": "Mobile Number", "name": "mobile", "required": True, "options": [], "value": ""},
    {"id": "j4", "kind": "date", "label": "Date of Birth", "name": "dob", "required": False, "options": [], "value": ""},
    {"id": "j5", "kind": "select", "label": "Gender", "name": "gender", "required": False, "options": ["Select", "Male", "Female", "Other"], "value": ""},
    {"id": "j6", "kind": "password", "label": "Password", "name": "pw", "required": True, "options": [], "value": "", "sensitive": True},
    {"id": "j7", "kind": "text", "label": "Card number", "name": "cc", "required": False, "options": [], "value": ""},
    {"id": "j8", "kind": "text", "label": "Name of the College", "name": "college", "required": True, "options": [], "value": ""},
    {"id": "j9", "kind": "text", "label": "Roll Number", "name": "roll", "required": True, "options": [], "value": ""},
    {"id": "j10", "kind": "aria-radio", "label": "Year of study", "name": "", "required": False, "options": ["1st year", "2nd year", "3rd year"], "value": ""},
    {"id": "j11", "kind": "text", "label": "Father's name", "name": "", "required": False, "options": [], "value": ""},
]
DETAILS = {"full_name": "Ravi Kumar", "email": "ravi@example.com", "phone": "9876543210", "date_of_birth": "2007-08-15", "gender": "male",
           "college": "PSG College of Technology", "year_of_study": "2nd year"}


def test_form_fields_are_matched_to_the_saved_details():
    proposal, missing = propose(FORM, DETAILS)
    got = {p["id"]: p["value"] for p in proposal}
    assert got == {"j1": "Ravi Kumar", "j2": "ravi@example.com", "j3": "9876543210", "j4": "2007-08-15", "j5": "Male",
                   "j8": "PSG College of Technology", "j10": "2nd year"}
    assert "Roll Number" in missing and "Password" not in missing and "Card number" not in missing  # never asked for, never filled


@pytest.mark.parametrize("label,key", [("First Name", "first_name"), ("Name", "full_name"), ("E-mail ID", "email"), ("WhatsApp Number", "phone"),
                                       ("Name of the father", "father_name"), ("Pin Code", "pincode"), ("Email address", "email"),
                                       ("Password", None), ("OTP", None), ("Aadhaar number", None), ("Account number", None), ("Favourite colour", None)])
def test_label_to_detail(label, key):
    assert detail_key(label) == key


def test_dates_options_and_derived_details():
    assert iso_date("15/08/2007") == iso_date("15-08-2007") == iso_date("15 Aug 2007") == "2007-08-15" and iso_date("soon") is None
    assert pick_option("m", ["Male", "Female"]) == "Male" and pick_option("tamil nadu", ["Kerala", "Tamil Nadu"]) == "Tamil Nadu"
    d = with_derived({"full_name": "Ravi Kumar S", "date_of_birth": "2007-08-15"})
    assert d["first_name"] == "Ravi" and d["last_name"] == "Kumar S" and int(d["age"]) >= 18


def test_fill_this_form_routes_instantly_and_details_are_said_naturally():
    assert match_rules("Jarvis, fill this form", ENV)[1] == ("tab_form", {})
    assert match_rules("please fill in the application", ENV)[1] == ("tab_form", {})
    assert match_rules("இந்த ஃபார்ம் ஃபில் பண்ணு", ENV)[1] == ("tab_form", {})
    assert match_rules("my email is ravi@example.com", ENV)[1] == ("set_my_details", {"details": {"email": "ravi@example.com"}})
    assert match_rules("my city is Chennai", ENV)[1][0] == "set_home_city"  # the weather rule keeps its phrase
    assert match_rules("my password is hunter2", ENV) is None


def test_details_are_saved_but_never_secrets(jarvis):
    from jarvis.tools.registry import ToolContext
    from jarvis.tools.tab import my_details, set_my_details

    ctx = ToolContext(jarvis, agent="user", actor="chat")
    r = set_my_details(ctx, {"email": "ravi@example.com", "date of birth": "15/08/2007", "OTP": "1234"})
    assert r["saved"] == {"email": "ravi@example.com", "date of birth": "2007-08-15"} and r["refused"] == ["OTP"]
    with pytest.raises(SecurityError):
        set_my_details(ctx, {"password": "hunter2"})
    with pytest.raises(ValueError):
        set_my_details(ctx, {"email": "not an email"})
    assert my_details(ctx)["details"]["email"] == "ravi@example.com"


def test_auto_memory_never_keeps_a_password(jarvis):
    assert jarvis.auto_memory("my password is hunter2 for the portal") == []
    assert jarvis.auto_memory("My favourite colour is blue.") != []


class FakeSock:
    def __init__(self, hub, answers):
        self.hub, self.answers, self.sent = hub, answers, []

    async def send_json(self, msg):
        self.sent.append(msg)
        answer = self.answers.get(msg["cmd"])
        reply = {"id": msg["id"], **(answer(msg["args"]) if callable(answer) else {"ok": True, "result": answer})}
        asyncio.get_running_loop().call_soon(self.hub.deliver, reply)


def test_hub_sends_one_command_and_waits_for_its_answer():
    async def go():
        hub = ConnectorHub()
        with pytest.raises(ConnectorError, match="isn.t connected"):
            await hub.call("read_page")
        sock = FakeSock(hub, {"read_page": {"title": "Form", "url": "https://x", "text": "hello"}, "fill": lambda a: {"ok": False, "error": "boom"}})
        hub.attach(sock, {"browser": "Edge", "version": "1.0.0"})
        assert (await hub.call("read_page"))["title"] == "Form" and hub.status()["connected"]
        with pytest.raises(ConnectorError, match="boom"):
            await hub.call("fill", {"values": []})
        hub.detach(sock)
        assert not hub.connected()
    run(go())


def _jarvis_with_browser(home, tmp_path, form, fills):
    j = make(home, tmp_path)
    j.db.set_setting("profile", DETAILS)

    async def call(cmd, args=None, timeout=None):
        if cmd == "read_form":
            return {"title": "College registration", "url": "https://forms.example/reg", "fields": form}
        if cmd == "fill":
            fills.append(args["values"])
            return {"filled": [v["id"] for v in args["values"]], "skipped": []}
        raise AssertionError(cmd)
    j.connector.call = call
    return j


def test_fill_this_form_shows_every_field_then_fills_only_after_yes(home, tmp_path):
    fills = []
    j = _jarvis_with_browser(home, tmp_path, FORM, fills)
    r = run(j.chat("Jarvis, fill this form"))
    text = r["text"]
    assert "College registration" in text and "I can fill 7" in text and "Roll Number" in text  # what it will and won't do
    assert "- Email address: ravi@example.com" in text and "Password" not in text.split("I can fill")[1].split("I don't know")[0]
    assert "won't press Submit" in text and fills == [] and r["pending_approvals"]  # nothing touched yet
    done = run(j.chat("yes", r["conversation_id"]))
    assert done["text"] == "Approved and done." and len(fills) == 1 and len(fills[0]) == 7
    assert all(v["id"] not in ("j6", "j7") for v in fills[0])  # password and card never sent to the page
    j.db.close()


def test_a_form_with_nothing_known_asks_for_details(home, tmp_path):
    j = _jarvis_with_browser(home, tmp_path, [FORM[8]], [])
    r = run(j.chat("fill this form"))
    assert "None of your saved details fit it yet" in r["text"] and "Roll Number" in r["text"] and not r["pending_approvals"]
    j.db.close()


def test_a_fill_with_a_sensitive_field_is_refused_before_it_reaches_the_page(home, tmp_path):
    fills = []
    j = _jarvis_with_browser(home, tmp_path, FORM, fills)
    from jarvis.tools.registry import ToolContext

    res = run(j.executor.call("tab_fill", {"values": [{"id": "j6", "label": "Password", "value": "x"}]}, ToolContext(j, agent="orchestrator", actor="chat")))
    assert not res.ok and "never fills" in (res.error or "") and fills == []
    j.db.close()


def test_connector_socket_needs_an_extension_origin_and_the_key(jarvis):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from jarvis.api import create_app

    key = jarvis.settings.connector_key()
    with TestClient(create_app(jarvis)) as c:
        h = {"Authorization": f"Bearer {jarvis.settings.api_token()}"}
        assert c.get("/api/connector", headers=h).json()["key"] == key and c.get("/api/connector").status_code == 401
        for origin, k in (("https://evil.example", key), ("chrome-extension://abc", "wrong")):
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect(f"/ws/connector?key={k}", headers={"origin": origin}) as ws:
                    ws.receive_json()
        with c.websocket_connect(f"/ws/connector?key={key}", headers={"origin": "chrome-extension://abc"}) as ws:
            ws.send_json({"type": "hello", "browser": "Edge", "version": "1.0.0"})
            for _ in range(50):
                if c.get("/api/connector", headers=h).json()["connected"]:
                    break
            assert c.get("/api/connector", headers=h).json()["version"] == "1.0.0"
        # details from Settings: secrets refused, a cleared box forgets
        r = c.put("/api/details", headers=h, json={"details": {"email": "ravi@example.com", "phone": "9876543210"}}).json()
        assert r["details"]["email"] == "ravi@example.com"
        r = c.put("/api/details", headers=h, json={"details": {"phone": ""}}).json()
        assert "phone" not in r["details"]
        assert c.put("/api/details", headers=h, json={"details": {"password": "x"}}).status_code == 403


# -- pairing without copying a key, and the owner's own phrasings (2026-11-22: "it did not!") -------------------------------
def _pair(jarvis, approve: bool):
    from fastapi.testclient import TestClient

    from jarvis.api import create_app

    with TestClient(create_app(jarvis)) as c:
        h = {"Authorization": f"Bearer {jarvis.settings.api_token()}"}
        with c.websocket_connect("/ws/connector?pair=1", headers={"origin": "chrome-extension://abc"}) as ws:
            ws.send_json({"type": "hello", "browser": "Mozilla/5.0 ... Edg/140.0", "version": "1.0.0"})
            ap = None
            for _ in range(100):
                aps = [a for a in c.get("/api/approvals", headers=h).json() if a["tool"] == "connector_pair"]
                if aps:
                    ap = aps[0]
                    break
            assert ap and "Edge" in ap["preview"] and "each fill still asks you first" in ap["preview"]
            assert c.post(f"/api/approvals/{ap['id']}/decide", headers=h, json={"approve": approve}).status_code == 200
            msg = ws.receive_json()
        return msg, c.get("/api/connector", headers=h).json()


def test_an_extension_pairs_once_the_user_approves(jarvis):
    msg, status = _pair(jarvis, True)
    assert msg == {"type": "paired", "key": jarvis.settings.connector_key()} and status["last_attempt"]["result"] == "paired"


def test_a_denied_pairing_gives_no_key(jarvis):
    msg, status = _pair(jarvis, False)
    assert msg == {"type": "denied"} and status["last_attempt"]["result"] == "pairing denied"


def test_refused_connections_are_recorded_for_settings(jarvis):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from jarvis.api import create_app

    with TestClient(create_app(jarvis)) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws/connector?key=old", headers={"origin": "chrome-extension://abc"}) as ws:
                ws.receive_json()
        st = c.get("/api/connector", headers={"Authorization": f"Bearer {jarvis.settings.api_token()}"}).json()
        assert "wrong key" in st["last_attempt"]["result"]


@pytest.mark.parametrize("text", ["fill the details in the browser", "fill up this page", "fill my details here", "do this survey for me",
                                  "fill this form", "Jarvis, fill the form."])
def test_the_owners_own_ways_of_asking_reach_the_form(text):
    assert match_rules(text, ENV)[1] == ("tab_form", {})


@pytest.mark.parametrize("text", ["i want you to complete the test in my current window", "Can you complete the questions in the browser?",
                                  "answer the questions on this page", "solve this quiz", "fill in the sample test questions",
                                  "Jarvis, answer these MCQs"])
def test_questions_and_tests_go_to_the_answerer(text):
    assert match_rules(text, ENV)[1] == ("tab_answer", {})


@pytest.mark.parametrize("text", ["summarise this page", "Summarize the content in my current tab in a browser", "give me the gist of this article",
                                  "what's this page about", "can you summarise the website I'm on", "tl;dr this tab"])
def test_summarise_my_tab(text):
    assert match_rules(text, ENV)[1] == ("tab_summary", {})


def test_other_summaries_keep_their_own_tools():
    assert match_rules("summarize https://example.com/page", ENV)[1][0] == "fetch_url"
    assert match_rules("summarise what I just copied", ENV)[1][0] == "clipboard_read"
    assert match_rules("give me a summary of my day", ENV)[1][0] == "daily_summary"


@pytest.mark.parametrize("text", ["what is a form factor", "write me a poem", "complete the sentence: the sky is", "what is the capital of France"])
def test_other_requests_are_left_alone(text):
    hit = match_rules(text, ENV)
    assert not hit or hit[1][0] != "tab_form"


def test_can_you_see_my_screen_reads_it():
    assert match_rules("can you see my current screen", ENV)[1] == ("screen_read", {})


QUIZ = [  # a practice quiz: two details, three questions (options, written, a detail JARVIS doesn't know)
    {"id": "q0", "kind": "text", "label": "Name", "name": "name", "required": True, "options": [], "value": ""},
    {"id": "q1", "kind": "radio", "label": "What is the capital of France?", "name": "q1", "required": True,
     "options": ["Berlin", "Paris", "Madrid", "Rome"], "value": ""},
    {"id": "q2", "kind": "textarea", "label": "Explain in one line why the sky looks blue", "name": "q2", "required": False, "options": [], "value": ""},
    {"id": "q3", "kind": "aria-radio", "label": "2 + 2 = ?", "name": "", "required": False, "options": ["3", "4", "5"], "value": ""},
    {"id": "q4", "kind": "text", "label": "Roll Number", "name": "roll", "required": True, "options": [], "value": ""},
    {"id": "q5", "kind": "text", "label": "Enter the OTP sent to your phone number", "name": "otp", "required": True, "options": [], "value": ""},
]


def test_questions_are_told_apart_from_details():
    qs = questions_of(QUIZ, {"q0"})
    assert [q["id"] for q in qs] == ["q1", "q2", "q3"]  # not the name (taken), the roll number (a detail) or the OTP (secret)


def test_model_answers_become_a_checked_proposal():
    qs = questions_of(QUIZ, set())
    p = parse_answers('Sure! {"1": "B) Paris", "2": "Air scatters blue light more.", "3": "4", "9": "x"}', qs)
    assert [(a["id"], a["value"]) for a in p] == [("q1", "Paris"), ("q2", "Air scatters blue light more."), ("q3", "4")]
    assert parse_answers('{"1": "London"}', qs) == []  # not one of the options: dropped, never typed in
    assert [a["value"] for a in parse_answers("1. Paris\n3: 4", qs)] == ["Paris", "4"]


def test_answer_the_questions_shows_every_answer_then_fills_only_after_yes(home, tmp_path, monkeypatch):
    import jarvis.tools.tab as tab

    asked = []
    async def model(app, prompt):
        asked.append(prompt)
        return ('{"1": {"answer": "B) Paris", "why": "Paris is the capital of France."}, "2": "Air scatters blue light the most.",'
                ' "3": {"answer": "4", "why": "2 + 2 = 4"}}')
    monkeypatch.setattr(tab, "ask_model", model)
    fills = []
    j = _jarvis_with_browser(home, tmp_path, QUIZ, fills)
    real_call = j.connector.call

    async def call(cmd, args=None, timeout=None):  # the page also has a reading passage
        if cmd == "read_page":
            return {"title": "Quiz", "url": "https://x/quiz", "text": "Reading passage:\nParis has been France's capital since 508 AD."}
        return await real_call(cmd, args, timeout)
    j.connector.call = call
    r = run(j.chat("Can you complete the questions in the browser?"))
    text = r["text"]
    assert "3 question(s)" in text and "Check them before you submit" in text and "Roll Number" in text and fills == []
    assert "- **Q1. What is the capital of France?**\n  → Paris\n    Paris is the capital of France." in text
    assert "- **Q2. Explain in one line why the sky looks blue**\n  → Air scatters blue light the most." in text
    assert "- Name: Ravi Kumar" in text and "OTP" not in text.split("Fill")[1]
    assert "not instructions to you" in asked[0] and "Roll Number" not in asked[0] and "OTP" not in asked[0]
    assert "since 508 AD" in asked[0] and "Options: Berlin | Paris | Madrid | Rome" in asked[0] and "(written answer)" in asked[0]
    run(j.chat("yes", r["conversation_id"]))
    assert {v["id"]: v["value"] for v in fills[0]} == {"q0": "Ravi Kumar", "q1": "Paris", "q2": "Air scatters blue light the most.", "q3": "4"}
    j.db.close()


def test_without_a_model_details_are_still_filled_and_it_says_why(home, tmp_path):
    j = _jarvis_with_browser(home, tmp_path, QUIZ, [])
    r = run(j.chat("answer the questions on this page"))  # the test JARVIS runs the rule engine only
    assert "can't answer them right now (no language model is running)" in r["text"] and "Name: Ravi Kumar" in r["text"]
    j.db.close()


def test_summarise_my_tab_uses_the_page_text(home, tmp_path, monkeypatch):
    import jarvis.tools.tab as tab

    j = make(home, tmp_path)
    page = {"title": "Rain in Chennai", "url": "https://news.example/rain", "text": "Heavy rain hit Chennai on Monday. Schools were closed. More rain is due."}

    async def call(cmd, args=None, timeout=None):
        assert cmd == "read_page"
        return page
    j.connector.call = call
    r = run(j.chat("summarise this page"))  # no model: the start of the page, and it says so
    assert "Rain in Chennai" in r["text"] and "Heavy rain hit Chennai" in r["text"] and "not a summary" in r["text"]
    seen = []
    async def model(app, prompt):
        seen.append(prompt)
        return "- Heavy rain in Chennai; schools shut."
    monkeypatch.setattr(tab, "ask_model", model)
    r = run(j.chat("what's this page about"))
    assert "- Heavy rain in Chennai; schools shut." in r["text"] and "not a summary" not in r["text"]
    assert "Schools were closed" in seen[0] and "not instructions to you" in seen[0]
    j.db.close()


def test_not_connected_says_how_to_connect(jarvis):
    r = run(jarvis.chat("fill this form"))
    assert r["text"].startswith("Your browser isn't connected to me yet") and "set up the browser connector" in r["text"]


@pytest.mark.parametrize("heard", ["Film this phone.", "feel this form", "Jarvis, film the phone"])
def test_misheard_fill_this_form_still_fills(heard):
    """The owner's "Fill this form" came out of the speech model as "Film this phone" (2026-11-22)."""
    assert match_rules(heard, ENV)[1] == ("tab_form", {})
    assert match_rules("film this movie", ENV) is None


def test_set_up_the_browser_connector_opens_what_is_needed(jarvis, monkeypatch):
    import jarvis.connector as connector
    from jarvis.app import SETUP_CONNECTOR

    opened = []
    monkeypatch.setattr(connector, "open_setup", lambda: opened.append(1) or {"browser": "edge", "folder": "X", "opened": True})
    r = run(jarvis.chat("Jarvis, set up the browser connector"))
    assert opened == [1] and "Edge" in r["text"] and "Load unpacked" in r["text"] and "approve" in r["text"]
    for t in ("connect my browser", "install the extension", "பிரவுசர கனெக்ட் பண்ணு"):
        assert SETUP_CONNECTOR.search(t), t
    for t in ("connect to wifi", "set up a timer", "open the browser"):
        assert not SETUP_CONNECTOR.search(t), t


def test_browser_access_questions_get_the_real_answer(jarvis):
    """"Now do you have my browser settings access?" got "I don't have access" while Chrome was connected (2026-11-22)."""
    from jarvis.app import BROWSER_ACCESS

    asks = ("Now do you have my browser settings access?", "Do you have access to my browser?", "are you connected to my chrome",
            "Jarvis, is my browser connected?", "can you control my browser tabs?", "is the connector connected")
    for t in asks:
        assert BROWSER_ACCESS.search(t), t
    for t in ("open chrome", "can you open youtube in chrome", "can you see my screen", "connect my browser",
              "search the web for browser access settings", "do you have access to my files"):
        assert not BROWSER_ACCESS.search(t), t
    r = run(jarvis.chat("Now do you have my browser settings access?"))
    assert r["text"].startswith("Not yet") and "set up the browser connector" in r["text"]

    class Sock:
        pass
    jarvis.connector.attach(Sock(), {"browser": "Mozilla/5.0 (Windows NT 10.0) Chrome/158.0.0.0 Safari/537.36", "version": "1.0.1"})
    r = run(jarvis.chat("Do you have access to my browser?"))
    assert r["text"].startswith("Yes - I'm connected to your Chrome") and "fill this form" in r["text"]


# -- the popup's "Fill this form" button (2026-11-22: the owner's spoken requests never reached JARVIS) ----------------------
def test_popup_button_shows_the_fields_then_fills_only_after_its_own_fill_click(home, tmp_path):
    from jarvis.security import SecurityError

    fills = []
    j = _jarvis_with_browser(home, tmp_path, FORM, fills)
    r = run(j.popup_fill())
    assert r["approval"] and "- Email address: ravi@example.com" in r["text"] and fills == []
    with pytest.raises(SecurityError):  # an id the button did not start is not accepted
        run(j.popup_decide("ap_somethingelse", True))
    done = run(j.popup_decide(r["approval"], True))
    assert done["filled"] == 7 and "press Submit yourself" in done["text"] and len(fills) == 1
    assert all(v["id"] not in ("j6", "j7") for v in fills[0])
    with pytest.raises(SecurityError):  # once only
        run(j.popup_decide(r["approval"], True))
    r2 = run(j.popup_fill())
    assert run(j.popup_decide(r2["approval"], False))["text"].startswith("OK, I left the form") and len(fills) == 1
    j.db.close()


def test_a_fill_asked_for_by_voice_cannot_be_approved_from_the_popup(home, tmp_path):
    from jarvis.security import SecurityError

    fills = []
    j = _jarvis_with_browser(home, tmp_path, FORM, fills)
    aid = [a for a in run(j.chat("fill this form"))["pending_approvals"]][0]
    with pytest.raises(SecurityError):
        run(j.popup_decide(aid, True))
    assert fills == [] and j.db.one("SELECT status FROM approvals WHERE id=?", (aid,))["status"] == "pending"
    j.db.close()


def test_popup_requests_travel_over_the_extension_socket(home, tmp_path):
    from fastapi.testclient import TestClient

    from jarvis.api import create_app

    j = make(home, tmp_path)
    j.db.set_setting("profile", DETAILS)
    key = j.settings.connector_key()
    with TestClient(create_app(j)) as c:
        with c.websocket_connect(f"/ws/connector?key={key}", headers={"origin": "chrome-extension://abc"}) as ws:
            ws.send_json({"type": "hello", "browser": "Chrome/158", "version": "1.0.2"})

            def until_result(rid):  # play the extension: answer the page commands, return the popup's answer
                while True:
                    m = ws.receive_json()
                    if m.get("type") == "popup_result":
                        assert m["rid"] == rid
                        return m
                    res = {"read_form": {"title": "Reg", "url": "https://x/reg", "fields": FORM},
                           "fill": {"filled": ["j1", "j2"], "skipped": []}}[m["cmd"]]
                    ws.send_json({"id": m["id"], "ok": True, "result": res})

            ws.send_json({"type": "popup", "rid": 1, "action": "fill"})
            r = until_result(1)
            assert r["ok"] and r["approval"] and "Ravi Kumar" in r["text"]
            ws.send_json({"type": "popup", "rid": 2, "action": "decide", "approval": r["approval"], "approve": True})
            d = until_result(2)
            assert d["ok"] and d["text"].startswith("Filled 2 fields")
            ws.send_json({"type": "popup", "rid": 3, "action": "decide", "approval": "ap_x", "approve": True})
            assert until_result(3)["ok"] is False
    j.db.close()


# -- several browsers at once (2026-11-22: the quiz was in Chrome profile "A", the extension only in profile "H") ------------
def test_commands_go_to_the_browser_used_last_and_a_fill_to_the_one_that_was_read():
    async def go():
        hub = ConnectorHub()
        a = FakeSock(hub, {"read_page": {"title": "A page"}, "read_form": {"title": "A form", "fields": []}, "fill": {"filled": ["a"]}})
        b = FakeSock(hub, {"read_page": {"title": "B page"}, "read_form": {"title": "B form", "fields": []}, "fill": {"filled": ["b"]}})
        hub.attach(a, {"browser": "Chrome A", "version": "1.2.0"})
        await asyncio.sleep(0.01)
        hub.attach(b, {"browser": "Chrome B", "version": "1.2.0"})
        assert (await hub.call("read_page"))["title"] == "B page" and hub.status()["browsers"] == 2  # newest first
        await asyncio.sleep(0.01)
        hub.used(a)  # the owner clicked into browser A
        assert (await hub.call("read_form"))["title"] == "A form"
        await asyncio.sleep(0.01)
        hub.used(b)  # ...then looked at B before saying "yes": the fill still goes to A, whose form was shown
        assert (await hub.call("fill", {"values": []}))["filled"] == ["a"]
        assert (await hub.call("read_page"))["title"] == "B page"
        hub.detach(b)
        assert (await hub.call("read_page"))["title"] == "A page" and hub.connected()
        hub.detach(a)
        assert not hub.connected()
    run(go())


def test_a_wrong_key_is_told_with_4401_so_the_extension_pairs_again(jarvis):
    """Closed before accept, the browser only saw 1006 and kept its bad key forever (2026-11-22)."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from jarvis.api import create_app

    with TestClient(create_app(jarvis)) as c:
        with c.websocket_connect("/ws/connector?key=old", headers={"origin": "chrome-extension://abc"}) as ws:
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_json()
        assert e.value.code == 4401


def test_summaries_come_out_in_one_shape():
    from jarvis.tools.tab import tidy_summary

    raw = ("Here is a summary of the page:\n\nThe city opened a new metro line.\n\n* 18 km, 14 stations.\n• Trains every 6 minutes.\n"
           "1. Fares from 10 rupees.\n- Students get 20% off.\n- 150,000 riders a day expected.\n- Extra sixth point.")
    assert tidy_summary(raw) == ("The city opened a new metro line.\n- 18 km, 14 stations.\n- Trains every 6 minutes.\n"
                                 "- Fares from 10 rupees.\n- Students get 20% off.\n- 150,000 riders a day expected.")


def test_custom_answer_boxes_count_as_questions():
    choice = {"id": "0:j9", "kind": "choice", "label": "Which hazard is described in paragraph 2?", "name": "",
              "options": ["A Faulty wiring", "B Human negligence", "C Flammable materials"], "required": False, "value": ""}
    qs = questions_of([choice], set())
    assert qs == [choice]
    assert parse_answers('{"1": {"answer": "Faulty wiring", "why": "Paragraph 2 is about loose wires."}}', qs)[0]["value"] == "A Faulty wiring"


def test_fill_in_the_blank_pages_get_the_phrase_lists_and_blank_numbers():
    """Pearson "ready reckoner": 5 boxes with sentence starts, the phrase lists for Blank 1-5 below them (2026-11-22)."""
    from jarvis.tools.tab import answer_prompt, around_questions

    boxes = [{"id": f"0:j{i}", "kind": "textarea", "label": stem, "options": [], "value": ""}
             for i, stem in enumerate(["Fire can break out due to overheating of computers if", "Combustible materials stacked together is"], 1)]
    page = ("Passage about fire safety. " * 200 + "\nFire can break out due to overheating of computers if\nCombustible materials stacked "
            "together is\nList of phrases for Blank 1:\na. Proper reviews are not conducted\n" + "filler " * 100
            + "\nList of phrases for Blank 2:\nb. a fire hazard\n")
    window = around_questions(page, boxes, 2000)
    assert len(window) <= 2000 and "List of phrases for Blank 1" in window and "Fire can break out" in window
    prompt = answer_prompt("Writing", boxes, window)
    assert "1. Blank 1: Fire can break out due to overheating of computers if ____" in prompt
    assert "2. Blank 2: Combustible materials stacked together is ____" in prompt and "without its letter" in prompt
    qs = questions_of(boxes, set())
    assert parse_answers('{"1": {"answer": "c. Proper reviews are not conducted", "why": "x"}}', qs)[0]["value"] == "Proper reviews are not conducted"


def test_a_reply_cut_off_by_the_length_cap_keeps_its_complete_answers():
    qs = questions_of([{"id": f"g{i}", "kind": "radio", "label": f"{i}. Question number {i} here?", "options": ["yes", "no"], "value": ""}
                       for i in range(1, 4)], set())
    cut = '{"1": {"answer": "yes", "why": "because"}, "2": {"answer": "no", "why": "it says \\"no\\""}, "3": {"answer": "ye'
    got = parse_answers(cut, qs)
    assert [(a["id"], a["value"], a["note"]) for a in got] == [("g1", "yes", "because"), ("g2", "no", 'it says "no"')]
