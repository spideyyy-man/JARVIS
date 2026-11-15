import { useState } from "react";
import { CalendarClock, History, Play, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import type { Schedule, WorkflowStep } from "../api";
import { errText, useApi, useStore } from "../store";
import { ago, dateTime, scheduleText, until } from "../util";
import { Loading, Panel, ScreenHead, StatusBadge, Switch } from "../components/Panel";

const EXAMPLE = `[
  { "id": "stats", "tool": "system_stats", "retries": 1 },
  { "id": "warn", "level": "warning",
    "notify": "CPU is at {{stats.cpu.percent}}%",
    "when": { "ref": "stats.cpu.percent", "op": ">", "value": 80 } }
]`;

function CreateForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [type, setType] = useState<Schedule["type"]>("interval");
  const [seconds, setSeconds] = useState("600");
  const [expr, setExpr] = useState("0 9 * * 1-5");
  const [inMinutes, setInMinutes] = useState("5");
  const [steps, setSteps] = useState(EXAMPLE);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setErr("");
    let parsed: WorkflowStep[];
    try {
      const v: unknown = JSON.parse(steps);
      if (!Array.isArray(v)) throw new Error("Steps must be a JSON array.");
      parsed = v as WorkflowStep[];
    } catch (e) {
      setErr(`Steps are not valid JSON: ${errText(e)}`);
      return;
    }
    const schedule: Schedule = type === "interval" ? { type, seconds: Number(seconds) } : type === "cron" ? { type, expr } : type === "once" ? { type, at: Date.now() / 1000 + Number(inMinutes) * 60 } : { type };
    setBusy(true);
    try {
      await api.createWorkflow({ name: name.trim(), description: "", schedule, steps: parsed });
      setName("");
      onCreated();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="col" style={{ gap: 10 }} onSubmit={(e) => { e.preventDefault(); void submit(); }} data-testid="wf-form">
      <div className="row wrap">
        <label className="field grow"><span>Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="CPU watchdog" data-testid="wf-name" /></label>
        <label className="field"><span>Schedule</span>
          <select className="select" value={type} onChange={(e) => setType(e.target.value as Schedule["type"])} data-testid="wf-type">
            <option value="interval">Every N seconds</option><option value="cron">Cron expression</option><option value="once">Once, later</option><option value="manual">Manual only</option>
          </select>
        </label>
        {type === "interval" && <label className="field"><span>Seconds (min 5)</span><input className="input num" style={{ width: 110 }} value={seconds} onChange={(e) => setSeconds(e.target.value)} inputMode="numeric" data-testid="wf-seconds" /></label>}
        {type === "cron" && <label className="field"><span>Cron (min hour dom mon dow)</span><input className="input num" style={{ width: 170 }} value={expr} onChange={(e) => setExpr(e.target.value)} data-testid="wf-cron" /></label>}
        {type === "once" && <label className="field"><span>In minutes</span><input className="input num" style={{ width: 110 }} value={inMinutes} onChange={(e) => setInMinutes(e.target.value)} inputMode="numeric" /></label>}
      </div>
      <div className="dim" style={{ fontSize: 12 }}>Each step is a tool call, a <span className="num">notify</span> message or an <span className="num">agent_prompt</span>. Later steps can reference earlier results as <span className="num">{"{{step_id.path}}"}</span> and be conditional with <span className="num">when</span>.</div>
      <label className="field"><span>Steps (JSON)</span>
        <textarea className="textarea num" rows={8} value={steps} onChange={(e) => setSteps(e.target.value)} spellCheck={false} data-testid="wf-steps" />
      </label>
      <div className="dim" style={{ fontSize: 12 }}>Steps that modify anything still need your approval on every run unless the tool is on the trusted list.</div>
      {err && <div className="error-text" role="alert" data-testid="wf-error">{err}</div>}
      <div><button type="submit" className="btn primary" disabled={busy || !name.trim()} data-testid="wf-create"><Plus size={14} /> Create workflow</button></div>
    </form>
  );
}

