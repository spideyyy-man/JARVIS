import docx
from reportlab.pdfgen import canvas

from conftest import run
from jarvis.knowledge import chunk_sections, extract_text

RAFT = """# Raft consensus

## Leader election
Raft elects a single leader per term. A follower becomes a candidate when its election timeout
expires without hearing from a leader, increments its term and requests votes from its peers.
A candidate that receives votes from a majority of the cluster becomes the leader.

## Log replication
The leader appends client commands to its log and replicates them to followers with AppendEntries
RPCs. An entry is committed once it is stored on a majority of servers.
"""


def make_docs(home):
    (home / "kb").mkdir()
    (home / "kb" / "raft.md").write_text(RAFT)
    (home / "kb" / "cooking.txt").write_text("To make sourdough bread you need a starter, flour, water and salt. Ferment overnight and bake in a hot dutch oven.")
    c = canvas.Canvas(str(home / "kb" / "vectors.pdf"))
    c.drawString(72, 720, "Vector databases store embeddings and answer nearest neighbour queries.")
    c.showPage()
    c.drawString(72, 720, "HNSW is a graph based index that trades memory for fast approximate search.")
    c.save()
    d = docx.Document()
    d.add_heading("Caching strategy", level=1)
    d.add_paragraph("Write-through caching updates the cache and the database in the same operation, keeping them consistent.")
    d.save(str(home / "kb" / "caching.docx"))
    return home / "kb"


def test_extractors_keep_locators(home):
    kb = make_docs(home)
    assert [loc for loc, _ in extract_text(kb / "vectors.pdf")] == ["p.1", "p.2"]
    assert extract_text(kb / "caching.docx")[0][0] == "Caching strategy"
    assert "Leader election" in [loc for loc, _ in extract_text(kb / "raft.md")]
    assert all(len(t) <= 1100 for _l, t in chunk_sections([("", "word " * 2000)]))


def test_ingest_all_formats_and_retrieve_with_sources(jarvis, ctx, home):
    kb = make_docs(home)
    res = run(jarvis.executor.call("kb_ingest", {"path": str(kb), "space": "tech"}, ctx))
    assert res.ok and res.data["indexed"] == 4
    assert run(jarvis.executor.call("kb_ingest", {"path": str(kb), "space": "tech"}, ctx)).data["new_or_updated"] == 0  # idempotent

    top = jarvis.kb.search("how does a raft node become leader", "tech")[0]
    assert top["title"] == "raft.md" and top["locator"] == "Leader election"
    top = jarvis.kb.search("graph index for approximate nearest neighbour search")[0]
    assert top["title"] == "vectors.pdf" and top["locator"] == "p.2"
    assert jarvis.kb.search("keeping cache and database consistent")[0]["title"] == "caching.docx"
    assert jarvis.kb.search("how to bake bread")[0]["title"] == "cooking.txt"
    assert jarvis.kb.search("raft leader", space="empty-space") == []  # spaces are isolated


def test_rag_answer_cites_indexed_documents(jarvis, home):
    kb = make_docs(home)
    jarvis.kb.ingest_directory(kb, "tech", jarvis.guard)
    r = run(jarvis.chat("Explain how leader election works according to my indexed documents"))
    assert "majority" in r["text"] and "[1]" in r["text"] and "raft.md" in r["text"] and "Leader election" in r["text"]
    r = run(jarvis.chat("What do my documents say about quantum chromodynamics lattice gauge?"))
    assert "raft.md" not in r["text"] or "Nothing in your indexed documents" in r["text"]


def test_memory_and_conversation_search(jarvis):
    run(jarvis.chat("remember that I prefer TypeScript over JavaScript for frontends", "c1"))
    assert jarvis.memory.recall("which language for the frontend?")[0]["content"].startswith("I prefer TypeScript")
    r = run(jarvis.chat("what do you remember about TypeScript", "c2"))
    assert "TypeScript over JavaScript" in r["text"]
    r = run(jarvis.chat("search previous conversations for TypeScript", "c3"))
    assert "TypeScript" in r["text"]
