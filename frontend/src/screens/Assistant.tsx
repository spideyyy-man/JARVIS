import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Ear, Mic, Plus, SendHorizontal, Square, Volume2, VolumeX } from "lucide-react";
import { api } from "../api";
import type { AssistantState } from "../api";
import { errText, setState, usePendingApprovals, useStore, useVoice, voice } from "../store";
import { AssistantCore } from "../components/AssistantCore";
import { Markdown } from "../components/Markdown";
import { ApprovalCard } from "../components/ApprovalCard";
import { Switch } from "../components/Panel";

const CAPTION: Record<AssistantState, string> = {
  idle: "Standing by", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…", executing: "Working…", error: "Something went wrong",
};
const getLevel = () => voice.getLevel();
const LANGS = [{ code: "auto", label: "Auto" }, { code: "en-IN", label: "English" }, { code: "ta-IN", label: "தமிழ்" }] as const;

/** The whole assistant on one screen: talk (wake word, hotkey or mic), read the conversation, approve actions. */
export function Assistant({ listen = false }: { listen?: boolean }) {
  const v = useVoice();
  const server = useStore((s) => s.assistantState);
  const settings = useStore((s) => s.settings);
  const pending = usePendingApprovals();
  const [typed, setTyped] = useState("");
  const [saveError, setSaveError] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const vs = settings?.voice;
  const wake = v.local ? "Jarvis" : (vs?.wake_word || "jarvis").split(",")[0].trim();
  const speakReplies = vs?.speak_replies !== false;
  const lang = vs?.language === "auto" ? "auto" : vs?.language?.startsWith("ta") ? "ta-IN" : "en-IN";

  // the server's "speaking" counts too: with the offline listener JARVIS says the reply itself after the page went idle
  const state: AssistantState = v.phase === "thinking" ? (server === "executing" ? "executing" : "thinking") : v.phase !== "idle" ? v.phase : server === "error" || server === "speaking" ? server : "idle";
  const busy = v.phase === "thinking" || v.phase === "speaking";
  const timers = useStore((s) => s.timers);
  const nextTimer = useMemo(() => {
    const t = timers.filter((x) => x.kind === "timer" && x.seconds).sort((a, b) => a.due - b.due)[0];
    return t ? { due: t.due, seconds: t.seconds as number } : null;
  }, [timers]);

  // Opened by the hotkey/tray while no window existed (#/assistant/listen): start listening once, then drop the flag.
  useEffect(() => {
    if (!listen) return;
    const t = setTimeout(() => voice.listenNow(), 400);
    history.replaceState(null, "", "#/assistant");
    return () => clearTimeout(t);
  }, [listen]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [v.turns.length, v.reply, pending.length, v.interim]);

  const saveVoice = async (patch: { speak_replies?: boolean; language?: string; engine?: "local" | "browser" }) => {
    setSaveError("");
    voice.configure(patch);
    try {
      const next = await api.putSettings({ voice: patch });
      setState({ settings: next });
      voice.configure(next.voice);
    } catch (e) {
      setSaveError(errText(e));
    }
  };

  const live = v.interim;
  return (
    <div className="asst" data-testid="assistant" data-phase={v.phase}>
      <section className="asst-stage" data-testid="voice-stage">
        <AssistantCore state={state} following={v.local && v.localState === "following"} approval={pending.length > 0} timer={nextTimer} level={getLevel} />
        <div className="asst-caption" data-testid="voice-caption">{CAPTION[state]}</div>
        <div className="asst-chips">
          <span className={`badge ${v.wakeMode || v.local ? "cyan" : ""}`} data-testid="wake-indicator" data-wake={v.wakeMode || v.local ? "armed" : "off"}>
            <Ear size={12} /> {v.local ? `say “${wake}” · offline` : v.wakeMode ? `say “${wake}”` : "wake word off"}
          </span>
          {v.local ? <span className="badge" title="Whisper hears English and Tamil and tells them apart by itself">EN / தமிழ் auto</span> : (
          <div className="seg" role="group" aria-label="Language">
            {LANGS.map((l) => (
              <button key={l.code} type="button" className={lang === l.code ? "on" : ""} aria-pressed={lang === l.code} onClick={() => void saveVoice({ language: l.code })} data-testid={`lang-${l.code}`}>
                {l.label}{l.code === "auto" && lang === "auto" ? <span className="dim"> · {v.listenLang.startsWith("ta") ? "த" : "EN"}</span> : null}
              </button>
            ))}
          </div>)}
        </div>
        <TimerChips />
      </section>

      {!v.sttSupported && (
        <div className="banner" role="alert" data-testid="stt-unsupported"><AlertTriangle size={16} /><div>Speech recognition is not available in this browser. Type below instead.</div></div>
      )}
      {v.error && <div className="banner red" role="alert"><AlertTriangle size={16} /><div>{v.error}</div></div>}
      {saveError && <div className="error-text" role="alert">{saveError}</div>}

      <div className="asst-log" ref={scroller} data-testid="voice-turns" aria-live="polite">
        {v.turns.length === 0 && !v.reply && !live && (
          <div className="asst-empty dim">
            <p>Say <b>“{wake}”</b> and your request, press <b>Ctrl+Alt+J</b>, or tap the mic.</p>
            <div className="asst-try" data-testid="try-chips">
              {TRY.map((t) => (
                <button key={t} type="button" className="try-chip" onClick={() => voice.sendText(t)}>{t}</button>
              ))}
            </div>
            {v.local && <p>Speak English or Tamil - the offline listener tells them apart. Nothing leaves this computer.</p>}
            {!v.local && lang === "auto" && <p>Auto language: say <b>“Jarvis, Tamil”</b> or <b>“Vanakkam Jarvis”</b> to talk in Tamil, <b>“Jarvis, English”</b> to switch back. Typing in either language switches too.</p>}
          </div>
        )}
        {v.turns.map((t, i) => (
          <div key={i} className={`asst-msg ${t.role}`}>
            {t.role === "user" ? <span>{t.text}</span> : <Markdown text={t.text} />}
          </div>
        ))}
        {v.reply && v.replyStreaming && <div className="asst-msg assistant" data-testid="voice-reply" data-streaming="true"><Markdown text={v.reply} /></div>}
        {!v.reply && v.replyStreaming && <div className="asst-msg assistant"><span className="typing" aria-label="Thinking"><i /><i /><i /></span></div>}
        {live && <div className="asst-msg user interim" data-testid="voice-interim">{live}</div>}
        {pending.map((a) => <ApprovalCard key={a.id} approval={a} />)}
      </div>

      <div className="asst-controls">
        <button type="button" className="btn icon ghost" onClick={() => voice.newSession()} aria-label="New conversation" title="New conversation"><Plus size={16} /></button>
        <button type="button" className={`mic-btn ${v.capturing ? "on" : ""}`} onClick={() => voice.toggleMic()} disabled={!v.sttSupported} data-testid="mic-btn"
          aria-pressed={v.capturing} aria-label={busy ? "Interrupt and talk" : v.capturing ? "Stop listening" : "Talk"}>
          {v.capturing ? <Square size={22} /> : <Mic size={24} />}
        </button>
        <button type="button" className="btn icon ghost" onClick={() => voice.interrupt()} disabled={!busy} aria-label="Stop" title="Stop" data-testid="voice-stop"><Square size={14} /></button>
      </div>

      <form className="asst-composer" onSubmit={(e) => { e.preventDefault(); voice.sendText(typed); setTyped(""); }}>
        <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Type a request…" aria-label="Type a request" data-testid="voice-typed" />
        <button type="submit" className="btn primary icon" disabled={!typed.trim()} aria-label="Send"><SendHorizontal size={15} /></button>
      </form>

      <div className="asst-toggles">
        {settings?.voice_local && !settings.voice_local.unavailable ? (
          <label className="toggle-row">
            <Switch checked={(vs?.engine ?? "local") === "local"} onChange={(on) => void saveVoice({ engine: on ? "local" : "browser" })} label="Offline listener" />
            <span>Offline listener (“Jarvis”)</span>
          </label>
        ) : null}
        {!v.local && (
          <label className="toggle-row">
            <Switch checked={v.wakeMode} onChange={(on) => voice.setWakeMode(on)} label="Wake word listening" disabled={!v.sttSupported} />
            <span>Listen for “{wake}”</span>
          </label>
        )}
        <label className="toggle-row">
          <Switch checked={speakReplies} onChange={(on) => void saveVoice({ speak_replies: on })} label="Speak replies" disabled={!v.ttsSupported} />
          <span>{speakReplies ? <Volume2 size={13} /> : <VolumeX size={13} />} Speak replies</span>
        </label>
      </div>
    </div>
  );
}

