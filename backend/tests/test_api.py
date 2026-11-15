import pytest
from fastapi.testclient import TestClient

from jarvis.api import create_app


@pytest.fixture
def client(jarvis):
    token = jarvis.settings.api_token()
    with TestClient(create_app(jarvis)) as c:
        c.token = token
        c.auth_headers = {"Authorization": f"Bearer {token}"}
        yield c


def test_api_requires_token_and_known_host(client):
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/status", headers={**client.auth_headers, "Host": "evil.example"}).status_code == 421
    assert client.get("/api/status", headers=client.auth_headers).json()["state"] == "idle"
    assert client.get("/api/auth?token=wrong", follow_redirects=False).status_code == 401
    r = client.get(f"/api/auth?token={client.token}&next=//evil.example", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/" and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()


def test_chat_streams_over_websocket_and_approval_round_trip(client, home):
    h = client.auth_headers
    with client.websocket_connect(f"/ws?token={client.token}") as ws:
        assert ws.receive_json()["type"] == "hello"
        r = client.post("/api/chat", json={"text": "organize my downloads folder", "wait": True, "source": "voice"}, headers=h).json()
        types, deltas = [], []
        while True:
            ev = ws.receive_json()
            if ev["type"] == "metrics":
                continue
            types.append(ev["type"])
            if ev["type"] == "chat.delta":
                deltas.append(ev["text"])
            if ev["type"] == "chat.done":
                break
        assert types[0] == "chat.start" and "approval.requested" in types and "agent.status" in types and "agent.message" in types
        assert "".join(deltas).strip() == r["text"].strip()  # streamed tokens equal the final message
    pending = client.get("/api/approvals", headers=h).json()
    assert len(pending) == 1 and pending[0]["tool"] == "organize_folder" and "invoice.pdf" in pending[0]["preview"]
    done = client.post(f"/api/approvals/{pending[0]['id']}/decide", json={"approve": True}, headers=h).json()
    assert done["status"] == "executed" and (home / "Downloads" / "Documents" / "invoice.pdf").exists()
    assert client.post(f"/api/approvals/{pending[0]['id']}/decide", json={"approve": True}, headers=h).status_code == 403
    conv = client.get(f"/api/conversations/{r['conversation_id']}", headers=h).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "event"]
    assert any(a["decision"] == "approved" for a in client.get("/api/audit", headers=h).json())


def test_ws_rejects_unauthenticated(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass


def test_settings_endpoints_enforce_policy(client, home):
    h = client.auth_headers
    assert client.put("/api/settings", json={"trusted_tools": ["delete_path"]}, headers=h).status_code == 403
    assert client.put("/api/settings", json={"trusted_tools": ["create_directory"]}, headers=h).json()["trusted_tools"] == ["create_directory"]
    assert client.put("/api/settings", json={"default_project": "/etc"}, headers=h).status_code == 403
    assert client.get("/api/files/read", params={"path": "/etc/passwd"}, headers=h).status_code == 403
    tools = {t["name"]: t for t in client.get("/api/tools", headers=h).json()}
    assert tools["delete_path"]["level"] == "confirm" and not tools["delete_path"]["trust_eligible"] and tools["system_stats"]["level"] == "read"


def test_dashboard_knowledge_and_workflow_endpoints(client, home):
    h = client.auth_headers
    (home / "doc.md").write_text("# Notes\nThe deploy pipeline uses blue green releases.\n")
    assert client.post("/api/knowledge/ingest", json={"path": str(home / "doc.md"), "space": "ops"}, headers=h).json()["status"] == "indexed"
    up = client.post("/api/knowledge/upload", files={"file": ("up.txt", b"Kubernetes schedules pods onto nodes.")}, data={"space": "ops"}, headers=h).json()
    assert up["status"] == "indexed"
    hits = client.post("/api/knowledge/search", json={"query": "blue green deploy", "space": "ops"}, headers=h).json()["results"]
    assert hits[0]["title"] == "doc.md"
    wf = client.post("/api/workflows", json={"name": "stats", "schedule": {"type": "manual"}, "steps": [{"tool": "system_stats"}]}, headers=h).json()
    assert client.post(f"/api/workflows/{wf['id']}/run", headers=h).json()["status"] == "succeeded"
    assert client.post("/api/workflows", json={"name": "x", "schedule": {"type": "cron", "expr": "bad"}, "steps": [{"tool": "system_stats"}]}, headers=h).status_code == 400
    d = client.get("/api/dashboard", headers=h).json()
    assert d["metrics"]["cpu"]["cores"] >= 1 and d["workflows"][0]["name"] == "stats" and d["status"]["llm"]["degraded"]
    assert len(client.get("/api/agents", headers=h).json()) == 7
    assert client.post("/api/voice/transcribe", files={"file": ("a.wav", b"x")}, headers=h).status_code == 501
