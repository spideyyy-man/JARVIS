import { useEffect, useState } from "react";
import { Cpu, Eye, FolderLock, History, Mic, ScrollText, ShieldCheck, ShieldQuestion, Zap } from "lucide-react";
import { api } from "../api";
import type { VoiceSettings } from "../api";
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
