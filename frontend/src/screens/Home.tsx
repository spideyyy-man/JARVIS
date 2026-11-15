import { Activity, Bell, Bot, CalendarClock, ClipboardList, Code2, FileClock, Gauge as GaugeIcon, MessageSquare, ShieldAlert, Sparkles, Terminal } from "lucide-react";
import { api } from "../api";
import { useApi, useAssistantState, usePendingApprovals, useStore } from "../store";
import { ago, basename, clock, go, scheduleText, until, useNow } from "../util";
import { Panel, ScreenHead, StatusBadge, Loading } from "../components/Panel";
import { Gauge, Sparkline, levelColor } from "../components/Charts";
import { Orb } from "../components/Orb";
import { QuickChat } from "../components/Chat";
import { ApprovalCard } from "../components/ApprovalCard";

function describeArgs(args: unknown): string {
  let a = args;
  if (typeof a === "string") {
    try {
      a = JSON.parse(a);
    } catch {
      return a as string;
    }
  }
  if (a && typeof a === "object") {
    const o = a as Record<string, unknown>;
    const v = o.path ?? o.src ?? o.source ?? o.folder ?? Object.values(o)[0];
    return v === undefined ? "" : String(v);
  }
  return "";
}

export function Home() {
  const rev = useStore((s) => s.rev);
  const { data, error, loading } = useApi(api.dashboard, [rev.chat, rev.approval, rev.agent, rev.workflow, rev.notification]);
  const state = useAssistantState();
  const detail = useStore((s) => s.stateDetail);
  const status = useStore((s) => s.status);
  const liveMetrics = useStore((s) => s.metrics);
  const history = useStore((s) => s.history);
  const running = useStore((s) => s.runningAgents);
  const notifications = useStore((s) => s.notifications);
  const pending = usePendingApprovals();
  useNow(5000);

  const m = liveMetrics ?? data?.metrics ?? null;
  const hist = history.length ? history : data?.metrics_history ?? [];
  const agents = Object.values(running);
  const sum = data?.daily_summary;
  const activeTask = status?.active_task ?? data?.status.active_task ?? null;

  return (
    <>
      <ScreenHead title="Command Center" sub={sum ? sum.date : "Live overview of your assistant and this computer"} />
      <Loading error={error} loading={loading} empty={!data} />
      <div className="grid">
        <Panel className="s7 stretch home-assistant" title="Assistant" icon={<Sparkles size={14} />} testid="home-assistant">
          <div className="home-assistant-top">
            <Orb state={state} size="md" />
            <div className="col grow" style={{ gap: 6 }}>
              <div className="home-state" data-testid="home-state">{state}</div>
              <div className="soft">
                {state === "executing" && detail ? <>Running <span className="num">{detail}</span></> : state === "idle" ? "Standing by. Ask anything below, or use the Voice screen." : state === "error" ? "The last request ended with an error." : "Working on your request…"}
              </div>
              <div className="home-task">
                <span className="label">Active task</span>
                {activeTask ? (
                  <button type="button" className="btn ghost sm truncate" onClick={() => go(`conversations/${activeTask.conversation_id}`)}>
                    <Activity size={13} /> <span className="truncate">{activeTask.title}</span> <span className="dim num">{ago(activeTask.started)}</span>
                  </button>
                ) : (
                  <span className="dim">none</span>
                )}
              </div>
            </div>
          </div>
          <QuickChat
            testid="home-command"
            placeholder="Give JARVIS a command…"
            emptyHint={status?.llm.degraded ? "Offline engine: try “analyze my computer's performance”, “inspect my current project” or “organize my downloads folder”." : undefined}
          />
        </Panel>

        <Panel className="s5 stretch" title="System vitals" icon={<GaugeIcon size={14} />} testid="home-vitals" actions={<button type="button" className="btn ghost sm" onClick={() => go("computer")}>Details</button>}>
          <div className="vitals">
            {([
              ["CPU", m?.cpu.percent ?? null, hist.map((p) => p.cpu), m ? `${m.cpu.cores} cores · load ${m.cpu.load_avg[0]?.toFixed(2)}` : ""],
              ["Memory", m?.memory.percent ?? null, hist.map((p) => p.memory), m ? `${m.memory.used_gb.toFixed(1)} / ${m.memory.total_gb.toFixed(1)} GB` : ""],
              ["Disk", m?.disk.percent ?? null, hist.map((p) => p.disk), m ? `${m.disk.free_gb.toFixed(0)} GB free` : ""],
            ] as const).map(([label, value, series, sub]) => (
              <div className="vital" key={label} data-testid={`vital-${label.toLowerCase()}`}>
                <Gauge value={value} label={label} sub={sub} />
                <Sparkline values={[...series]} color={levelColor(value ?? 0)} label={`${label} history`} height={34} />
              </div>
            ))}
          </div>
          <div className="row between dim" style={{ fontSize: 11.5 }}>
            <span>{hist.length} samples · 2 s interval</span>
            {m && <span className="num">net ↑ {m.net.sent_mb.toFixed(1)} MB ↓ {m.net.recv_mb.toFixed(1)} MB</span>}
          </div>
        </Panel>

        <Panel className="s4" title={`Pending approvals · ${pending.length}`} icon={<ShieldAlert size={14} />} testid="home-approvals">
          {pending.length === 0 ? <div className="empty">Nothing is waiting for your approval.</div> : pending.slice(0, 3).map((a) => <ApprovalCard key={a.id} approval={a} showSource />)}
          {pending.length > 3 && <div className="dim">+{pending.length - 3} more in the approval tray</div>}
        </Panel>

        <Panel className="s4" title="Agents" icon={<Bot size={14} />} testid="home-agents" actions={<button type="button" className="btn ghost sm" onClick={() => go("agents")}>Control center</button>}>
          <div className="label">Running now · {agents.length}</div>
          <div className="list">
            {agents.length === 0 && <div className="dim">No agent is running.</div>}
            {agents.map((a) => (
              <div className="list-item" key={a.task_id}><span className="dot live" /><span className="badge violet">{a.agent}</span><span className="truncate grow">{a.title}</span></div>
            ))}
          </div>
          <div className="label" style={{ marginTop: 6 }}>Recent tasks</div>
          <div className="list">
            {data?.recent_tasks.length === 0 && <div className="dim">No tasks yet.</div>}
            {data?.recent_tasks.map((t) => (
              <button type="button" className="list-item" key={t.id} onClick={() => go(`agents/${t.id}`)}>
                <span className="truncate grow">{t.title}</span><StatusBadge status={t.status} /><span className="dim num">{ago(t.updated)}</span>
              </button>
            ))}
          </div>
        </Panel>

        <Panel className="s4" title="Scheduled workflows" icon={<CalendarClock size={14} />} testid="home-workflows" actions={<button type="button" className="btn ghost sm" onClick={() => go("automation")}>Manage</button>}>
          <div className="list">
            {data?.workflows.length === 0 && <div className="empty">No workflows yet. Try “every 10 minutes check my computer's performance and alert me if cpu is above 80”.</div>}
            {data?.workflows.map((w) => (
              <div className="list-item" key={w.id}>
                <span className={`dot ${w.enabled ? "on" : ""}`} />
                <div className="col grow" style={{ gap: 0 }}>
                  <span className="truncate">{w.name}</span>
                  <span className="dim" style={{ fontSize: 11.5 }}>{scheduleText(w.schedule)}</span>
                </div>
                <span className="num soft" title="Next run">{w.enabled ? until(w.next_run) : "paused"}</span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel className="s4" title="Recent conversations" icon={<MessageSquare size={14} />} testid="home-conversations" actions={<button type="button" className="btn ghost sm" onClick={() => go("conversations")}>All</button>}>
          <div className="list">
            {data?.recent_conversations.length === 0 && <div className="empty">No conversations yet.</div>}
            {data?.recent_conversations.map((c) => (
              <button type="button" className="list-item" key={c.id} onClick={() => go(`conversations/${c.id}`)}>
                <span className="truncate grow">{c.title}</span><span className="dim num">{ago(c.updated)}</span>
              </button>
            ))}
          </div>
        </Panel>

        <Panel className="s4" title="Development" icon={<Code2 size={14} />} testid="home-dev" actions={<button type="button" className="btn ghost sm" onClick={() => go("coding")}>Workspace</button>}>
          <dl className="kv">
            <dt>Project</dt>
            <dd className="num" title={data?.projects.default}>{data?.projects.default ? basename(data.projects.default) : <span className="dim">not set</span>}</dd>
            <dt>Path</dt>
            <dd className="num soft">{data?.projects.default || "—"}</dd>
            <dt>Open tasks</dt>
            <dd className="num">{data?.projects.dev_tasks_open ?? "—"}</dd>
            <dt>Commits today</dt>
            <dd className="num">{sum?.commits_today?.length ?? 0}</dd>
          </dl>
          {sum && sum.tasks_open.length > 0 && (
            <div className="list">
              {sum.tasks_open.slice(0, 5).map((t, i) => <div className="list-item" key={i}><ClipboardList size={13} className="dim" /><span className="truncate">{t}</span></div>)}
            </div>
          )}
        </Panel>

        <Panel className="s4" title="Today" icon={<Terminal size={14} />} testid="home-summary">
          {sum && (
            <>
              <div className="stats-row">
                <div className="stat"><b>{sum.conversations}</b><span className="label">Chats</span></div>
                <div className="stat"><b>{sum.requests}</b><span className="label">Requests</span></div>
                <div className="stat"><b>{sum.actions_executed.reduce((n, a) => n + a.n, 0)}</b><span className="label">Actions</span></div>
                <div className="stat"><b>{sum.approved_changes.length}</b><span className="label">Approved</span></div>
              </div>
              {sum.actions_executed.length > 0 && (
                <div className="chips">{sum.actions_executed.map((a) => <span key={a.tool} className="badge mono">{a.tool} ×{a.n}</span>)}</div>
              )}
              {sum.approved_changes.map((c, i) => <div key={i} className="soft truncate" title={c} style={{ fontSize: 12.5 }}>✓ {c}</div>)}
              <div className="dim" style={{ fontSize: 12 }}>
                {sum.tasks_completed.length} dev task(s) completed · {sum.documents_indexed} document(s) indexed · {sum.workflow_runs.reduce((n, r) => n + r.n, 0)} workflow run(s)
              </div>
            </>
          )}
        </Panel>

        <Panel className="s6" title="Recent file activity" icon={<FileClock size={14} />} testid="home-files">
          <div className="list">
            {data?.file_activity.length === 0 && <div className="empty">No file operations recorded yet.</div>}
            {data?.file_activity.map((f, i) => (
              <div className="list-item" key={i}>
                <span className="num dim">{clock(f.ts)}</span>
                <span className="badge mono">{f.tool}</span>
                <span className="truncate grow num soft" title={describeArgs(f.args)}>{describeArgs(f.args)}</span>
                <StatusBadge status={f.outcome} />
              </div>
            ))}
          </div>
        </Panel>

        <Panel className="s6" title="Notifications" icon={<Bell size={14} />} testid="home-notifications">
          {notifications.length === 0 && <div className="empty">No notifications. Alerts from workflows and the system monitor appear here.</div>}
          {notifications.slice(0, 6).map((n) => (
            <div key={n.id} className={`note note-${n.level}`} style={{ marginBottom: 0 }}>
              <div className="row between"><b className="truncate">{n.title}</b><span className="dim num">{ago(n.created)}</span></div>
              <div className="soft">{n.body}</div>
            </div>
          ))}
        </Panel>
      </div>
    </>
  );
}
