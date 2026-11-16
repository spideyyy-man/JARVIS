import { useEffect, useState } from "react";
import { MessageSquarePlus, Search, Trash2, X } from "lucide-react";
import { api } from "../api";
import { errText, liveOf, useApi, useStore } from "../store";
import { ago, go } from "../util";
import { Composer, Thread } from "../components/Chat";
import { ScreenHead, StatusBadge } from "../components/Panel";

export function Conversations({ cid }: { cid: string | null }) {
  const rev = useStore((s) => s.rev.chat);
  const list = useApi(api.conversations, [rev]);
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(query.trim()), 250);
    return () => window.clearTimeout(t);
  }, [query]);
  const search = useApi(() => (debounced ? api.searchConversations(debounced) : Promise.resolve(null)), [debounced]);
  const detail = useApi(() => (cid && list.data?.some((c) => c.id === cid) ? api.conversation(cid) : Promise.resolve(null)), [cid, rev, !!list.data]);
  const streaming = useStore((s) => liveOf(s, cid).streaming);
  const current = list.data?.find((c) => c.id === cid);
  const tasks = detail.data && detail.data.id === cid ? detail.data.tasks : [];
  const plan = tasks.find((t) => t.plan.length > 0)?.plan ?? [];

  const remove = async (id: string) => {
    if (!window.confirm("Delete this conversation and all of its messages?")) return;
    setError("");
    try {
      await api.deleteConversation(id);
      if (id === cid) go("conversations");
      list.reload();
    } catch (e) {
      setError(errText(e));
    }
  };

  return (
    <div className="conv-screen">
      <ScreenHead title="AI Conversations" sub="Every exchange with JARVIS, searchable. Replies stream live; actions that change anything wait for your approval inline.">
        <button type="button" className="btn primary" onClick={() => go("conversations")} data-testid="new-conversation"><MessageSquarePlus size={15} /> New conversation</button>
      </ScreenHead>
      <div className="conv-layout">
        <aside className="panel conv-list" data-testid="conv-list">
          <div className="conv-search">
            <Search size={14} aria-hidden="true" />
            <input className="input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search messages…" aria-label="Search conversations" data-testid="conv-search" />
            {query && <button type="button" className="btn icon ghost sm" onClick={() => setQuery("")} aria-label="Clear search"><X size={13} /></button>}
          </div>
          {error && <div className="error-text" role="alert" style={{ padding: "0 12px" }}>{error}</div>}
          <div className="conv-items">
            {debounced ? (
              <>
                <div className="label" style={{ padding: "4px 12px" }}>{search.data ? `${search.data.length} match(es)` : "Searching…"}</div>
                {search.error && <div className="error-text" style={{ padding: "0 12px" }}>{search.error}</div>}
                {search.data?.map((h) => (
                  <button type="button" key={h.id} className={`conv-item ${h.conversation_id === cid ? "active" : ""}`} onClick={() => go(`conversations/${h.conversation_id}`)} data-testid="conv-hit">
                    <span className="truncate conv-title">{h.title}</span>
                    <span className="conv-snippet soft">{h.snippet}</span>
                    <span className="dim num">{h.role} · {ago(h.created)}</span>
                  </button>
                ))}
              </>
            ) : (
              <>
                {list.error && <div className="error-text" style={{ padding: "0 12px" }}>{list.error}</div>}
                {list.data?.length === 0 && <div className="empty" style={{ padding: 12 }}>No conversations yet. Start one on the right.</div>}
                {list.data?.map((c) => (
                  <div key={c.id} className={`conv-item-row ${c.id === cid ? "active" : ""}`}>
                    <button type="button" className="conv-item" onClick={() => go(`conversations/${c.id}`)} aria-current={c.id === cid} data-testid="conv-item">
                      <span className="truncate conv-title">{c.title}</span>
                      <span className="dim num">{c.messages} msg · {ago(c.updated)}</span>
                    </button>
                    <button type="button" className="btn icon ghost sm conv-del" onClick={() => void remove(c.id)} aria-label={`Delete conversation ${c.title}`} data-testid="conv-delete"><Trash2 size={13} /></button>
                  </div>
                ))}
              </>
            )}
          </div>
        </aside>

        <section className="panel conv-thread">
          <header className="conv-thread-head">
            <div className="grow truncate">
              <h2 className="truncate">{current?.title ?? (cid ? "Conversation" : "New conversation")}</h2>
              <span className="dim num">{cid ?? "not started"}</span>
            </div>
            {streaming && <span className="badge cyan"><span className="dot live" /> streaming</span>}
            {tasks.length > 0 && <span className="badge" title="Agent tasks spawned by this conversation">{tasks.length} task(s)</span>}
            {cid && current && <button type="button" className="btn danger sm" onClick={() => void remove(cid)} data-testid="conv-delete-current"><Trash2 size={13} /> Delete</button>}
          </header>
          <div className="conv-scroll">
            {plan.length > 0 && (
              <div className="plan-box" data-testid="conv-plan">
                <div className="label">Plan</div>
                <ol>{plan.map((p) => <li key={p.n} data-status={p.status}><b>{p.title}</b>{p.detail && <span className="soft"> — {p.detail}</span>} <StatusBadge status={p.status} />{(p as { note?: string }).note && <div className="dim">{(p as { note?: string }).note}</div>}</li>)}</ol>
              </div>
            )}
            <Thread key={cid ?? "new"} cid={cid} emptyHint={cid ? undefined : "Ask a question or give an instruction. Reading and analysis run immediately; anything that modifies your system asks first."} />
          </div>
          <Composer cid={cid} onSent={(id) => id !== cid && go(`conversations/${id}`)} placeholder="Message JARVIS…" autoFocus testid="conv-composer" />
        </section>
      </div>
    </div>
  );
}
