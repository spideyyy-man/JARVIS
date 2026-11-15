import { useEffect, useState } from "react";
import { BookOpenText, FlaskConical, FolderGit2, GitBranch, GitCommitHorizontal, Kanban, ListChecks, Plus, RefreshCw, Save, Sparkles, Trash2, Wrench } from "lucide-react";
import { api } from "../api";
import type { DevTask } from "../api";
import { errText, setState, useApi, useStore } from "../store";
import { ago, basename } from "../util";
import { Bar } from "../components/Charts";
import { QuickChat } from "../components/Chat";
import { Loading, Panel, ScreenHead } from "../components/Panel";

const COLUMNS = [["open", "Open"], ["in_progress", "In progress"], ["done", "Done"]] as const;

function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) return <div className="empty" data-testid="diff-empty">No unstaged changes to tracked files in the working tree.</div>;
  return (
    <pre className="code diff" data-testid="diff">
      {diff.split("\n").map((l, i) => {
        const cls = l.startsWith("+++") || l.startsWith("---") || l.startsWith("diff ") || l.startsWith("index ") ? "meta" : l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
        return <span key={i} className={cls}>{l + "\n"}</span>;
      })}
    </pre>
  );
}

function TaskBoard({ project }: { project: string }) {
  const rev = useStore((s) => s.rev.tool);
  const { data, error, reload } = useApi(api.devTasks, [rev]);
  const [title, setTitle] = useState("");
  const [err, setErr] = useState("");
  const act = async (fn: () => Promise<unknown>) => {
    setErr("");
    try {
      await fn();
      reload();
    } catch (e) {
      setErr(errText(e));
    }
  };
  const tasks = data?.tasks ?? [];
  return (
    <Panel className="s12" title={`Development tasks · ${data?.open ?? 0} open`} icon={<Kanban size={14} />} testid="dev-board">
      <form className="row" onSubmit={(e) => { e.preventDefault(); const t = title.trim(); if (t) { setTitle(""); void act(() => api.addDevTask(t, project)); } }}>
        <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a development task…" aria-label="New task title" data-testid="dev-task-input" />
        <button type="submit" className="btn primary" disabled={!title.trim()} data-testid="dev-task-add"><Plus size={14} /> Add</button>
      </form>
      {(error || err) && <div className="error-text" role="alert">{error || err}</div>}
      <div className="board">
        {COLUMNS.map(([status, label]) => {
          const items = tasks.filter((t: DevTask) => (status === "open" ? !["in_progress", "done"].includes(t.status) : t.status === status));
          return (
            <div key={status} className="board-col" data-testid={`board-${status}`}>
              <div className="row between"><span className="label">{label}</span><span className="num dim">{items.length}</span></div>
              {items.length === 0 && <div className="dim board-empty">Nothing here.</div>}
              {items.map((t) => (
                <div key={t.id} className="board-card" data-testid="dev-task">
                  <div className="board-title">{t.title}</div>
                  {t.notes && <div className="soft" style={{ fontSize: 12 }}>{t.notes}</div>}
                  <div className="row">
                    <span className="dim num grow truncate" style={{ fontSize: 11 }}>{t.project ? basename(t.project) + " · " : ""}{ago(t.updated)}{t.overdue ? " · overdue" : ""}</span>
                    <select className="select board-select" value={status} aria-label={`Status of ${t.title}`} onChange={(e) => void act(() => api.updateDevTask(t.id, e.target.value))} data-testid="dev-task-status">
                      {COLUMNS.map(([s, l]) => <option key={s} value={s}>{l}</option>)}
                    </select>
                    <button type="button" className="btn icon ghost sm" aria-label={`Delete task ${t.title}`} onClick={() => void act(() => api.deleteDevTask(t.id))} data-testid="dev-task-delete"><Trash2 size={13} /></button>
                  </div>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

export function Coding() {
  const settings = useStore((s) => s.settings);
  const project = settings?.default_project ?? "";
  const [path, setPath] = useState(project);
  const [saveErr, setSaveErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => setPath(project), [project]);
  const overview = useApi(() => (project ? api.codingOverview() : Promise.resolve(null)), [project, tick]);
  const diff = useApi(() => (project ? api.codingDiff() : Promise.resolve(null)), [project, tick]);
  const env = useApi(api.devEnv, []);
  const o = overview.data;
  const totalLines = o?.languages.reduce((n, l) => n + l.lines, 0) || 1;

  const save = async (p: string) => {
    setSaving(true);
    setSaveErr("");
    try {
      setState({ settings: await api.putSettings({ default_project: p.trim() }) });
    } catch (e) {
      setSaveErr(errText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <ScreenHead title="Coding Workspace" sub="Repository intelligence, working-tree changes and your development task board.">
        <button type="button" className="btn" onClick={() => setTick((t) => t + 1)}><RefreshCw size={14} /> Refresh</button>
      </ScreenHead>
      <div className="grid">
        <Panel className="s12" title="Project" icon={<FolderGit2 size={14} />} testid="project-picker">
          <form className="row" onSubmit={(e) => { e.preventDefault(); void save(path); }}>
            <input className="input num" value={path} onChange={(e) => setPath(e.target.value)} placeholder="/path/to/project" aria-label="Project path" data-testid="project-path" />
            <button type="submit" className="btn primary" disabled={saving || path.trim() === project} data-testid="project-save"><Save size={14} /> {saving ? "Saving…" : "Set project"}</button>
          </form>
          {saveErr && <div className="error-text" role="alert" data-testid="project-error">{saveErr}</div>}
          {env.data && env.data.projects.length > 0 && (
            <div className="row wrap">
              <span className="label">Detected in {env.data.projects_root}</span>
              {env.data.projects.map((p) => (
                <button type="button" key={p.path} className={`btn sm ${p.path === project ? "primary" : ""}`} onClick={() => void save(p.path)} title={p.path}>
                  {basename(p.path)}{p.git ? " · git" : ""}{p.stack.length ? ` · ${p.stack.join(", ")}` : ""}
                </button>
              ))}
            </div>
          )}
        </Panel>

        <Panel className="s12" title="Ask about this project" icon={<Sparkles size={14} />} testid="coding-actions">
          <QuickChat
            testid="coding-chat"
            placeholder="Ask about the codebase…"
            suggestions={[
              { label: "Explain this codebase", prompt: "inspect my current project and explain this codebase", icon: <BookOpenText size={12} /> },
              { label: "Create a development plan", prompt: "create a development plan for this project", icon: <ListChecks size={12} /> },
              { label: "Run the tests", prompt: "run the tests for this project", icon: <FlaskConical size={12} /> },
            ]}
            emptyHint="Quick actions send a prompt to JARVIS. Running tests executes commands, so it asks for approval first."
          />
        </Panel>

        {!project && <div className="s12 banner">No project is selected. Enter a path above to load the repository overview.</div>}
        <Loading error={overview.error} loading={overview.loading} empty={!o && !!project} />
        {o && (
          <>
            <Panel className="s4 stretch" title="Git" icon={<GitBranch size={14} />} testid="repo-git">
              {o.git ? (
                <>
                  <dl className="kv">
                    <dt>Branch</dt><dd className="num">{o.git.branch}</dd>
                    <dt>Uncommitted</dt><dd className="num">{o.git.uncommitted_changes} change(s)</dd>
                    <dt>Remotes</dt><dd className="num">{o.git.remotes.length ? o.git.remotes.join(", ") : <span className="dim">none</span>}</dd>
                  </dl>
                  {o.git.changed_files.length > 0 && (
                    <>
                      <div className="label">Changed files</div>
                      <div className="chips">{o.git.changed_files.slice(0, 14).map((f) => <span key={f} className="badge mono amber">{f}</span>)}</div>
                    </>
                  )}
                  <div className="label">Recent commits</div>
                  {o.git.recent_commits.length === 0 ? <div className="dim">No commits yet.</div> : (
                    <div className="list">{o.git.recent_commits.map((c, i) => <div key={i} className="list-item"><GitCommitHorizontal size={13} className="dim" /><span className="truncate num" title={c}>{c}</span></div>)}</div>
                  )}
                </>
              ) : <div className="empty">Not a git repository.</div>}
            </Panel>

            <Panel className="s4 stretch" title="Languages" icon={<Wrench size={14} />} testid="repo-languages">
              {o.languages.length === 0 && <div className="empty">No source files detected.</div>}
              {o.languages.map((l) => (
                <div key={l.language} className="lang">
                  <div className="row between"><span>{l.language}</span><span className="num dim">{l.files} files · {l.lines.toLocaleString()} lines</span></div>
                  <Bar value={(l.lines / totalLines) * 100} color="var(--cyan)" />
                </div>
              ))}
              <dl className="kv" style={{ marginTop: 4 }}>
                <dt>Test command</dt><dd className="num" data-testid="test-command">{o.test_command ?? <span className="dim">none detected</span>}</dd>
                <dt>TODO markers</dt><dd className="num">{o.todo_markers}</dd>
              </dl>
            </Panel>

            <Panel className="s4 stretch" title="Layout" icon={<FolderGit2 size={14} />} testid="repo-layout">
              <div className="label">Top level</div>
              <div className="chips">{o.top_level.map((t) => <span key={t} className="badge mono">{t}</span>)}</div>
              <div className="label">Entry points</div>
              {o.entry_points.length === 0 ? <div className="dim">None detected.</div> : <div className="col num soft" style={{ gap: 2, fontSize: 12.5 }}>{o.entry_points.map((e) => <span key={e}>{e}</span>)}</div>}
              <div className="label">Manifests</div>
              {Object.keys(o.manifests).length === 0 ? <div className="dim">None found at the project root.</div> : <div className="chips">{Object.keys(o.manifests).map((m) => <span key={m} className="badge mono cyan">{m}</span>)}</div>}
              {o.readme_excerpt && <><div className="label">README</div><div className="soft" style={{ fontSize: 12.5 }}>{o.readme_excerpt.slice(0, 280)}</div></>}
            </Panel>
          </>
        )}

        <Panel className="s12" title="Working-tree diff" icon={<GitCommitHorizontal size={14} />} testid="repo-diff">
          <Loading error={diff.error} loading={diff.loading} empty={!diff.data && !!project} />
          {diff.data && <DiffView diff={diff.data.diff} />}
        </Panel>

        <TaskBoard project={project} />

        <Panel className="s12" title="Development environment" icon={<Wrench size={14} />} testid="dev-env">
          <Loading error={env.error} loading={env.loading} empty={!env.data} />
          {env.data && (
            <>
              <div className="toolchains">
                {Object.entries(env.data.toolchains).map(([k, v]) => <div key={k} className="toolchain" data-testid="toolchain"><span className="label">{k}</span><span className="num truncate" title={v}>{v}</span></div>)}
              </div>
              <div className="dim num" style={{ fontSize: 12 }}>shell {env.data.shell} · editors {env.data.editors.join(", ") || "none detected"}</div>
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
