import { useEffect, useState } from "react";
import { AppWindow, ArrowUp, Cpu, File, FileSearch, Folder, FolderOpen, HardDrive, ListFilter, RefreshCw, Search, Wrench } from "lucide-react";
import { api } from "../api";
import type { FileContent, FileSearch as FileSearchResult } from "../api";
import { errText, useApi, useStore } from "../store";
import { bytes } from "../util";
import { Bar, LineChart, levelColor } from "../components/Charts";
import { Loading, Panel, ScreenHead } from "../components/Panel";

function Processes() {
  const [sort, setSort] = useState<"cpu" | "memory">("cpu");
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = window.setTimeout(() => setQ(filter.trim()), 250);
    return () => window.clearTimeout(t);
  }, [filter]);
  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), 5000);
    return () => window.clearInterval(t);
  }, []);
  const { data, error, loading } = useApi(() => api.processes(sort, q, 30), [sort, q, tick]);
  return (
    <Panel className="s8" title={`Processes${data ? ` · showing ${data.processes.length} of ${data.total}` : ""}`} icon={<ListFilter size={14} />} testid="processes"
      actions={
        <div className="row">
          <div className="seg" role="group" aria-label="Sort processes">
            <button type="button" className={sort === "cpu" ? "on" : ""} aria-pressed={sort === "cpu"} onClick={() => setSort("cpu")} data-testid="sort-cpu">CPU</button>
            <button type="button" className={sort === "memory" ? "on" : ""} aria-pressed={sort === "memory"} onClick={() => setSort("memory")} data-testid="sort-memory">Memory</button>
          </div>
          <input className="input" style={{ width: 170, height: 26 }} placeholder="Filter by name…" aria-label="Filter processes" value={filter} onChange={(e) => setFilter(e.target.value)} data-testid="process-filter" />
        </div>
      }>
      <Loading error={error} loading={loading} empty={!data} />
      <div className="table-wrap" style={{ maxHeight: 380 }}>
        <table className="table">
          <thead><tr><th className="r">PID</th><th>Name</th><th>User</th><th className="r">CPU %</th><th className="r">Mem %</th><th>Status</th><th style={{ width: "38%" }}>Command</th></tr></thead>
          <tbody>
            {data?.processes.map((p) => (
              <tr key={p.pid} data-testid="process-row">
                <td className="r num dim">{p.pid}</td>
                <td className="truncate" style={{ maxWidth: 150 }}>{p.name}</td>
                <td className="soft">{p.user}</td>
                <td className="r num" style={{ color: p.cpu_percent >= 50 ? levelColor(90) : undefined }}>{p.cpu_percent.toFixed(1)}</td>
                <td className="r num">{p.memory_percent.toFixed(2)}</td>
                <td className="dim">{p.status}</td>
                <td className="cell-clip num dim" title={p.command}>{p.command || "—"}</td>
              </tr>
            ))}
            {data?.processes.length === 0 && <tr><td colSpan={7} className="dim">No process matches “{q}”.</td></tr>}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Explorer() {
  const [path, setPath] = useState("");
  const [hidden, setHidden] = useState(false);
  const [preview, setPreview] = useState<FileContent | null>(null);
  const [previewError, setPreviewError] = useState("");
  const { data, error, loading } = useApi(() => api.listDir(path, hidden), [path, hidden]);
  const [pathInput, setPathInput] = useState("");
  useEffect(() => {
    if (data) setPathInput(data.path);
  }, [data]);
  const open = async (p: string) => {
    setPreviewError("");
    try {
      setPreview(await api.readFile(p));
    } catch (e) {
      setPreview(null);
      setPreviewError(`${p}: ${errText(e)}`);
    }
  };
  const parent = data ? data.path.replace(/\/[^/]+\/?$/, "") || "/" : "";
  const entries = data ? [...data.entries].sort((a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name)) : [];
  return (
    <Panel className="s12" title="File explorer" icon={<FolderOpen size={14} />} testid="explorer"
      actions={<label className="row dim" style={{ fontSize: 12 }}><input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} /> hidden files</label>}>
      <form className="row" onSubmit={(e) => { e.preventDefault(); setPath(pathInput.trim()); }}>
        <button type="button" className="btn icon" onClick={() => setPath(parent)} disabled={!data} aria-label="Go to parent folder" data-testid="explorer-up"><ArrowUp size={14} /></button>
        <input className="input num" value={pathInput} onChange={(e) => setPathInput(e.target.value)} aria-label="Folder path" data-testid="explorer-path" />
        <button type="submit" className="btn">Go</button>
      </form>
      {error && <div className="error-text" role="alert" data-testid="explorer-error">{error}</div>}
      <div className="explorer">
        <div className="explorer-list table-wrap">
          <table className="table">
            <thead><tr><th>Name</th><th className="r">Size</th><th>Modified</th></tr></thead>
            <tbody>
              {loading && !data && <tr><td colSpan={3} className="dim">Loading…</td></tr>}
              {entries.map((e) => (
                <tr key={e.path} data-testid="explorer-entry" data-dir={e.is_dir}>
                  <td>
                    <button type="button" className="link-btn" onClick={() => (e.is_dir ? setPath(e.path) : void open(e.path))}>
                      {e.is_dir ? <Folder size={14} className="ico-dir" /> : <File size={14} className="dim" />} <span className="truncate">{e.name}</span>
                    </button>
                  </td>
                  <td className="r num dim">{e.is_dir ? "—" : bytes(e.size)}</td>
                  <td className="num dim">{e.modified.replace("T", " ")}</td>
                </tr>
              ))}
              {data && entries.length === 0 && <tr><td colSpan={3} className="dim">This folder is empty.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="explorer-preview" data-testid="file-preview">
          {previewError && <div className="error-text" role="alert">{previewError}</div>}
          {!preview && !previewError && <div className="empty">Select a file to preview its text content.</div>}
          {preview && (
            <>
              <div className="row between">
                <span className="num truncate" title={preview.path}>{preview.path}</span>
                <span className="dim num">{preview.binary ? `binary · ${bytes(preview.size ?? 0)}` : `${preview.total_chars} chars${preview.truncated ? " · truncated" : ""}`}</span>
              </div>
              {preview.binary ? <div className="empty">Binary file — no text preview.</div> : <pre className="code" style={{ maxHeight: 330 }}>{preview.content}</pre>}
            </>
          )}
        </div>
      </div>
    </Panel>
  );
}

function FileSearchPanel() {
  const [form, setForm] = useState({ q: "", root: "", ext: "", days: "", text: "" });
  const [res, setRes] = useState<FileSearchResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      setRes(await api.searchFiles({ q: form.q, root: form.root, ext: form.ext, text: form.text, days: form.days ? Number(form.days) : undefined }));
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel className="s12" title="File search" icon={<FileSearch size={14} />} testid="file-search">
      <form className="search-form" onSubmit={run}>
        <label className="field"><span>Name contains</span><input className="input" value={form.q} onChange={set("q")} placeholder="budget report" data-testid="fs-q" /></label>
        <label className="field"><span>Folder</span><input className="input num" value={form.root} onChange={set("root")} placeholder="default: home" /></label>
        <label className="field"><span>Extensions</span><input className="input num" value={form.ext} onChange={set("ext")} placeholder="py,md" data-testid="fs-ext" /></label>
        <label className="field"><span>Modified within (days)</span><input className="input num" type="number" min={0} value={form.days} onChange={set("days")} placeholder="any" /></label>
        <label className="field"><span>Text inside</span><input className="input" value={form.text} onChange={set("text")} placeholder="optional" /></label>
        <button type="submit" className="btn primary" disabled={busy} data-testid="fs-run"><Search size={14} /> {busy ? "Searching…" : "Search"}</button>
      </form>
      {error && <div className="error-text" role="alert">{error}</div>}
      {res && (
        <div data-testid="fs-results">
          <div className="dim num" style={{ fontSize: 12, marginBottom: 4 }}>{res.matched} match(es) · {res.scanned} files scanned in {res.root}{res.timed_out ? " · stopped at time limit" : ""}</div>
          <div className="table-wrap" style={{ maxHeight: 260 }}>
            <table className="table">
              <thead><tr><th>Path</th><th className="r">Size</th><th>Modified</th></tr></thead>
              <tbody>
                {res.results.map((r) => (
                  <tr key={r.path}><td className="num">{r.path}{r.match && <div className="dim">{r.match}</div>}</td><td className="r num dim">{bytes(r.size)}</td><td className="num dim">{r.modified.replace("T", " ")}</td></tr>
                ))}
                {res.results.length === 0 && <tr><td colSpan={3} className="dim">No files matched.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Panel>
  );
}

export function Computer() {
  const history = useStore((s) => s.history);
  const live = useStore((s) => s.metrics);
  const [tick, setTick] = useState(0);
  const stats = useApi(api.systemStats, [tick]);
  const disks = useApi(api.disks, [tick]);
  const apps = useApi(api.apps, []);
  const [appFilter, setAppFilter] = useState("");
  const m = live ?? stats.data;
  const s = stats.data;
  const span = history.length > 1 ? `last ${Math.round((history[history.length - 1].ts - history[0].ts) / 60) || 1} min` : undefined;
  const realDisks = disks.data?.partitions.filter((p) => p.total_gb > 0) ?? [];
  const f = appFilter.trim().toLowerCase();

  return (
    <>
      <ScreenHead title="Computer Intelligence" sub={s ? `${s.platform} · up ${s.uptime_hours.toFixed(1)} h` : "Live performance, processes, storage, software and files"}>
        <button type="button" className="btn" onClick={() => setTick((t) => t + 1)}><RefreshCw size={14} /> Refresh</button>
      </ScreenHead>
      <div className="grid">
        <Panel className="s4" title="CPU" icon={<Cpu size={14} />} testid="chart-cpu" actions={<span className="num chart-now" style={{ color: levelColor(m?.cpu.percent ?? 0) }}>{m ? `${m.cpu.percent.toFixed(1)}%` : "—"}</span>}>
          <LineChart values={history.map((p) => p.cpu)} color={levelColor(m?.cpu.percent ?? 0)} label="CPU usage history" span={span} />
          <div className="dim num" style={{ fontSize: 12 }}>{m ? `${m.cpu.cores} cores · load ${m.cpu.load_avg.map((x) => x.toFixed(2)).join(" ")}` : ""}{s?.history.cpu_avg !== undefined ? ` · avg ${s.history.cpu_avg}% · peak ${s.history.cpu_max}%` : ""}</div>
        </Panel>
        <Panel className="s4" title="Memory" icon={<Cpu size={14} />} testid="chart-memory" actions={<span className="num chart-now" style={{ color: levelColor(m?.memory.percent ?? 0) }}>{m ? `${m.memory.percent.toFixed(1)}%` : "—"}</span>}>
          <LineChart values={history.map((p) => p.memory)} color="var(--blue)" label="Memory usage history" span={span} />
          <div className="dim num" style={{ fontSize: 12 }}>{m ? `${m.memory.used_gb.toFixed(2)} of ${m.memory.total_gb.toFixed(2)} GB used · swap ${m.swap.percent.toFixed(0)}%` : ""}</div>
        </Panel>
        <Panel className="s4 stretch" title="Health assessment" icon={<Wrench size={14} />} testid="assessment">
          <Loading error={stats.error} loading={stats.loading} empty={!s} />
          {s?.assessment.map((a, i) => <div key={i} className="banner info">{a}</div>)}
          {m && (
            <dl className="kv num">
              <dt>Disk</dt><dd>{m.disk.percent.toFixed(1)}% used · {m.disk.free_gb.toFixed(1)} GB free</dd>
              <dt>Network</dt><dd>↑ {m.net.sent_mb.toFixed(1)} MB · ↓ {m.net.recv_mb.toFixed(1)} MB</dd>
              <dt>Samples</dt><dd>{s?.history.samples ?? history.length} over {s?.history.window_minutes ?? "—"} min</dd>
            </dl>
          )}
        </Panel>

        <Processes />
        <Panel className="s4" title="Disks" icon={<HardDrive size={14} />} testid="disks">
          <Loading error={disks.error} loading={disks.loading} empty={!disks.data} />
          {realDisks.map((p) => (
            <div key={p.mount} className="disk" data-testid="disk-row">
              <div className="row between"><span className="num truncate" title={p.mount}>{p.mount}</span><span className="num soft">{p.percent.toFixed(0)}%</span></div>
              <Bar value={p.percent} />
              <div className="dim num" style={{ fontSize: 11.5 }}>{p.fs} · {p.free_gb.toFixed(1)} GB free of {p.total_gb.toFixed(1)} GB</div>
            </div>
          ))}
          {disks.data && disks.data.partitions.length > realDisks.length && <div className="dim" style={{ fontSize: 11.5 }}>{disks.data.partitions.length - realDisks.length} zero-size pseudo filesystem(s) hidden</div>}
        </Panel>

        <Explorer />
        <FileSearchPanel />

        <Panel className="s12" title="Installed software" icon={<AppWindow size={14} />} testid="apps"
          actions={<input className="input" style={{ width: 200, height: 26 }} placeholder="Filter…" aria-label="Filter software" value={appFilter} onChange={(e) => setAppFilter(e.target.value)} />}>
          <Loading error={apps.error} loading={apps.loading} empty={!apps.data} />
          {apps.data && (
            <div className="apps">
              <div>
                <div className="label">Developer tools on PATH · {apps.data.developer_tools_on_path.length}</div>
                <div className="chips" style={{ marginTop: 6 }}>
                  {apps.data.developer_tools_on_path.filter((t) => !f || t.name.toLowerCase().includes(f)).map((t) => <span key={t.name} className="badge mono cyan" title={t.path} data-testid="dev-tool">{t.name}</span>)}
                </div>
              </div>
              <div>
                <div className="label">Desktop applications · {apps.data.desktop_applications.length}</div>
                <div className="chips" style={{ marginTop: 6 }}>
                  {apps.data.desktop_applications.filter((a) => !f || a.name.toLowerCase().includes(f)).map((a) => <span key={a.source} className="badge" title={a.exec}>{a.name}</span>)}
                  {apps.data.desktop_applications.length === 0 && <span className="dim">None detected.</span>}
                </div>
              </div>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
