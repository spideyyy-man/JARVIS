import { useState } from "react";
import { AlertTriangle, Ear, Mic, MicOff, Plus, Repeat, SendHorizontal, Square, Volume2, VolumeX } from "lucide-react";
import { api } from "../api";
import type { AssistantState } from "../api";
import { errText, setState, useStore, useVoice, voice } from "../store";
import { go, useNow } from "../util";
import { Orb } from "../components/Orb";
import { Waveform } from "../components/Waveform";
import { Markdown } from "../components/Markdown";
import { Panel, ScreenHead, Switch } from "../components/Panel";

const CAPTION: Record<AssistantState, string> = {
  idle: "Standing by", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…", executing: "Executing…", error: "Something went wrong",
};
const getWave = () => voice.getWave();
const getLevel = () => voice.getLevel();

export function Voice() {
  const v = useVoice();
  const server = useStore((s) => s.assistantState);
  const settings = useStore((s) => s.settings);
  const [typed, setTyped] = useState("");
  useNow(1000);
  const [saveError, setSaveError] = useState("");
  const vs = settings?.voice;
  const wake = vs?.wake_word || "jarvis";
  const continuous = !!vs?.continuous;
  const speakReplies = vs?.speak_replies !== false;

  // Local voice activity drives the orb; while the backend is working on our turn, show its finer-grained state.
  const state: AssistantState = v.phase === "thinking" ? (server === "executing" ? "executing" : "thinking") : v.phase !== "idle" ? v.phase : server === "error" ? "error" : "idle";
  const busy = v.phase === "thinking" || v.phase === "speaking";
  const wakeFresh = Date.now() - v.wakeHeardAt < 4000;

  const saveVoice = async (patch: { continuous?: boolean; speak_replies?: boolean }) => {
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

  return (
    <>
      <ScreenHead title="Voice Assistant" sub={`Say “${wake}” followed by your request, or hold a conversation hands-free.`}>
        <button type="button" className="btn" onClick={() => voice.newSession()}><Plus size={14} /> New session</button>
      </ScreenHead>

      {!v.sttSupported && (
        <div className="banner" role="alert" data-testid="stt-unsupported" style={{ marginBottom: 12 }}>
          <AlertTriangle size={16} />
          <div>
            <b>Speech recognition is not available in this browser.</b> The Web Speech API is supported in Chrome, Edge and Safari. You can still type below{v.ttsSupported ? " and JARVIS will answer out loud." : "."}
          </div>
        </div>
      )}
      {!v.ttsSupported && (
        <div className="banner" role="alert" data-testid="tts-unsupported" style={{ marginBottom: 12 }}>
          <AlertTriangle size={16} />
          <div><b>Speech synthesis is not available in this browser.</b> Replies are shown as text only.</div>
        </div>
      )}
      {v.error && <div className="banner red" role="alert" style={{ marginBottom: 12 }}><AlertTriangle size={16} /><div>{v.error}</div></div>}

      <div className="voice-layout">
        <section className="voice-stage panel" data-testid="voice-stage" data-phase={v.phase}>
          <div className="voice-chips">
            <span className={`badge ${v.wakeMode ? (wakeFresh ? "green" : "cyan") : ""}`} data-testid="wake-indicator" data-wake={v.wakeMode ? (wakeFresh ? "heard" : "armed") : "off"}>
              <Ear size={12} /> wake word “{wake}” · {v.wakeMode ? (wakeFresh ? "heard" : "armed") : "off"}
            </span>
            <span className={`badge ${v.micLive ? "green" : ""}`} title={v.micLive ? "Waveform shows the real microphone signal" : "Waveform is synthetic until microphone access is granted"}>
              {v.micLive ? <Mic size={12} /> : <MicOff size={12} />} {v.micLive ? "mic live" : "mic waveform inactive"}
            </span>
            {continuous && <span className="badge cyan"><Repeat size={12} /> continuous</span>}
          </div>

          <div className="voice-orb"><Orb state={state} size="lg" level={getLevel} /></div>
          <div className="voice-caption" data-testid="voice-caption">{CAPTION[state]}</div>
          <Waveform state={state} getWave={getWave} getLevel={getLevel} height={84} />

          <div className="voice-transcript" data-testid="voice-transcript" aria-live="polite">
            <span className="label">You said</span>
            <p>
              {v.interim ? <span className="interim" data-testid="voice-interim">{v.interim}</span> : v.heard ? <span data-testid="voice-final">{v.heard}</span> : <span className="dim">{v.sttSupported ? "Your words appear here as you speak." : "Type a request below."}</span>}
              {!v.interim && v.heard && v.heardStatus === "ignored" && <span className="badge" style={{ marginLeft: 8 }}>ignored · no wake word</span>}
            </p>
          </div>

          <div className="voice-controls">
            <button type="button" className={`mic-btn ${v.capturing ? "on" : ""}`} onClick={() => voice.toggleMic()} disabled={!v.sttSupported} data-testid="mic-btn" aria-pressed={v.capturing} aria-label={busy ? "Interrupt and talk" : v.capturing ? "Stop listening" : "Push to talk"}>
              {v.capturing ? <Square size={24} /> : <Mic size={26} />}
            </button>
            <button type="button" className="btn danger" onClick={() => voice.interrupt()} disabled={!busy} data-testid="voice-stop"><Square size={13} /> Stop</button>
          </div>
          <div className="dim voice-hint">{busy ? "Start speaking or press the mic to interrupt." : v.capturing ? "Listening for one request — no wake word needed." : "Press the mic to talk without the wake word."}</div>

          <div className="voice-toggles">
            <label className="toggle-row">
              <Switch checked={v.wakeMode} onChange={(on) => voice.setWakeMode(on)} label="Wake word listening" disabled={!v.sttSupported} />
              <span><b>Wake word</b><span className="dim"> · always listen for “{wake}”</span></span>
            </label>
            <label className="toggle-row">
              <Switch checked={continuous} onChange={(on) => void saveVoice({ continuous: on })} label="Continuous conversation" disabled={!v.sttSupported} />
              <span><b>Continuous conversation</b><span className="dim"> · listen again after each reply</span></span>
            </label>
            <label className="toggle-row">
              <Switch checked={speakReplies} onChange={(on) => void saveVoice({ speak_replies: on })} label="Speak replies" disabled={!v.ttsSupported} />
              <span>{speakReplies ? <Volume2 size={13} /> : <VolumeX size={13} />} <b>Speak replies</b></span>
            </label>
          </div>
          {saveError && <div className="error-text" role="alert">{saveError}</div>}
        </section>

        <div className="col" style={{ gap: 14 }}>
          <Panel title="Reply" testid="voice-reply-panel" actions={v.conversationId ? <button type="button" className="btn ghost sm" onClick={() => go(`conversations/${v.conversationId}`)}>Open thread</button> : undefined}>
            <div className="voice-reply" data-testid="voice-reply" data-streaming={v.replyStreaming}>
              {v.reply ? <Markdown text={v.reply} /> : v.replyStreaming ? <span className="typing" aria-label="Thinking"><i /><i /><i /></span> : <span className="dim">The answer streams here while JARVIS speaks it.</span>}
            </div>
          </Panel>
          <Panel title={`Session transcript · ${v.turns.length}`} testid="voice-turns">
            <div className="voice-turns">
              {v.turns.length === 0 && <div className="dim">No turns yet in this voice session.</div>}
              {v.turns.map((t, i) => (
                <div key={i} className={`voice-turn ${t.role}`}>
                  <span className="label">{t.role === "user" ? "You" : "JARVIS"}</span>
                  <span className="truncate-3">{t.text}</span>
                </div>
              ))}
            </div>
            <form className="composer-row" onSubmit={(e) => { e.preventDefault(); voice.sendText(typed); setTyped(""); }}>
              <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Or type a request…" aria-label="Type a request" data-testid="voice-typed" />
              <button type="submit" className="btn primary" disabled={!typed.trim()}><SendHorizontal size={14} /> Send</button>
            </form>
          </Panel>
        </div>
      </div>
    </>
  );
}
