"""Personal knowledge system: document ingestion, chunking, embeddings, hybrid retrieval, memory.

Vectors live in SQLite as float32 blobs and are searched by brute-force cosine with numpy.
At personal scale (tens of thousands of chunks) this is a few milliseconds and removes a whole
service dependency; swap `VectorIndex` for a real ANN store if the corpus outgrows it.
"""
from __future__ import annotations

import hashlib
import re
import time
import zlib
from pathlib import Path
from typing import Protocol

import numpy as np

from .db import Database, new_id

STOP = set("a an and are as at be by for from has have in is it its of on or that the this to was were will with what which who how why when "
           "do does can could should would i you we they he she me my your our about into than then there their them these those not no".split())
_WORD = re.compile(r"[a-z0-9][a-z0-9_\-]*")


def tokenize(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if w in STOP or len(w) < 2:
            continue
        for suffix in ("ations", "ation", "ingly", "ings", "ing", "ies", "ed", "es", "s"):  # crude stemming
            if w.endswith(suffix) and len(w) - len(suffix) >= 4:
                w = w[: -len(suffix)]
                break
        out.append(w)
    return out


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    """Dependency-free lexical embedding: signed feature hashing of stemmed unigrams, bigrams and
    character trigrams. It captures word overlap and morphology, NOT deep semantics - paraphrases
    with no shared vocabulary will not match. Set JARVIS_EMBEDDER to a neural provider for that."""

    name = "hashing-v1"
    dim = 1024

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            toks = tokenize(text)
            feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
            feats += [f"#{t[i:i + 3]}" for t in toks for i in range(len(t) - 2)]
            for f in feats:
                h = zlib.crc32(f.encode())
                weight = 0.4 if f.startswith("#") else 1.0
                out[row, h % self.dim] += weight if (h >> 16) & 1 else -weight
            norm = np.linalg.norm(out[row])
            if norm:
                out[row] /= norm
        return out


class OpenAICompatEmbedder:
    """Integration point for neural embeddings via any OpenAI-compatible /v1/embeddings server
    (OpenAI, Ollama, LM Studio, text-embeddings-inference). Not exercised in CI - needs a server."""

    def __init__(self, base_url: str, model: str, api_key: str | None):
        self.base_url, self.model, self.api_key = base_url.rstrip("/"), model, api_key
        self.name, self.dim = f"openai:{model}", 0

    def embed(self, texts: list[str]) -> np.ndarray:
        import httpx

        r = httpx.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": texts},
                       headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}, timeout=60)
        r.raise_for_status()
        vecs = np.array([d["embedding"] for d in r.json()["data"]], dtype=np.float32)
        self.dim = vecs.shape[1]
        return vecs / np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None)


def make_embedder(settings) -> Embedder:
    if settings.embedder.startswith("openai:") and settings.openai_base_url:
        from .config import get_secret

        return OpenAICompatEmbedder(settings.openai_base_url, settings.embedder.split(":", 1)[1], get_secret("OPENAI_API_KEY"))
    return HashingEmbedder()


# ---------------------------------------------------------------- extraction + chunking
SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".markdown", ".rst", ".py", ".ts", ".tsx", ".js", ".json", ".yaml", ".yml", ".toml", ".html", ".csv"}


