// Conversation building blocks shared by Home, Conversations, Coding and Research.
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowUpRight, Check, Clock, Cpu, Mic, SendHorizontal, Square, Wrench, X, Zap } from "lucide-react";
import { api } from "../api";
import type { Message } from "../api";
import { errText, liveOf, sendChat, useApi, useStore } from "../store";
import { clock, go } from "../util";
import { ApprovalCard } from "./ApprovalCard";
import { Markdown } from "./Markdown";

/** Conversations created in this tab; nothing to fetch for them, the live stream has every message. */
const localOnly = new Set<string>();

export function MessageView({ msg }: { msg: Message }) {
  const approvals = useStore((s) => s.approvals);
  if (msg.role === "event") {
    const bad = msg.meta.status === "failed" || msg.meta.status === "denied";
    return (
      <div className={`msg-event ${bad ? "bad" : ""}`} data-testid="msg-event">
        <Zap size={13} aria-hidden="true" />
        <Markdown text={msg.content} />
        <span className="num dim">{clock(msg.created)}</span>
      </div>
    );
  }
  const m = msg.meta;
  const isUser = msg.role === "user";
  return (
    <div className={`msg ${isUser ? "user" : "assistant"}`} data-testid={`msg-${msg.role}`}>
      <div className="msg-meta-top">
        <span className="label">{isUser ? "You" : "JARVIS"}</span>
        {isUser && m.source === "voice" && <span className="badge cyan"><Mic size={11} /> voice</span>}
        <span className="num dim">{clock(msg.created)}</span>
      </div>
      <div className="msg-bubble">{isUser ? <p className="msg-text">{msg.content}</p> : <Markdown text={msg.content} />}</div>
      {!isUser && (m.provider || m.agents?.length || m.tools?.length || m.errors?.length) ? (
        <div className="msg-meta" data-testid="msg-meta">
          {m.provider && <span className={`badge ${m.provider === "offline" ? "amber" : "cyan"}`} title="Engine that produced this answer"><Cpu size={11} /> {m.provider === "offline" ? "offline engine" : m.provider}</span>}
          {m.agents?.map((a) => <span key={a} className="badge violet">{a}</span>)}
          {m.tools?.map((t, i) => (
            <span key={i} className={`badge mono ${t.pending ? "amber" : t.ok ? "" : "red"}`} title={`${t.agent} agent · ${t.pending ? "waiting for approval" : t.ok ? "ok" : "failed"}`}>
              {t.pending ? <Clock size={11} /> : t.ok ? <Check size={11} /> : <X size={11} />} {t.tool}
            </span>
          ))}
          {m.interrupted && <span className="badge amber">interrupted</span>}
          {m.errors?.map((e, i) => <span key={`e${i}`} className="badge red" title={e}><AlertTriangle size={11} /> {e.length > 60 ? e.slice(0, 60) + "…" : e}</span>)}
        </div>
      ) : null}
      {m.pending_approvals?.map((id) => (approvals[id] ? <ApprovalCard key={id} approval={approvals[id]} /> : <div key={id} className="dim">Approval {id} (details unavailable)</div>))}
    </div>
  );
}