const TRY = ["what's the weather", "set a timer for 5 minutes", "add milk to my shopping list", "what is 15% of 240",
  "convert 5 km to miles", "play some lo-fi music", "open notepad", "நேரம் என்ன"];

function clock(left: number): string {
  const s = Math.max(0, Math.round(left));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

/** Running timers (live countdown, with a thin progress bar) and alarms; × cancels one. */
function TimerChips() {
  const timers = useStore((s) => s.timers);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!timers.length) return;
    const id = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [timers.length]);
  if (!timers.length) return null;
  const now = Date.now() / 1000;
  return (
    <div className="timer-chips" data-testid="timer-chips">
      {timers.map((t) => {
        const left = t.due - now;
        const frac = t.kind === "timer" && t.seconds ? Math.max(0, Math.min(1, left / t.seconds)) : null;
        const label = t.kind === "timer" ? `${t.label ? t.label + " · " : ""}${clock(left)}`
          : `${new Date(t.due * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}${t.label ? " · " + t.label : ""}`;
        return (
          <span key={t.id} className={`timer-chip ${t.kind}`} title={t.kind === "timer" ? "Timer" : "Alarm"}>
            <span aria-hidden="true">{t.kind === "timer" ? "⏱" : "⏰"}</span> {label}
            {frac !== null && <i className="timer-bar" style={{ width: `${frac * 100}%` }} />}
            <button type="button" aria-label={`Cancel ${t.label || t.kind}`} onClick={() => void api.cancelTimer(t.id)}>×</button>
          </span>
        );
      })}
    </div>
  );
}
