import { useEffect, useState } from "react";
import { Cpu, Eye, FolderLock, History, Mic, ScrollText, ShieldCheck, ShieldQuestion, Zap } from "lucide-react";
import { api } from "../api";
import type { DoctorResult, VoiceSettings } from "../api";
import { errText, useApi, useStore } from "../store";
import { dateTime } from "../util";
import { Loading, Panel, ScreenHead, StatusBadge, Switch } from "../components/Panel";

function useVoices(): SpeechSynthesisVoice[] {
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  useEffect(() => {
    const synth = window.speechSynthesis;
    if (!synth) return;
    const load = () => setVoices(synth.getVoices());
    load();
    synth.addEventListener?.("voiceschanged", load);
    return () => synth.removeEventListener?.("voiceschanged", load);
  }, []);
  return voices;
}

export function SettingsScreen() {
  const rev = useStore((s) => s.rev.approval + s.rev.tool);
  const settings = useApi(api.settings, []);
  const tools = useApi(api.tools, []);
  const audit = useApi(() => api.audit(120), [rev]);
  const history = useApi(() => api.approvals("all"), [rev]);
  const grants = useApi(api.grants, [rev]);
  const [doc, setDoc] = useState<DoctorResult[] | null>(null);
  const [docBusy, setDocBusy] = useState(false);
  const [docErr, setDocErr] = useState("");
  const runDoctor = async () => {
    setDocBusy(true);
    setDocErr("");
    try {
      setDoc(await api.doctor());
    } catch (e) {
      setDocErr(errText(e));
    } finally {
      setDocBusy(false);
    }
  };
  const voices = useVoices();
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState("");
  const [wake, setWake] = useState<string | null>(null);
  const s = settings.data;

  const put = async (body: Parameters<typeof api.putSettings>[0], note: string) => {
    setErr("");
    setSaved("");
    try {
      await api.putSettings(body);
      setSaved(note);
    } catch (e) {
      setErr(errText(e));
    }
    settings.reload();
    tools.reload();
  };
  const toggleTrust = (name: string, on: boolean) => {
    const current = new Set(s?.trusted_tools ?? []);
    if (on) current.add(name); else current.delete(name);
    void put({ trusted_tools: [...current] }, on ? `${name} now runs without asking.` : `${name} requires approval again.`);
  };
  const voice = (patch: Partial<VoiceSettings>) => void put({ voice: patch }, "Voice settings saved.");

  return (
    <>
      <ScreenHead title="Settings and Permissions" sub="What JARVIS may do on its own, what needs your approval, and a record of everything it did." />
      {err && <div className="banner error-text" role="alert" data-testid="settings-error">{err}</div>}
      {saved && <div className="note note-success" role="status" data-testid="settings-saved">{saved}</div>}
      <div className="grid">
        <Panel className="s4 stretch" title="Level 1 · Read" icon={<Eye size={14} />} testid="level-read">
          <div className="stat"><b>{s?.levels.read ?? "—"}</b><span className="dim">tools</span></div>
          <p className="soft">Inspects information and never modifies anything. Runs automatically.</p>
        </Panel>
        <Panel className="s4 stretch" title="Level 2 · Confirm" icon={<ShieldQuestion size={14} />} testid="level-confirm">
          <div className="stat"><b>{s?.levels.confirm ?? "—"}</b><span className="dim">tools</span></div>
          <p className="soft">Changes something. JARVIS prepares the action, shows exactly what will happen, and waits for your approval.</p>
        </Panel>
        <Panel className="s4 stretch" title="Level 3 · Trusted automation" icon={<Zap size={14} />} testid="level-trusted">
          <div className="stat"><b>{s?.trusted_tools.length ?? "—"}</b><span className="dim">of {s?.levels.trust_eligible.length ?? "—"} eligible allowlisted</span></div>
          <p className="soft">Only low-risk Confirm tools can be allowlisted. Deleting, overwriting, running commands and committing can never be.</p>
        </Panel>

        <Panel className="s12" title="Capabilities" icon={<ShieldQuestion size={14} />} testid="capabilities">
          <p className="soft">High-impact abilities. All are off until you switch them on here; JARVIS cannot switch them on itself. Even when on, every click, keystroke and form entry still waits for your approval.</p>
          {Object.entries(s?.capabilities ?? {}).map(([name, c]) => (
            <div key={name} className="row between cap-row" data-testid="capability" data-cap={name}>
              <div><b>{name.replace(/_/g, " ")}</b><div className="soft">{c.description}</div></div>
              <Switch checked={c.enabled} label={`Allow ${name.replace(/_/g, " ")}`} onChange={(v) => void put({ capabilities: { [name]: v } }, `${name.replace(/_/g, " ")} ${v ? "enabled" : "disabled"}.`)} />
            </div>
          ))}
        </Panel>

        <Panel className="s6" title="Behaviour" icon={<Zap size={14} />} testid="behaviour">
          {s && (
            <div className="col" style={{ gap: 0 }}>
              {([["fast_path", "Instant answers for simple commands", "When a model is configured, recognisable commands (“turn the volume up”) skip it and run immediately."],
                 ["auto_continue", "Continue a task after I approve", "After you approve or deny a step, JARVIS picks the task back up instead of stopping."],
                 ["auto_memory", "Learn from what I say", "Remembers lasting facts you state in passing (“I prefer…”, “my manager is…”). Review them in Knowledge."]] as const).map(([key, title, desc]) => (
                <div key={key} className="row between cap-row" data-testid="behaviour-row" data-key={key}>
                  <div><b style={{ textTransform: "none" }}>{title}</b><div className="soft">{desc}</div></div>
                  <Switch checked={s.behavior[key]} label={title} onChange={(v) => void put({ behavior: { [key]: v } }, `${title}: ${v ? "on" : "off"}.`)} />
                </div>
              ))}
              <dl className="kv" style={{ marginTop: 10 }}><dt>Agent mode</dt><dd className="num" data-testid="agent-mode">{s.agent_mode.active} <span className="dim">(configured: {s.agent_mode.configured})</span></dd></dl>
              <div className="dim" style={{ fontSize: 12 }}>flat = one agent with every tool (fewer model calls, best for a slow local model); team = orchestrator plus specialists with separated permissions. Set with JARVIS_AGENT_MODE.</div>
            </div>
          )}
        </Panel>

        <Panel className="s6" title="Diagnostics" icon={<Cpu size={14} />} testid="diagnostics"
          actions={<button type="button" className="btn sm" disabled={docBusy} onClick={() => void runDoctor()} data-testid="run-doctor">{docBusy ? "Checking…" : "Run checks"}</button>}>
          {!doc && !docBusy && <div className="dim">Checks this machine: the model (including whether it can call tools), screen reading, mouse/keyboard, browser, speech, calendar and email.</div>}
          {docBusy && <div className="dim">Running. The model check can take a while on a CPU.</div>}
          {docErr && <div className="error-text" role="alert">{docErr}</div>}
          {doc?.map((r) => (
            <div key={r.name} className={`note ${r.status === "fail" ? "note-error" : r.status === "warn" ? "note-warning" : "note-success"}`} data-testid="doctor-row" data-status={r.status}>
              <b>{r.name}</b> <StatusBadge status={r.status === "ok" ? "ok" : r.status === "fail" ? "failed" : "pending"} />
              <div className="soft">{r.detail}</div>
              {r.fix && <div className="dim">Fix: {r.fix}</div>}
            </div>
          ))}
        </Panel>

        <Panel className="s12" title="Task approvals in effect" icon={<History size={14} />} testid="grants"
          actions={(grants.data?.length ?? 0) > 0 ? <button type="button" className="btn danger sm" onClick={() => void api.revokeGrants().then(grants.reload)} data-testid="revoke-grants">Revoke all</button> : undefined}>
          {(grants.data?.length ?? 0) === 0 ? <div className="dim">None. When you choose “Approve all … for this task”, that tool stops asking in that conversation for 30 minutes and appears here.</div> : (
            <table className="table"><thead><tr><th>Tool</th><th>Conversation</th><th>Expires</th></tr></thead>
              <tbody>{grants.data!.map((g) => <tr key={g.id} data-testid="grant-row"><td className="num">{g.tool}</td><td className="cell-clip">{g.title || g.conversation_id}</td><td className="num dim">{dateTime(g.expires)}</td></tr>)}</tbody></table>
          )}
        </Panel>

        <Panel className="s8" title="Tool permissions" icon={<ShieldCheck size={14} />} testid="tool-permissions">
          <Loading error={tools.error} loading={tools.loading} empty={!tools.data} />
          {tools.data && (
            <table className="table">
              <thead><tr><th>Tool</th><th>Level</th><th>Used by</th><th>Run without asking</th></tr></thead>
              <tbody>
                {[...tools.data].sort((a, b) => (a.level === b.level ? a.name.localeCompare(b.name) : a.level === "confirm" ? -1 : 1)).map((t) => (
                  <tr key={t.name} data-testid="tool-row" data-tool={t.name}>
                    <td title={t.description}><span className="num">{t.name}</span></td>
                    <td><span className={`badge ${t.level === "confirm" ? "amber" : "cyan"}`}>{t.level}</span></td>
                    <td className="dim">{t.agents.join(", ") || "—"}</td>
                    <td>
                      {t.level === "read" ? <span className="dim">always (read-only)</span>
                        : t.trust_eligible ? <Switch checked={t.trusted} label={`Run ${t.name} without asking`} onChange={(v) => toggleTrust(t.name, v)} />
                        : <span className="dim">never - always asks</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <div className="s4 col" style={{ gap: 14 }}>
          <Panel title="Language model" icon={<Cpu size={14} />} testid="llm-settings">
            <Loading error={settings.error} loading={settings.loading} empty={!s} />
            {s && (
              <>
                <div className="chips">{s.llm.chain.map((p, i) => <span key={p.name} className={`badge ${i === 0 ? "cyan" : ""}`}>{i + 1}. {p.name} · {p.model}</span>)}</div>
                {s.llm.degraded
                  ? <div className="note note-warning" data-testid="llm-degraded">Running on the offline rule engine only. It handles a fixed set of requests and cannot reason. Set <span className="num">ANTHROPIC_API_KEY</span> (or a local model URL) and restart.</div>
                  : <div className="note note-success">A language model is configured. The offline engine is the automatic fallback.</div>}
                <dl className="kv"><dt>Embeddings</dt><dd className="num">{s.embedder}</dd><dt>Web search</dt><dd className="num">{s.search_provider}</dd><dt>Speech</dt><dd className="num" data-testid="speech-providers">stt {s.speech.stt} · tts {s.speech.tts}</dd><dt>Images</dt><dd className="num">{s.image_provider}</dd></dl>
              </>
            )}
          </Panel>

          <Panel title="File access" icon={<FolderLock size={14} />} testid="file-access">
            <p className="soft">JARVIS can only see inside these folders. Credential files are never readable. Change with <span className="num">JARVIS_ALLOWED_ROOTS</span>.</p>
            <div className="col num" style={{ gap: 3 }}>{(s?.allowed_roots ?? []).map((r) => <span key={r} data-testid="allowed-root">{r}</span>)}</div>
            <dl className="kv"><dt>Data</dt><dd className="num">{s?.data_dir}</dd></dl>
          </Panel>

          <Panel title="Voice" icon={<Mic size={14} />} testid="voice-settings">
            {s && (
              <div className="col" style={{ gap: 10 }}>
                <form className="row" onSubmit={(e) => { e.preventDefault(); if (wake?.trim()) voice({ wake_word: wake.trim().toLowerCase() }); }}>
                  <label className="field grow"><span>Wake word</span><input className="input" value={wake ?? s.voice.wake_word} onChange={(e) => setWake(e.target.value)} data-testid="wake-word" /></label>
                  <button type="submit" className="btn" disabled={!wake?.trim() || wake.trim().toLowerCase() === s.voice.wake_word} data-testid="wake-save">Save</button>
                </form>
                <label className="field"><span>Voice</span>
                  <select className="select" value={s.voice.voice_name} onChange={(e) => voice({ voice_name: e.target.value })} data-testid="voice-name">
                    <option value="">Browser default</option>
                    {voices.map((v) => <option key={v.name} value={v.name}>{v.name} ({v.lang})</option>)}
                  </select>
                </label>
                <label className="field"><span>Speaking rate · {s.voice.rate.toFixed(2)}×</span>
                  <input type="range" min={0.6} max={1.6} step={0.05} defaultValue={s.voice.rate} onMouseUp={(e) => voice({ rate: Number(e.currentTarget.value) })} onKeyUp={(e) => voice({ rate: Number(e.currentTarget.value) })} aria-label="Speaking rate" />
                </label>
                <div className="row between"><span>Speak replies aloud</span><Switch checked={s.voice.speak_replies} label="Speak replies aloud" onChange={(v) => voice({ speak_replies: v })} /></div>
                <div className="row between"><span>Listen for the wake word as soon as JARVIS opens</span><Switch checked={!!s.voice.wake_on_start} label="Wake word on startup" onChange={(v) => voice({ wake_on_start: v })} /></div>
                <label className="field"><span>Daily spoken briefing at (HH:MM, empty = off)</span>
                  <input className="input num" defaultValue={s.proactive.briefing_time} placeholder="08:30" data-testid="briefing-time"
                    onBlur={(e) => { if (e.target.value.trim() !== s.proactive.briefing_time) void put({ proactive: { briefing_time: e.target.value.trim() } }, "Briefing time saved."); }} />
                </label>
                <div className="row between"><span>Continuous conversation</span><Switch checked={s.voice.continuous} label="Continuous conversation" onChange={(v) => voice({ continuous: v })} /></div>
              </div>
            )}
          </Panel>
        </div>

        <Panel className="s12" title="Approval history" icon={<History size={14} />} testid="approval-history">
          <Loading error={history.error} loading={history.loading} empty={!history.data} />
          {history.data && history.data.length === 0 && <div className="empty">No actions have needed approval yet.</div>}
          {history.data && history.data.length > 0 && (
            <table className="table">
              <thead><tr><th>Requested</th><th>Tool</th><th>Agent</th><th>Action</th><th>Outcome</th></tr></thead>
              <tbody>
                {history.data.map((a) => (
                  <tr key={a.id} data-testid="approval-row"><td className="num dim" style={{ whiteSpace: "nowrap" }}>{dateTime(a.created)}</td><td className="num">{a.tool}</td><td>{a.agent}</td>
                    <td className="cell-clip" title={a.preview}>{a.preview.split("\n")[0]}</td><td><StatusBadge status={a.status} /></td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel className="s12" title="Audit log" icon={<ScrollText size={14} />} testid="audit-log">
          <Loading error={audit.error} loading={audit.loading} empty={!audit.data} />
          {audit.data && audit.data.length === 0 && <div className="empty">Nothing recorded yet.</div>}
          {audit.data && audit.data.length > 0 && (
            <table className="table">
              <thead><tr><th>Time</th><th>Actor</th><th>Tool</th><th>Level</th><th>Decision</th><th>Outcome</th><th>Arguments</th></tr></thead>
              <tbody>
                {audit.data.map((a) => (
                  <tr key={a.id} data-testid="audit-row"><td className="num dim" style={{ whiteSpace: "nowrap" }}>{dateTime(a.ts)}</td><td>{a.actor}</td><td className="num">{a.tool}</td>
                    <td><span className={`badge ${a.level === "confirm" ? "amber" : "cyan"}`}>{a.level}</span></td><td>{a.decision}</td><td><StatusBadge status={a.outcome} /></td>
                    <td className="cell-clip num dim" title={typeof a.args === "string" ? a.args : JSON.stringify(a.args)}>{typeof a.args === "string" ? a.args : JSON.stringify(a.args)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </>
  );
}
