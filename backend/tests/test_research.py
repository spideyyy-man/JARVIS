import httpx
import pytest

from conftest import ScriptedLLM, make, run
from jarvis.security import SecurityError
from jarvis.tools.research import _check_public, verify_citations

DDG = """<html><body>
<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fagents-2026">Agent frameworks in 2026</a><a class="result__snippet">Survey of tool-using agents.</a></div>
<div class="result"><a class="result__a" href="https://example.net/mcp">Model Context Protocol</a><a class="result__snippet">An open protocol for tools.</a></div>
</body></html>"""


def web(request: httpx.Request) -> httpx.Response:
    if "duckduckgo" in request.url.host:
        return httpx.Response(200, text=DDG, headers={"content-type": "text/html"})
    if request.url.host == "example.org":
        return httpx.Response(200, text="<html><title>Agents 2026</title><body><main><p>Agents now plan, call tools and verify their work.</p></main><script>evil()</script></body></html>", headers={"content-type": "text/html"})
    if request.url.host == "example.net":
        return httpx.Response(200, text="<html><title>MCP</title><body><p>MCP standardises tool access.</p></body></html>", headers={"content-type": "text/html"})
    if request.url.host == "redirector.example":
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8765/api/status"})
    return httpx.Response(404)


def test_research_searches_reads_and_cites_only_retrieved_sources(jarvis):
    jarvis.http_transport = httpx.MockTransport(web)
    r = run(jarvis.chat("research the latest developments in AI agents"))
    assert "https://example.org/agents-2026" in r["text"] and "Agents now plan, call tools" in r["text"] and "evil()" not in r["text"]
    task = jarvis.db.one("SELECT * FROM tasks WHERE agent='research'")
    urls = {s["url"] for s in jarvis.db.query("SELECT url FROM sources WHERE task_id=?", (task["id"],))}
    assert {"https://example.org/agents-2026", "https://example.net/mcp"} <= urls
    assert task["evaluation"]["citations"]["unverified"] == [] and task["evaluation"]["ok"]


def test_fabricated_citation_is_flagged(home, tmp_path):
    llm = ScriptedLLM([("", [("web_search", {"query": "agents"})]),
                       ("Agents improved a lot (https://example.org/agents-2026) and also per https://made-up.example/paper.", [])])
    j = make(home, tmp_path, providers=[llm])
    j.http_transport = httpx.MockTransport(web)
    res = run(j.agents.run("research", "what's new in agents", conversation_id=None))
    ev = j.db.one("SELECT evaluation FROM tasks WHERE id=?", (res.task_id,))["evaluation"]
    assert ev["citations"]["unverified"] == ["https://made-up.example/paper"] and not ev["ok"]
    assert "unverified" in res.text and "made-up.example" in res.text


def test_search_outage_is_reported_not_papered_over(jarvis):
    jarvis.http_transport = httpx.MockTransport(lambda req: httpx.Response(503))
    r = run(jarvis.chat("research the history of the transistor"))
    assert "no" in r["text"].lower() and "web findings" in r["text"] and r["errors"]
    assert jarvis.db.one("SELECT COUNT(*) AS n FROM sources")["n"] == 0


def test_ssrf_guard(jarvis, ctx):
    for bad in ("http://127.0.0.1:8765/api/status", "http://localhost/", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd", "http://10.0.0.5/"):
        with pytest.raises(SecurityError):
            _check_public(bad)
    jarvis.http_transport = httpx.MockTransport(web)
    res = run(jarvis.executor.call("fetch_url", {"url": "https://redirector.example/x"}, ctx))  # redirect into loopback
    assert not res.ok and "security policy" in res.error


def test_verify_citations_normalises_urls(jarvis):
    jarvis.db.insert("sources", {"id": "s1", "task_id": "t", "url": "https://www.example.com/a/", "title": "", "kind": "page", "snippet": "", "fetched": 0})
    v = verify_citations(jarvis.db, "t", "See http://example.com/a. And https://other.example/b")
    assert v["verified"] == ["http://example.com/a"] and v["unverified"] == ["https://other.example/b"]