def extract_text(path: Path) -> list[tuple[str, str]]:
    """Returns (locator, text) sections so citations can point at a page or heading."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        return [(f"p.{i}", page.extract_text() or "") for i, page in enumerate(PdfReader(str(path)).pages, 1)]
    if ext == ".docx":
        import docx

        sections, heading, buf = [], "start", []
        for para in docx.Document(str(path)).paragraphs:
            if para.style is not None and para.style.name.lower().startswith("heading") and para.text.strip():
                if buf:
                    sections.append((heading, "\n".join(buf)))
                heading, buf = para.text.strip(), []
            elif para.text.strip():
                buf.append(para.text)
        if buf:
            sections.append((heading, "\n".join(buf)))
        return sections
    text = path.read_text(errors="replace")
    if ext == ".html":
        from bs4 import BeautifulSoup

        text = BeautifulSoup(text, "html.parser").get_text("\n")
    if ext in (".md", ".markdown"):
        sections, heading, buf = [], "intro", []
        for line in text.splitlines():
            m = re.match(r"^#{1,4}\s+(.*)", line)
            if m:
                if "".join(buf).strip():
                    sections.append((heading, "\n".join(buf)))
                heading, buf = m.group(1).strip(), [line]
            else:
                buf.append(line)
        if "".join(buf).strip():
            sections.append((heading, "\n".join(buf)))
        return sections
    return [("", text)]


def chunk_sections(sections: list[tuple[str, str]], size: int = 900, overlap: int = 150) -> list[tuple[str, str]]:
    chunks = []
    for locator, text in sections:
        text = re.sub(r"[ \t]+", " ", text).strip()
        if not text:
            continue
        paras, buf = re.split(r"\n\s*\n", text), ""
        for para in paras:
            while len(para) > size:  # a single huge paragraph: hard split on sentence-ish boundary
                cut = para.rfind(". ", 0, size)
                cut = cut + 1 if cut > size // 2 else size
                paras_head, para = para[:cut], para[cut:]
                if buf:
                    chunks.append((locator, buf))
                    buf = ""
                chunks.append((locator, paras_head.strip()))
            if len(buf) + len(para) + 2 > size and buf:
                chunks.append((locator, buf))
                buf = buf[-overlap:] + "\n\n" + para
            else:
                buf = f"{buf}\n\n{para}" if buf else para
        if buf.strip():
            chunks.append((locator, buf.strip()))
    return chunks


def _fts_query(text: str) -> str:
    toks = list(dict.fromkeys(w for w in re.findall(r"[A-Za-z0-9]+", text.lower()) if w not in STOP and len(w) > 1))
    return " OR ".join(f'"{t}"*' for t in toks[:24])


class KnowledgeBase:
    def __init__(self, db: Database, embedder: Embedder):
        self.db, self.embedder = db, embedder

    # -- ingestion -------------------------------------------------------------
    def ingest_file(self, path: Path, space: str = "general") -> dict:
        if path.suffix.lower() not in SUPPORTED:
            raise ValueError(f"Unsupported file type {path.suffix}; supported: {', '.join(sorted(SUPPORTED))}")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        existing = self.db.one("SELECT id, sha FROM kb_documents WHERE source=? AND space=?", (str(path), space))
        if existing and existing["sha"] == sha:
            return {"document_id": existing["id"], "status": "unchanged", "title": path.name}
        if existing:
            self.delete_document(existing["id"])
        return self._store(path.name, str(path), path.suffix.lower().lstrip("."), sha, extract_text(path), space)

    def ingest_text(self, title: str, text: str, space: str = "general", source: str = "note") -> dict:
        return self._store(title, source, "note", hashlib.sha256(text.encode()).hexdigest(), [("", text)], space)

    def ingest_directory(self, root: Path, space: str, guard) -> dict:
        from .tools.files import walk

        done, skipped = [], []
        for p in walk(root, guard, max_files=3000):
            if p.suffix.lower() in SUPPORTED and p.stat().st_size < 5_000_000:
                try:
                    done.append(self.ingest_file(p, space))
                except Exception as e:
                    skipped.append({"file": str(p), "error": str(e)})
        return {"space": space, "indexed": len(done), "new_or_updated": sum(d["status"] == "indexed" for d in done), "skipped": skipped[:20]}

    def _store(self, title: str, source: str, kind: str, sha: str, sections: list[tuple[str, str]], space: str) -> dict:
        chunks = chunk_sections(sections)
        if not chunks:
            raise ValueError(f"No extractable text in {title}")
        vectors = self.embedder.embed([f"{title}\n{t}" for _l, t in chunks])
        doc_id = new_id("doc_")
        self.db.insert("kb_documents", {"id": doc_id, "space": space, "title": title, "source": source, "kind": kind, "sha": sha, "chunks": len(chunks), "created": time.time()})
        for i, ((locator, text), vec) in enumerate(zip(chunks, vectors)):
            cid = new_id("ch_")
            self.db.insert("kb_chunks", {"id": cid, "document_id": doc_id, "space": space, "ord": i, "locator": locator, "text": text, "vector": vec.astype(np.float32).tobytes()})
            self.db.execute("INSERT INTO kb_fts(text, chunk_id, space) VALUES (?,?,?)", (text, cid, space))
        return {"document_id": doc_id, "status": "indexed", "title": title, "chunks": len(chunks), "space": space}

    def delete_document(self, doc_id: str) -> None:
        self.db.execute("DELETE FROM kb_fts WHERE chunk_id IN (SELECT id FROM kb_chunks WHERE document_id=?)", (doc_id,))
        self.db.execute("DELETE FROM kb_chunks WHERE document_id=?", (doc_id,))
        self.db.execute("DELETE FROM kb_documents WHERE id=?", (doc_id,))

    # -- retrieval ---------------------------------------------------------------
    def search(self, query: str, space: str | None = None, k: int = 6) -> list[dict]:
        """Hybrid retrieval: BM25 (FTS5) and vector cosine, fused with reciprocal-rank fusion."""
        where, params = ("WHERE space=?", [space]) if space else ("", [])
        with self.db._lock:
            rows = self.db._conn.execute(f"SELECT id, vector FROM kb_chunks {where}", params).fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        matrix = np.frombuffer(b"".join(r["vector"] for r in rows), dtype=np.float32).reshape(len(rows), -1)
        qvec = self.embedder.embed([query])[0]
        sims = matrix @ qvec if matrix.shape[1] == qvec.shape[0] else np.zeros(len(rows))
        vec_rank = [ids[i] for i in np.argsort(-sims)[: k * 4] if sims[i] > 0.02]
        fts_rank: list[str] = []
        fq = _fts_query(query)
        if fq:
            sql = "SELECT chunk_id FROM kb_fts WHERE kb_fts MATCH ?" + (" AND space=?" if space else "") + " ORDER BY bm25(kb_fts) LIMIT ?"
            fts_rank = [r["chunk_id"] for r in self.db.query(sql, [fq, *([space] if space else []), k * 4])]
        sim_by_id_all = sim_by_id = dict(zip(ids, np.asarray(sims).tolist()))
        scores: dict[str, float] = {}
        for ranking in (vec_rank, fts_rank):
            for rank, cid in enumerate(ranking):
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + rank)
        results = []
        # Relevance floor: a passage must share a real term (FTS hit) or be clearly similar. Without it,
        # weak vector noise would surface unrelated passages and invite a confident wrong answer.
        fts_set = set(fts_rank)
        scores = {cid: sc for cid, sc in scores.items() if cid in fts_set or sim_by_id_all.get(cid, 0.0) >= 0.18}
        for cid in sorted(scores, key=scores.get, reverse=True)[:k]:
            row = self.db.one("SELECT c.id, c.text, c.locator, c.space, d.title, d.source, d.kind FROM kb_chunks c JOIN kb_documents d ON d.id=c.document_id WHERE c.id=?", (cid,))
            if row:
                results.append({"chunk_id": cid, "title": row["title"], "source": row["source"], "locator": row["locator"], "space": row["space"],
                                "score": round(scores[cid], 4), "similarity": round(sim_by_id.get(cid, 0.0), 3), "text": row["text"]})
        return results

    def spaces(self) -> list[dict]:
        return self.db.query("SELECT space, COUNT(*) AS documents, SUM(chunks) AS chunks FROM kb_documents GROUP BY space ORDER BY space")

    def documents(self, space: str | None = None) -> list[dict]:
        if space:
            return self.db.query("SELECT * FROM kb_documents WHERE space=? ORDER BY created DESC", (space,))
        return self.db.query("SELECT * FROM kb_documents ORDER BY created DESC")


class Memory:
    """Long-term memory: durable facts/preferences, recalled by similarity and injected into context."""

    def __init__(self, db: Database, embedder: Embedder):
        self.db, self.embedder = db, embedder

    def remember(self, content: str, kind: str = "fact") -> dict:
        row = {"id": new_id("m_"), "kind": kind, "content": content, "vector": self.embedder.embed([content])[0].tobytes(), "created": time.time()}
        self.db.insert("memories", row)
        return {"id": row["id"], "kind": kind, "content": content}

    def recall(self, query: str, k: int = 5, min_sim: float = 0.08) -> list[dict]:
        with self.db._lock:
            rows = self.db._conn.execute("SELECT id, kind, content, vector, created FROM memories").fetchall()
        if not rows:
            return []
        matrix = np.frombuffer(b"".join(r["vector"] for r in rows), dtype=np.float32).reshape(len(rows), -1)
        sims = matrix @ self.embedder.embed([query])[0]
        order = np.argsort(-sims)[:k]
        return [{"id": rows[i]["id"], "kind": rows[i]["kind"], "content": rows[i]["content"], "similarity": round(float(sims[i]), 3)} for i in order if sims[i] >= min_sim]

    def all(self) -> list[dict]:
        return self.db.query("SELECT id, kind, content, created FROM memories ORDER BY created DESC")

    def forget(self, id_: str) -> None:
        self.db.execute("DELETE FROM memories WHERE id=?", (id_,))


def search_conversations(db: Database, query: str, limit: int = 10) -> list[dict]:
    fq = _fts_query(query)
    if not fq:
        return []
    return db.query(
        "SELECT m.id, m.conversation_id, m.role, m.created, c.title, snippet(messages_fts, 0, '[', ']', ' ... ', 24) AS snippet "
        "FROM messages_fts JOIN messages m ON m.id = messages_fts.message_id JOIN conversations c ON c.id = m.conversation_id "
        "WHERE messages_fts MATCH ? ORDER BY bm25(messages_fts) LIMIT ?", (fq, limit))
