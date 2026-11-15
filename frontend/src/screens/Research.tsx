import { useState } from "react";
import { FileText, FlaskConical, Link2, ShieldAlert, ShieldCheck } from "lucide-react";
import { api } from "../api";
import { useApi, useStore } from "../store";
import { ago, dateTime } from "../util";
import { QuickChat } from "../components/Chat";
import { Markdown } from "../components/Markdown";
import { Loading, Panel, ScreenHead, StatusBadge } from "../components/Panel";

export function Research() {
  const rev = useStore((s) => s.rev.agent + s.rev.chat);
  const tasks = useApi(api.researchTasks, [rev]);
  const sources = useApi(api.sources, [rev]);
  const reports = useApi(api.reports, [rev]);
  const [open, setOpen] = useState<string | null>(null);
  const [reportName, setReportName] = useState<string | null>(null);
  const report = useApi(() => (reportName ? api.report(reportName) : Promise.resolve(null)), [reportName]);

  return (
    <>
      <ScreenHead title="Research Workspace" sub="Web research with tracked sources. Every cited link is checked against what was actually retrieved." />
      <div className="grid">
        <Panel className="s12" title="New research" icon={<FlaskConical size={14} />} testid="research-new">
          <QuickChat testid="research-chat" prefix="research " placeholder="Topic to research, e.g. “vector databases for local RAG”…"
            emptyHint="JARVIS searches, reads the top pages and reports with sources. If the search engine is unreachable it says so instead of answering from memory."
            suggestions={[{ label: "Evaluate a GitHub repo", prompt: "evaluate the repo fastapi/fastapi" }, { label: "Compare frameworks", prompt: "research compare Tauri versus Electron" }]} />
        </Panel>

        <Panel className="s7" title="Research tasks" icon={<FlaskConical size={14} />} testid="research-tasks">
          <Loading error={tasks.error} loading={tasks.loading} empty={!tasks.data} />
          {tasks.data && tasks.data.length === 0 && <div className="empty">No research yet.</div>}
          {(tasks.data ?? []).map((t) => {
            const c = t.evaluation?.citations;
            const bad = (c?.unverified.length ?? 0) > 0;
            return (
              <article key={t.id} className={`note ${bad ? "note-warning" : ""}`} data-testid="research-task">
                <button type="button" className="list-item" onClick={() => setOpen(open === t.id ? null : t.id)} aria-expanded={open === t.id}>
                  <span className="grow truncate">{t.title}</span><StatusBadge status={t.status} /><span className="dim">{ago(t.created)}</span>
                </button>
                <div className="row wrap" style={{ fontSize: 12 }} data-testid="citation-check">
                  {c ? (
                    <>
                      {bad ? <ShieldAlert size={13} color="var(--amber)" /> : <ShieldCheck size={13} color="var(--green)" />}
                      <span>{c.retrieved_sources} source(s) retrieved · {c.verified.length} of {c.cited} cited link(s) verified</span>
                      {bad && <span className="badge amber">{c.unverified.length} unverified</span>}
                    </>
                  ) : <span className="dim">No citation data.</span>}
                  {(t.evaluation?.issues ?? []).map((i) => <span key={i} className="badge red">{i}</span>)}
                </div>
                {open === t.id && <Markdown text={t.result || "_No result recorded._"} />}
              </article>
            );
          })}
        </Panel>

        <Panel className="s5" title="Tracked sources" icon={<Link2 size={14} />} scroll testid="research-sources">
          <Loading error={sources.error} loading={sources.loading} empty={!sources.data} />
          {sources.data && sources.data.length === 0 && <div className="empty">No sources retrieved yet. Sources appear here only after JARVIS actually fetches them.</div>}
          <div className="list">
            {(sources.data ?? []).map((s) => (
              <div key={s.id} className="list-item" data-testid="source">
                <span className="badge">{s.kind.replace(/_/g, " ")}</span>
                <a className="grow truncate" href={s.url} target="_blank" rel="noopener noreferrer" title={s.url}>{s.title || s.url}</a>
                <span className="dim">{ago(s.fetched)}</span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel className="s12" title="Saved reports" icon={<FileText size={14} />} testid="research-reports">
          <Loading error={reports.error} loading={reports.loading} empty={!reports.data} />
          {reports.data && reports.data.length === 0 && <div className="empty">No saved reports. Reports are written to JARVIS’s own reports folder when a research agent saves one.</div>}
          <div className="list">
            {(reports.data ?? []).map((r) => (
              <button key={r.name} type="button" className="list-item" onClick={() => setReportName(reportName === r.name ? null : r.name)} data-testid="report">
                <FileText size={13} className="dim" /><span className="grow truncate num">{r.name}</span><span className="dim">{dateTime(r.modified)}</span>
              </button>
            ))}
          </div>
          {reportName && report.data && <Markdown text={report.data.markdown} />}
        </Panel>
      </div>
    </>
  );
}