export function Automation() {
  const rev = useStore((s) => s.rev.workflow + s.rev.approval);
  const wfs = useApi(api.workflows, [rev]);
  const runs = useApi(api.workflowRuns, [rev]);
  const [err, setErr] = useState("");
  const [openRun, setOpenRun] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setErr("");
    try {
      await fn();
    } catch (e) {
      setErr(errText(e));
    }
    wfs.reload();
    runs.reload();
  };

  return (
    <>
      <ScreenHead title="Automation Manager" sub="Scheduled, recurring and conditional workflows, with a per-step execution log." />
      <div className="grid">
        <Panel className="s12" title="Workflows" icon={<CalendarClock size={14} />} testid="workflows">
          <Loading error={wfs.error} loading={wfs.loading} empty={!wfs.data} />
          {err && <div className="error-text" role="alert">{err}</div>}
          {wfs.data && wfs.data.length === 0 && <div className="empty">No workflows yet. Create one below, or ask: “every 10 minutes check my computer’s performance and alert me if cpu is above 80”.</div>}
          {wfs.data && wfs.data.length > 0 && (
            <table className="table">
              <thead><tr><th>Name</th><th>Schedule</th><th>Steps</th><th>Next run</th><th>Last run</th><th>Enabled</th><th /></tr></thead>
              <tbody>
                {wfs.data.map((w) => (
                  <tr key={w.id} data-testid="workflow">
                    <td>{w.name}</td><td className="num">{scheduleText(w.schedule)}</td>
                    <td className="num dim">{(w.steps ?? []).map((s) => s.tool ?? (s.notify ? "notify" : "agent")).join(" → ")}</td>
                    <td className="num">{w.enabled && w.next_run ? until(w.next_run) : <span className="dim">—</span>}</td>
                    <td className="dim">{w.last_run ? ago(w.last_run) : "never"}</td>
                    <td><Switch checked={!!w.enabled} label={`Enable ${w.name}`} onChange={(v) => void act(() => api.toggleWorkflow(w.id, v))} /></td>
                    <td className="r" style={{ whiteSpace: "nowrap" }}>
                      <button type="button" className="btn sm" onClick={() => void act(() => api.runWorkflow(w.id))} data-testid="wf-run"><Play size={12} /> Run now</button>{" "}
                      <button type="button" className="btn icon ghost sm" aria-label={`Delete workflow ${w.name}`} onClick={() => void act(() => api.deleteWorkflow(w.id))}><Trash2 size={13} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel className="s7" title="Run history" icon={<History size={14} />} testid="wf-runs">
          <Loading error={runs.error} loading={runs.loading} empty={!runs.data} />
          {runs.data && runs.data.length === 0 && <div className="empty">No runs yet.</div>}
          {(runs.data ?? []).map((r) => (
            <article key={r.id} className="note" data-testid="wf-run-row">
              <button type="button" className="list-item" onClick={() => setOpenRun(openRun === r.id ? null : r.id)} aria-expanded={openRun === r.id}>
                <span className="grow truncate">{r.workflow_name ?? r.workflow_id}</span>
                <span className="badge">{r.trigger}</span><StatusBadge status={r.status} /><span className="dim">{dateTime(r.started)}</span>
              </button>
              {openRun === r.id && (
                <table className="table" data-testid="wf-run-log">
                  <thead><tr><th>Step</th><th>Kind</th><th>Status</th><th className="r">Tries</th><th className="r">Time</th><th>Detail</th></tr></thead>
                  <tbody>
                    {r.log.map((e, i) => (
                      <tr key={i}><td className="num">{e.step}</td><td className="num dim">{e.kind}</td><td><StatusBadge status={e.status} /></td><td className="r num">{e.attempts}</td>
                        <td className="r num">{e.duration_s != null ? `${e.duration_s}s` : "—"}</td><td className="cell-clip dim" title={e.detail}>{e.detail}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </article>
          ))}
        </Panel>

        <Panel className="s5" title="Create workflow" icon={<Plus size={14} />} testid="wf-create-panel">
          <CreateForm onCreated={() => { wfs.reload(); }} />
        </Panel>
      </div>
    </>
  );
}
