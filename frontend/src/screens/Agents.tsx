import { useMemo } from "react";
import { ArrowRight, Bot, CheckCircle2, ListTree, Radio, X, XCircle } from "lucide-react";
import { api } from "../api";
import type { AgentMessage } from "../api";
import { useApi, useStore } from "../store";
import { ago, clock, go } from "../util";
import { Loading, Panel, ScreenHead, StatusBadge } from "../components/Panel";
import { Markdown } from "../components/Markdown";

function Msg({ m }: { m: AgentMessage }) {
  return (
    <div className="agent-msg" data-testid="agent-msg">
      <div className="row" style={{ gap: 6 }}>
        <span className="badge violet">{m.sender}</span>
        <ArrowRight size={12} className="dim" />
        <span className="badge">{m.recipient}</span>
        <span className={`badge mono ${m.type === "result" ? "green" : m.type === "error" ? "red" : "cyan"}`}>{m.type}</span>
        <span className="grow" />
        <span className="dim num">{clock(m.created)}</span>
      </div>
      <div className="agent-msg-body soft">{m.content}</div>
    </div>
  );
}

export function Agents({ taskId }: { taskId: string | null }) {
  const rev = useStore((s) => s.rev);
  const running = useStore((s) => s.runningAgents);
  const feedLive = useStore((s) => s.agentFeed);
  const agents = useApi(api.agents, [rev.agent, rev.approval]);
  const tasks = useApi(api.tasks, [rev.agent]);
  const messages = useApi(api.agentMessages, []);
  const detail = useApi(() => (taskId ? api.task(taskId) : Promise.resolve(null)), [taskId, rev.agent, rev.plan]);
  const feed = useMemo(() => {
    const seen = new Set(feedLive.map((m) => m.id));
    return [...feedLive, ...(messages.data ?? []).filter((m) => !seen.has(m.id))].slice(0, 80);
  }, [feedLive, messages.data]);
  const runningByAgent = useMemo(() => {
    const map: Record<string, string[]> = {};
    Object.values(running).forEach((r) => (map[r.agent] ??= []).push(r.title));
    return map;
  }, [running]);
  const t = detail.data && detail.data.id === taskId ? detail.data : null;

  return (
    <>
      <ScreenHead title="Agent Control Center" sub="Seven specialised agents coordinated by the orchestrator. Every tool they hold is permission-checked." />
      <Loading error={agents.error} loading={agents.loading} empty={!agents.data} />
      <div className="agent-grid" data-testid="agent-grid">
        {agents.data?.map((a) => {
          const live = runningByAgent[a.name];
          return (
            <article key={a.name} className={`panel agent-card ${live ? "running" : ""}`} data-testid="agent-card">
              <header className="row">
                <Bot size={16} className="agent-ico" aria-hidden="true" />
                <div className="grow">
                  <h3 className="truncate">{a.title}</h3>
                  <span className="dim num">{a.name}</span>
                </div>
                <span className={`badge ${live ? "cyan" : ""}`} data-testid="agent-status"><span className={`dot ${live ? "live" : ""}`} /> {live ? "running" : "idle"}</span>
              </header>
              <p className="soft agent-resp">{a.responsibility}</p>
              {live && <div className="agent-live truncate" title={live[0]}>{live[0]}</div>}
              <div className="agent-stats num">
                <span><b>{a.stats.total}</b> tasks</span>
                <span className="ok"><b>{a.stats.done ?? 0}</b> done</span>
                <span className={a.stats.failed ? "bad" : ""}><b>{a.stats.failed ?? 0}</b> failed</span>
                <span className="dim">last {ago(a.stats.last)}</span>
              </div>
              <div className="label">Tools · {a.tools.length}</div>
              <div className="chips">
                {a.tools.map((tool) => (
                  <span key={tool.name} className={`badge mono ${tool.trusted ? "green" : tool.level === "confirm" ? "amber" : ""}`} title={tool.trusted ? "confirm-level, allowlisted to run automatically" : tool.level === "confirm" ? "modifies the system: needs your approval" : "read-only: runs automatically"}>
                    {tool.name}
                  </span>
                ))}
              </div>
            </article>
          );
        })}
      </div>
      <div className="row dim agent-legend">
        <span className="badge mono">read</span> runs automatically
        <span className="badge mono amber">confirm</span> needs approval
        <span className="badge mono green">trusted</span> allowlisted by you
      </div>

      <div className="grid" style={{ marginTop: 14 }}>
        <Panel className="s4" title={`Tasks · ${tasks.data?.length ?? 0}`} icon={<ListTree size={14} />} testid="agent-tasks">
          <div className="list tall-list">
            {tasks.error && <div className="error-text">{tasks.error}</div>}
            {tasks.data?.length === 0 && <div className="empty">No tasks yet. Ask JARVIS something.</div>}
            {tasks.data?.map((task) => (
              <button type="button" key={task.id} className={`list-item ${task.id === taskId ? "selected" : ""}`} onClick={() => go(`agents/${task.id}`)} data-testid="task-row">
                <span className="badge violet">{task.agent}</span>
                <span className="truncate grow">{task.parent_id ? "↳ " : ""}{task.title}</span>
                <StatusBadge status={task.status} />
              </button>
            ))}
          </div>
        </Panel>

        <Panel className="s4" title="Task detail" icon={<CheckCircle2 size={14} />} testid="task-detail" actions={taskId ? <button type="button" className="btn icon ghost sm" onClick={() => go("agents")} aria-label="Close task detail"><X size={13} /></button> : undefined}>
          {!taskId && <div className="empty">Select a task to see its plan, result and self-evaluation.</div>}
          {taskId && detail.error && <div className="error-text">{detail.error}</div>}
          {t && (
            <div className="col tall-list" style={{ gap: 10 }}>
              <div>
                <div className="row wrap"><span className="badge violet">{t.agent}</span><StatusBadge status={t.status} /><span className="dim num">{t.id}</span></div>
                <h3 style={{ marginTop: 6, fontSize: 14 }}>{t.title}</h3>
                <div className="dim num" style={{ fontSize: 11.5 }}>created {ago(t.created)}{t.conversation_id && <> · <a href={`#/conversations/${t.conversation_id}`}>open conversation</a></>}</div>
              </div>
              <div>
                <div className="label">Plan</div>
                {t.plan.length === 0 ? <div className="dim">No explicit plan was recorded for this task.</div> : (
                  <ol className="plan-list">{t.plan.map((p) => <li key={p.n}><b>{p.title}</b> <StatusBadge status={p.status} />{p.detail && <div className="soft">{p.detail}</div>}</li>)}</ol>
                )}
              </div>
              <div data-testid="task-evaluation">
                <div className="label">Self-evaluation</div>
                <div className="row wrap" style={{ marginTop: 4 }}>
                  {t.evaluation.ok === undefined ? <span className="dim">not evaluated yet</span> : t.evaluation.ok ? <span className="badge green"><CheckCircle2 size={12} /> passed checks</span> : <span className="badge red"><XCircle size={12} /> issues found</span>}
                  {typeof t.evaluation.pending_approvals === "number" && <span className="badge">{t.evaluation.pending_approvals} pending approval(s)</span>}
                  {t.evaluation.citations && <span className="badge">{t.evaluation.citations.verified.length}/{t.evaluation.citations.cited} citations verified</span>}
                </div>
                {t.evaluation.issues?.map((i, k) => <div key={k} className="error-text">• {i}</div>)}
              </div>
              {t.subtasks.length > 0 && (
                <div>
                  <div className="label">Delegated subtasks</div>
                  <div className="list">{t.subtasks.map((s) => <button type="button" key={s.id} className="list-item" onClick={() => go(`agents/${s.id}`)}><span className="badge violet">{s.agent}</span><span className="truncate grow">{s.title}</span><StatusBadge status={s.status} /></button>)}</div>
                </div>
              )}
              {t.result && (
                <div>
                  <div className="label">Result</div>
                  <div className="result-box"><Markdown text={t.result} /></div>
                </div>
              )}
              {t.messages.length > 0 && (
                <div>
                  <div className="label">Messages for this task · {t.messages.length}</div>
                  {t.messages.map((m) => <Msg key={m.id} m={m} />)}
                </div>
              )}
            </div>
          )}
        </Panel>

        <Panel className="s4" title="Inter-agent messages" icon={<Radio size={14} />} testid="agent-feed" actions={<span className="badge cyan"><span className="dot live" /> live</span>}>
          <div className="tall-list">
            {messages.error && <div className="error-text">{messages.error}</div>}
            {feed.length === 0 && <div className="empty">No messages exchanged yet.</div>}
            {feed.map((m) => <Msg key={m.id} m={m} />)}
          </div>
        </Panel>
      </div>
    </>
  );
}