/** Messages of a conversation: stored history + anything that arrived live, plus the streaming reply. */
export function Thread({ cid, liveOnly = false, emptyHint }: { cid: string | null; liveOnly?: boolean; emptyHint?: string }) {
  const live = useStore((s) => liveOf(s, cid));
  const approvals = useStore((s) => s.approvals);
  const skip = liveOnly || !cid || localOnly.has(cid);
  const { data, error } = useApi(() => (skip ? Promise.resolve(null) : api.conversation(cid)), [cid, skip]);
  const messages = useMemo(() => {
    const base = data && data.id === cid ? data.messages : [];
    const seen = new Set(base.map((m) => m.id));
    return [...base, ...live.messages.filter((m) => !seen.has(m.id))];
  }, [data, live.messages, cid]);
  const orphanApprovals = useMemo(() => {
    if (!cid) return [];
    const referenced = new Set(messages.flatMap((m) => m.meta.pending_approvals ?? []));
    return Object.values(approvals).filter((a) => a.conversation_id === cid && a.status === "pending" && !referenced.has(a.id));
  }, [approvals, messages, cid]);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [messages.length, live.streamText, orphanApprovals.length]);

  return (
    <div className="thread" data-testid="thread" aria-live="polite">
      {error && !skip && <div className="error-text" role="alert">{error}</div>}
      {messages.length === 0 && !live.streaming && emptyHint && <div className="empty">{emptyHint}</div>}
      {messages.map((m) => <MessageView key={m.id} msg={m} />)}
      {live.streaming && (
        <div className="msg assistant streaming" data-testid="msg-streaming">
          <div className="msg-meta-top">
            <span className="label">JARVIS</span>
            <span className="badge cyan"><span className="dot live" /> {live.streamText ? "responding" : "thinking"}</span>
          </div>
          <div className="msg-bubble">{live.streamText ? <Markdown text={live.streamText} /> : <span className="typing" aria-label="Thinking"><i /><i /><i /></span>}</div>
        </div>
      )}
      {orphanApprovals.map((a) => <ApprovalCard key={a.id} approval={a} />)}
      <div ref={end} />
    </div>
  );
}

export function Composer({ cid, onSent, placeholder = "Ask JARVIS…", prefix = "", autoFocus, testid = "composer" }: { cid: string | null; onSent: (cid: string) => void; placeholder?: string; prefix?: string; autoFocus?: boolean; testid?: string }) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const streaming = useStore((s) => liveOf(s, cid).streaming);

  const send = async (override?: string) => {
    const t = (override ?? text).trim();
    if (!t || streaming) return;
    setError("");
    if (!override) setText("");
    try {
      const id = await sendChat(prefix + t, cid);
      if (!cid) localOnly.add(id);
      onSent(id);
    } catch (e) {
      setError(errText(e));
      if (!override) setText(t);
    }
  };
  return (
    <form className="composer" data-testid={testid} onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <div className="composer-row">
        <input className="input composer-input" value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} aria-label={placeholder} autoFocus={autoFocus} />
        {streaming && cid ? (
          <button type="button" className="btn danger" onClick={() => void api.cancelChat(cid).catch(() => {})} data-testid="stop-btn">
            <Square size={13} /> Stop
          </button>
        ) : (
          <button type="submit" className="btn primary" disabled={!text.trim()} data-testid="send-btn">
            <SendHorizontal size={15} /> Send
          </button>
        )}
      </div>
      {error && <div className="error-text" role="alert">{error}</div>}
    </form>
  );
}

/** Compact prompt box + live answer. Used wherever a screen "sends a prompt to chat". */
export function QuickChat({ placeholder, prefix, suggestions, testid, emptyHint }: { placeholder?: string; prefix?: string; suggestions?: { label: string; prompt: string; icon?: React.ReactNode }[]; testid?: string; emptyHint?: string }) {
  const [cid, setCid] = useState<string | null>(null);
  const [error, setError] = useState("");
  const streaming = useStore((s) => liveOf(s, cid).streaming);
  const run = async (prompt: string) => {
    setError("");
    try {
      const id = await sendChat(prompt, cid);
      if (!cid) localOnly.add(id);
      setCid(id);
    } catch (e) {
      setError(errText(e));
    }
  };
  return (
    <div className="quickchat" data-testid={testid}>
      <Composer cid={cid} onSent={setCid} placeholder={placeholder} prefix={prefix} testid={testid ? `${testid}-composer` : undefined} />
      {suggestions && (
        <div className="row wrap quick-suggestions">
          {suggestions.map((s) => (
            <button key={s.label} type="button" className="btn sm" disabled={streaming} onClick={() => void run(s.prompt)} title={s.prompt}>
              {s.icon ?? <Wrench size={12} />} {s.label}
            </button>
          ))}
        </div>
      )}
      {error && <div className="error-text" role="alert">{error}</div>}
      {cid ? (
        <>
          <Thread cid={cid} liveOnly />
          <button type="button" className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={() => go(`conversations/${cid}`)}>
            Open in Conversations <ArrowUpRight size={13} />
          </button>
        </>
      ) : (
        emptyHint && <div className="dim" style={{ fontSize: 12.5 }}>{emptyHint}</div>
      )}
    </div>
  );
}
