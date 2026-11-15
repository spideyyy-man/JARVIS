import { useRef, useState } from "react";
import { BookOpen, Brain, FileText, FolderInput, Search, Trash2, Upload } from "lucide-react";
import { api } from "../api";
import type { KbResult } from "../api";
import { errText, useApi, useStore } from "../store";
import { ago } from "../util";
import { Loading, Panel, ScreenHead, StatusBadge } from "../components/Panel";

export function Knowledge() {
  const rev = useStore((s) => s.rev.tool);
  const [space, setSpace] = useState("");
  const spaces = useApi(api.kbSpaces, [rev]);
  const docs = useApi(() => api.kbDocuments(space || undefined), [rev, space]);
  const memory = useApi(api.memory, [rev]);
  const [path, setPath] = useState("");
  const [target, setTarget] = useState("general");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KbResult[] | null>(null);
  const [searchErr, setSearchErr] = useState("");
  const [mem, setMem] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const reloadAll = () => { spaces.reload(); docs.reload(); };
  const run = async (fn: () => Promise<Record<string, unknown>>) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      const text = "indexed" in r ? `Indexed ${String(r.indexed)} document(s), ${String(r.new_or_updated)} new or updated.` : `"${String(r.title)}" ${String(r.status)}${r.chunks ? ` (${String(r.chunks)} passages)` : ""}.`;
      setMsg({ ok: true, text });
      reloadAll();
    } catch (e) {
      setMsg({ ok: false, text: errText(e) });
    } finally {
      setBusy(false);
    }
  };
  const search = async () => {
    if (!query.trim()) return;
    setSearchErr("");
    try {
      setResults((await api.kbSearch(query.trim(), space || undefined)).results);
    } catch (e) {
      setSearchErr(errText(e));
    }
  };

  return (
    <>
      <ScreenHead title="Knowledge Explorer" sub="Index your documents, search them, and manage what JARVIS remembers." />
      <div className="grid">
        <Panel className="s8" title="Search indexed knowledge" icon={<Search size={14} />} testid="kb-search">
          <form className="row" onSubmit={(e) => { e.preventDefault(); void search(); }}>
            <input className="input" style={{ flex: 1, minWidth: 0 }} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Ask your documents…" aria-label="Knowledge query" data-testid="kb-query" />
            <select className="select" style={{ width: 170, flex: "none" }} value={space} onChange={(e) => setSpace(e.target.value)} aria-label="Knowledge space" data-testid="kb-space-filter">
              <option value="">All spaces</option>
              {(spaces.data ?? []).map((s) => <option key={s.space} value={s.space}>{s.space}</option>)}
            </select>
            <button type="submit" className="btn primary" disabled={!query.trim()} data-testid="kb-search-btn"><Search size={14} /> Search</button>
          </form>
          {searchErr && <div className="error-text" role="alert">{searchErr}</div>}
          {results === null && <div className="dim" style={{ fontSize: 12.5 }}>Hybrid retrieval: keyword (BM25) and vector similarity, fused. Results show the exact passage and where it came from.</div>}
          {results && results.length === 0 && <div className="empty" data-testid="kb-no-results">Nothing indexed matches that query.</div>}
          {results?.map((r, i) => (
            <article key={r.chunk_id} className="note" data-testid="kb-result">
              <div className="row between">
                <b className="truncate">[{i + 1}] {r.title}{r.locator ? ` · ${r.locator}` : ""}</b>
                <span className="num dim" title="fused rank score · cosine similarity">score {r.score.toFixed(3)} · sim {r.similarity.toFixed(2)}</span>
              </div>
              <div className="soft" style={{ whiteSpace: "pre-wrap", margin: "4px 0" }}>{r.text.length > 600 ? r.text.slice(0, 600) + "…" : r.text}</div>
              <div className="dim num truncate" title={r.source}>{r.space} · {r.source}</div>
            </article>
          ))}
        </Panel>

        <Panel className="s4" title="Spaces" icon={<BookOpen size={14} />} testid="kb-spaces">
          <Loading error={spaces.error} loading={spaces.loading} empty={!spaces.data} />
          {spaces.data && spaces.data.length === 0 && <div className="empty">No knowledge indexed yet. Add a file or folder below.</div>}
          <div className="list">
            {(spaces.data ?? []).map((s) => (
              <button key={s.space} type="button" className="list-item" onClick={() => setSpace(space === s.space ? "" : s.space)} aria-pressed={space === s.space} data-testid="kb-space">
                <BookOpen size={13} className="dim" /><span className="grow truncate">{s.space}</span>
                <span className="num dim">{s.documents} docs · {s.chunks} passages</span>
              </button>
            ))}
          </div>
        </Panel>

        <Panel className="s12" title="Add knowledge" icon={<FolderInput size={14} />} testid="kb-add">
          <form className="row wrap" onSubmit={(e) => { e.preventDefault(); if (path.trim()) void run(() => api.kbIngest(path.trim(), target.trim() || "general")); }}>
            <input className="input grow" value={path} onChange={(e) => setPath(e.target.value)} placeholder="Path to a file or folder (PDF, DOCX, TXT, Markdown, code)…" aria-label="Path to index" data-testid="kb-path" />
            <input className="input" style={{ width: 150 }} value={target} onChange={(e) => setTarget(e.target.value)} placeholder="space" aria-label="Target space" data-testid="kb-target-space" />
            <button type="submit" className="btn primary" disabled={busy || !path.trim()} data-testid="kb-ingest-btn"><FolderInput size={14} /> Index path</button>
            <input ref={fileRef} type="file" className="sr-only" aria-label="Upload a document" data-testid="kb-file"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void run(() => api.kbUpload(f, target.trim() || "general")); e.target.value = ""; }} />
            <button type="button" className="btn" disabled={busy} onClick={() => fileRef.current?.click()}><Upload size={14} /> Upload file</button>
          </form>
          {busy && <div className="dim">Indexing…</div>}
          {msg && <div className={msg.ok ? "note note-success" : "error-text"} role={msg.ok ? "status" : "alert"} data-testid="kb-ingest-msg">{msg.text}</div>}
        </Panel>

        <Panel className="s8" title={`Documents${space ? ` · ${space}` : ""}`} icon={<FileText size={14} />} testid="kb-documents">
          <Loading error={docs.error} loading={docs.loading} empty={!docs.data} />
          {docs.data && docs.data.length === 0 && <div className="empty">No documents in this view.</div>}
          {docs.data && docs.data.length > 0 && (
            <table className="table">
              <thead><tr><th>Title</th><th>Space</th><th>Type</th><th className="r">Passages</th><th>Indexed</th><th /></tr></thead>
              <tbody>
                {docs.data.map((d) => (
                  <tr key={d.id} data-testid="kb-doc">
                    <td className="cell-clip" title={d.source}>{d.title}</td>
                    <td>{d.space}</td><td><StatusBadge status={d.kind} /></td><td className="r num">{d.chunks}</td><td className="dim">{ago(d.created)}</td>
                    <td className="r"><button type="button" className="btn icon ghost sm" aria-label={`Remove ${d.title} from the index`} onClick={() => void api.kbDelete(d.id).then(reloadAll)}><Trash2 size={13} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel className="s4" title="Long-term memory" icon={<Brain size={14} />} testid="memory">
          <form className="row" onSubmit={(e) => { e.preventDefault(); const c = mem.trim(); if (c) { setMem(""); void api.addMemory(c, "fact").then(memory.reload); } }}>
            <input className="input grow" value={mem} onChange={(e) => setMem(e.target.value)} placeholder="Something JARVIS should remember…" aria-label="New memory" data-testid="memory-input" />
            <button type="submit" className="btn primary" disabled={!mem.trim()} data-testid="memory-add">Save</button>
          </form>
          <Loading error={memory.error} loading={memory.loading} empty={!memory.data} />
          {memory.data && memory.data.length === 0 && <div className="empty">Nothing remembered yet. Say “remember that …”.</div>}
          <div className="list">
            {(memory.data ?? []).map((m) => (
              <div key={m.id} className="list-item" data-testid="memory-item">
                <span className="badge">{m.kind}</span><span className="grow" style={{ overflowWrap: "anywhere" }}>{m.content}</span>
                <button type="button" className="btn icon ghost sm" aria-label="Forget this" onClick={() => void api.deleteMemory(m.id).then(memory.reload)}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </>
  );
}
