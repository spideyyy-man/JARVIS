"""The JARVIS Connector browser extension, JARVIS's side: "fill this form" in the owner's own browser tab, from their saved
details, only after they approve; never passwords, OTPs, card, bank or ID numbers; never submitting on its own."""
import asyncio
from pathlib import Path

import pytest

from conftest import make, run
from jarvis.connector import ConnectorError, ConnectorHub
from jarvis.llm.offline import Env, match_rules
from jarvis.security import SecurityError
from jarvis.tools.tab import detail_key, iso_date, pick_option, propose, with_derived

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
        with pytest.raises(ConnectorError, match="not connected"):
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
