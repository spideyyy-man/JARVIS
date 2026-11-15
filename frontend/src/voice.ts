// Voice engine: Web Speech API recognition (wake word / push-to-talk / continuous), streamed sentence-by-sentence
// speech synthesis, barge-in, and a real microphone analyser for the waveform.
// Browser dependencies (SpeechRecognition, speechSynthesis, SpeechSynthesisUtterance, getUserMedia) are read from
// `window` at call time, so tests can install fakes before the page loads.
import type { VoiceSettings } from "./api";

export type VoicePhase = "idle" | "listening" | "thinking" | "speaking";
export interface VoiceTurn { role: "user" | "assistant"; text: string }
export interface VoiceState {
  sttSupported: boolean;
  ttsSupported: boolean;
  wakeMode: boolean; // recognition runs continuously, only utterances with the wake word are sent
  capturing: boolean; // push-to-talk or follow-up capture: next utterance is sent without the wake word
  phase: VoicePhase;
  interim: string;
  heard: string; // last final utterance
  heardStatus: "" | "sent" | "ignored";
  wakeHeardAt: number; // ms timestamp of the last wake-word detection
  reply: string;
  replyStreaming: boolean;
  micLive: boolean; // real microphone waveform available
  error: string;
  conversationId: string | null;
  turns: VoiceTurn[];
}
export interface VoiceDeps {
  sendChat: (text: string, conversationId: string) => Promise<unknown>;
  cancelChat: (conversationId: string) => Promise<unknown>;
  newConversationId: () => string;
  on: (type: string, fn: (e: Record<string, unknown>) => void) => () => void;
  report: (state: "listening" | "speaking" | "idle") => void;
}

interface RecAlternative { transcript: string }
interface RecResult { isFinal: boolean; 0: RecAlternative; length: number }
interface RecEvent { resultIndex: number; results: { length: number; [i: number]: RecResult } }
interface Recognition {
  continuous: boolean; interimResults: boolean; lang: string;
  onresult: ((e: RecEvent) => void) | null; onend: (() => void) | null; onerror: ((e: { error?: string }) => void) | null; onstart: (() => void) | null;
  start(): void; stop(): void; abort(): void;
}
type RecCtor = new () => Recognition;
interface Win {
  SpeechRecognition?: RecCtor; webkitSpeechRecognition?: RecCtor;
  speechSynthesis?: SpeechSynthesis; SpeechSynthesisUtterance?: typeof SpeechSynthesisUtterance;
  AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext;
}
const win = () => window as unknown as Win;
const recCtor = () => win().SpeechRecognition || win().webkitSpeechRecognition;
const synth = () => win().speechSynthesis;

const DEFAULTS: VoiceSettings = { wake_word: "jarvis", continuous: false, voice_name: "", rate: 1, speak_replies: true };
const norm = (t: string) => t.toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
const escapeRe = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Turn Markdown into plain speakable text. */
export function speakable(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, " code block omitted. ")
    .replace(/```[\s\S]*$/g, " ")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "link")
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, "")
    .replace(/^\s*\|?[\s:|-]{3,}\|?\s*$/gm, "")
    .replace(/\|/g, ", ")
    .replace(/[*_`~#]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export class VoiceEngine {
  private s: VoiceState;
  private listeners = new Set<() => void>();
  private cfg: VoiceSettings = { ...DEFAULTS };
  private rec: Recognition | null = null;
  private recRunning = false;
  private restartTimer: number | undefined;
  private capture: "none" | "ptt" | "followup" = "none";
  private armedUntil = 0;
  private activeTurn = false; // a reply is being generated/streamed
  private ttsBuf = "";
  private ttsPending = 0;
  private ttsGen = 0;
  private spoken = ""; // normalised text spoken this turn (echo guard)
  private pulse = 0; // speech-activity envelope while speaking
  private pulseAt = 0;
  private audio: { ctx: AudioContext; analyser: AnalyserNode; stream: MediaStream; buf: Uint8Array<ArrayBuffer> } | null = null;
  private micRequested = false;

  constructor(private deps: VoiceDeps) {
    this.s = {
      sttSupported: !!recCtor(), ttsSupported: !!synth() && !!win().SpeechSynthesisUtterance, wakeMode: false, capturing: false, phase: "idle",
      interim: "", heard: "", heardStatus: "", wakeHeardAt: 0, reply: "", replyStreaming: false, micLive: false, error: "", conversationId: null, turns: [],
    };
    deps.on("chat.delta", (e) => this.onDelta(e));
    deps.on("chat.done", (e) => this.onDone(e));
  }

  // -- state plumbing ----------------------------------------------------------
  getState = () => this.s;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  private set(patch: Partial<VoiceState>) {
    this.s = { ...this.s, ...patch };
    this.listeners.forEach((l) => l());
  }
  configure(v: Partial<VoiceSettings>) {
    this.cfg = { ...this.cfg, ...v };
    if (!this.cfg.wake_word?.trim()) this.cfg.wake_word = DEFAULTS.wake_word;
  }
  get settings(): VoiceSettings {
    return this.cfg;
  }
  /** Re-run feature detection (voices and recognition can appear late in some browsers). */
  detect() {
    const stt = !!recCtor();
    const tts = !!synth() && !!win().SpeechSynthesisUtterance;
    if (stt !== this.s.sttSupported || tts !== this.s.ttsSupported) this.set({ sttSupported: stt, ttsSupported: tts });
  }
  listVoices(): { name: string; lang: string }[] {
    try {
      return (synth()?.getVoices() ?? []).map((v) => ({ name: v.name, lang: v.lang }));
    } catch {
      return [];
    }
  }
  get busy() {
    return this.s.phase === "thinking" || this.s.phase === "speaking";
  }

  // -- public controls -----------------------------------------------------------
  setWakeMode(on: boolean) {
    if (on && !this.s.sttSupported) return;
    this.set({ wakeMode: on, error: "" });
    this.syncRecognition();
  }
  setContinuous(on: boolean) {
    this.cfg.continuous = on;
  }
  /** Mic button: interrupts JARVIS if it is talking/thinking, otherwise toggles push-to-talk capture. */
  toggleMic() {
    if (this.busy) {
      this.interrupt();
      this.startCapture("ptt");
    } else if (this.capture !== "none") this.stopCapture();
    else this.startCapture("ptt");
  }
  /** Barge-in / stop: silence speech output and cancel the running turn on the backend. */
  interrupt() {
    const wasBusy = this.busy;
    this.ttsGen++;
    this.ttsBuf = "";
    this.ttsPending = 0;
    try {
      synth()?.cancel();
    } catch {
      /* ignore */
    }
    if (this.activeTurn && this.s.conversationId) {
      this.deps.cancelChat(this.s.conversationId).catch(() => {});
      if (this.s.reply.trim()) this.set({ turns: [...this.s.turns, { role: "assistant", text: this.s.reply.trim() + " (interrupted)" }] });
    }
    this.activeTurn = false;
    if (wasBusy) {
      this.set({ phase: this.capture !== "none" ? "listening" : "idle", replyStreaming: false });
      this.deps.report(this.capture !== "none" ? "listening" : "idle");
    }
  }
  /** Typed input (also the fallback when speech recognition is unavailable). */
  sendText(text: string) {
    const t = text.trim();
    if (t) this.submit(t);
  }
  newSession() {
    this.interrupt();
    this.set({ conversationId: null, turns: [], reply: "", heard: "", heardStatus: "", interim: "" });
  }
  stopAll() {
    this.interrupt();
    this.capture = "none";
    this.set({ wakeMode: false, capturing: false, phase: "idle", interim: "" });
    this.syncRecognition();
    this.deps.report("idle");
  }

  // -- capture / recognition ------------------------------------------------------
  private startCapture(kind: "ptt" | "followup") {
    if (!this.s.sttSupported) {
      this.set({ error: "Speech recognition is not available in this browser. Type your request instead." });
      return;
    }
    this.capture = kind;
    this.set({ capturing: true, phase: "listening", interim: "", error: "" });
    this.deps.report("listening");
    this.syncRecognition();
  }
  private stopCapture() {
    this.capture = "none";
    this.set({ capturing: false, interim: "", phase: this.busy ? this.s.phase : "idle" });
    if (!this.busy) this.deps.report("idle");
    this.syncRecognition();
  }
  private shouldListen() {
    return this.s.wakeMode || this.capture !== "none";
  }
  private syncRecognition() {
    window.clearTimeout(this.restartTimer);
    if (this.shouldListen()) {
      this.ensureMic();
      if (!this.recRunning) this.startRecognition();
    } else {
      this.releaseMic();
      if (this.rec) {
        try {
          this.rec.stop();
        } catch {
          /* ignore */
        }
      }
    }
  }
  private startRecognition() {
    const Ctor = recCtor();
    if (!Ctor) return;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = navigator.language || "en-US";
    rec.onresult = (e) => this.onResult(e);
    rec.onerror = (e) => {
      const code = e?.error ?? "";
      if (code === "not-allowed" || code === "service-not-allowed" || code === "audio-capture") {
        this.capture = "none";
        this.set({ wakeMode: false, capturing: false, phase: this.busy ? this.s.phase : "idle", error: code === "audio-capture" ? "No microphone was found." : "Microphone access was denied. Allow it in the browser to use voice." });
        this.deps.report("idle");
      } else if (code && code !== "no-speech" && code !== "aborted") this.set({ error: `Speech recognition error: ${code}` });
    };
    rec.onend = () => {
      if (this.rec !== rec) return;
      this.recRunning = false;
      this.rec = null;
      if (this.shouldListen()) this.restartTimer = window.setTimeout(() => this.shouldListen() && !this.recRunning && this.startRecognition(), 300);
    };
    this.rec = rec;
    try {
      rec.start();
      this.recRunning = true;
    } catch (err) {
      this.rec = null;
      this.set({ error: `Could not start speech recognition: ${err instanceof Error ? err.message : String(err)}` });
    }
  }
  private wakeRe() {
    return new RegExp(`(?:\\b(?:hey|ok|okay|hi)[\\s,]+)?\\b${escapeRe(norm(this.cfg.wake_word) || "jarvis")}\\b`, "i");
  }
  private isEcho(text: string) {
    const n = norm(text);
    return this.s.phase === "speaking" && n.length > 0 && this.spoken.includes(n);
  }
  private onResult(e: RecEvent) {
    let interim = "";
    const finals: string[] = [];
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      const t = r[0]?.transcript ?? "";
      if (r.isFinal) finals.push(t);
      else interim += t;
    }
    const any = `${interim} ${finals.join(" ")}`.trim();
    if (!any || this.isEcho(any)) return;
    if (this.busy) {
      // barge-in: the user started speaking over JARVIS
      this.interrupt();
      this.armedUntil = Date.now() + 8000;
    }
    const direct = this.capture !== "none" || Date.now() < this.armedUntil;
    if (interim) {
      const woke = this.wakeRe().test(norm(interim));
      this.set({ interim, ...(woke && !direct ? { wakeHeardAt: Date.now() } : {}), phase: direct || woke ? "listening" : this.s.phase });
    }
    for (const f of finals) this.onFinal(f.trim());
  }
  private onFinal(text: string) {
    if (!text) return;
    const re = this.wakeRe();
    const hasWake = re.test(norm(text));
    const stripped = text.replace(new RegExp(`(?:\\b(?:hey|ok|okay|hi)[\\s,]+)?\\b${escapeRe(this.cfg.wake_word.trim() || "jarvis")}\\b[\\s,.!?:;-]*`, "i"), " ").replace(/\s+/g, " ").trim();
    const direct = this.capture !== "none" || Date.now() < this.armedUntil;
    if (direct) {
      const command = hasWake ? stripped : text;
      if (!command) return; // just the wake word again: keep listening
      this.armedUntil = 0;
      this.capture = "none";
      this.set({ capturing: false, interim: "", heard: text, heardStatus: "sent", ...(hasWake ? { wakeHeardAt: Date.now() } : {}) });
      this.submit(command);
      this.syncRecognition();
    } else if (hasWake) {
      if (stripped) {
        this.set({ interim: "", heard: text, heardStatus: "sent", wakeHeardAt: Date.now() });
        this.submit(stripped);
      } else {
        // bare wake word: the next utterance is the command
        this.armedUntil = Date.now() + 8000;
        this.set({ interim: "", heard: text, heardStatus: "", wakeHeardAt: Date.now(), phase: "listening" });
        this.deps.report("listening");
        window.setTimeout(() => {
          if (Date.now() >= this.armedUntil && this.s.phase === "listening" && this.capture === "none") {
            this.set({ phase: "idle" });
            this.deps.report("idle");
          }
        }, 8100);
      }
    } else {
      this.set({ interim: "", heard: text, heardStatus: "ignored", phase: this.s.phase === "listening" ? "idle" : this.s.phase });
    }
  }

  // -- conversation turn ---------------------------------------------------------
  private submit(text: string) {
    if (this.busy) this.interrupt();
    const cid = this.s.conversationId ?? this.deps.newConversationId();
    this.activeTurn = true;
    this.ttsBuf = "";
    this.spoken = "";
    this.set({ conversationId: cid, phase: "thinking", reply: "", replyStreaming: true, error: "", turns: [...this.s.turns, { role: "user", text }] });
    this.deps.sendChat(text, cid).catch((err: unknown) => {
      this.activeTurn = false;
      this.set({ phase: "idle", replyStreaming: false, error: err instanceof Error ? err.message : String(err) });
      this.deps.report("idle");
    });
  }
  private onDelta(e: Record<string, unknown>) {
    if (!this.activeTurn || e.conversation_id !== this.s.conversationId) return;
    const piece = String(e.text ?? "");
    this.set({ reply: this.s.reply + piece });
    this.ttsBuf += piece;
    this.flushSentences(false);
  }
  private onDone(e: Record<string, unknown>) {
    if (!this.activeTurn || e.conversation_id !== this.s.conversationId) return;
    const msg = e.message as { content?: string } | undefined;
    const full = msg?.content ?? this.s.reply;
    // Anything that was never streamed as deltas (e.g. policy replies) still has to be spoken.
    if (full.startsWith(this.s.reply)) this.ttsBuf += full.slice(this.s.reply.length);
    this.activeTurn = false;
    this.set({ reply: full, replyStreaming: false, turns: [...this.s.turns, { role: "assistant", text: full }] });
    this.flushSentences(true);
    if (this.ttsPending === 0) this.finishTurn();
    else this.deps.report("speaking"); // the backend went idle on chat.done, but we are still talking
  }
  private finishTurn() {
    this.set({ phase: "idle" });
    this.deps.report("idle");
    if (this.cfg.continuous && this.s.sttSupported) this.startCapture("followup");
  }

  // -- speech synthesis ------------------------------------------------------------
  private flushSentences(final: boolean) {
    let cut = -1;
    if (final) cut = this.ttsBuf.length;
    else {
      const re = /[.!?:;](?=\s)|\n+/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(this.ttsBuf))) cut = m.index + m[0].length;
      if (this.ttsBuf.split("```").length % 2 === 0) cut = -1; // inside an unfinished code fence
    }
    if (cut <= 0) return;
    const chunk = this.ttsBuf.slice(0, cut);
    this.ttsBuf = this.ttsBuf.slice(cut);
    const text = speakable(chunk);
    if (text) this.speak(text);
  }
  private speak(text: string) {
    const engine = synth();
    const Utter = win().SpeechSynthesisUtterance;
    if (!this.cfg.speak_replies || !engine || !Utter) return;
    const gen = this.ttsGen;
    const u = new Utter(text);
    u.rate = Math.min(2, Math.max(0.5, Number(this.cfg.rate) || 1));
    if (this.cfg.pitch) u.pitch = this.cfg.pitch;
    if (this.cfg.voice_name) {
      try {
        const v = engine.getVoices().find((x) => x.name === this.cfg.voice_name);
        if (v) u.voice = v;
      } catch {
        /* ignore */
      }
    }
    u.onstart = () => {
      if (gen !== this.ttsGen) return;
      this.spoken += " " + norm(text);
      this.bumpPulse();
      if (this.s.phase !== "speaking") {
        this.set({ phase: "speaking" });
        this.deps.report("speaking");
      }
    };
    u.onboundary = () => gen === this.ttsGen && this.bumpPulse();
    const finished = () => {
      if (gen !== this.ttsGen) return;
      this.ttsPending = Math.max(0, this.ttsPending - 1);
      if (this.ttsPending === 0 && !this.activeTurn) this.finishTurn();
    };
    u.onend = finished;
    u.onerror = finished;
    this.ttsPending++;
    try {
      engine.speak(u);
    } catch {
      finished();
    }
  }
  private bumpPulse() {
    this.pulse = 1;
    this.pulseAt = performance.now();
  }

  // -- microphone analyser (real waveform) -------------------------------------------
  private ensureMic() {
    if (this.audio || this.micRequested) return;
    const media = navigator.mediaDevices;
    const Ctx = win().AudioContext || win().webkitAudioContext;
    if (!media?.getUserMedia || !Ctx) return;
    this.micRequested = true;
    media.getUserMedia({ audio: true }).then(
      (stream) => {
        this.micRequested = false;
        if (!this.shouldListen()) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        try {
          const ctx = new Ctx();
          const analyser = ctx.createAnalyser();
          analyser.fftSize = 512;
          analyser.smoothingTimeConstant = 0.6;
          ctx.createMediaStreamSource(stream).connect(analyser);
          this.audio = { ctx, analyser, stream, buf: new Uint8Array(new ArrayBuffer(analyser.fftSize)) };
          this.set({ micLive: true });
        } catch {
          stream.getTracks().forEach((t) => t.stop());
        }
      },
      () => {
        this.micRequested = false; // denied: the waveform falls back to a synthetic animation
      },
    );
  }
  private releaseMic() {
    if (!this.audio) return;
    this.audio.stream.getTracks().forEach((t) => t.stop());
    void this.audio.ctx.close().catch(() => {});
    this.audio = null;
    this.set({ micLive: false });
  }
  /** Time-domain samples (-1..1) from the real microphone, or null when unavailable. */
  getWave(): Float32Array | null {
    if (!this.audio || this.s.phase === "speaking") return null;
    const { analyser, buf } = this.audio;
    analyser.getByteTimeDomainData(buf);
    const out = new Float32Array(buf.length);
    for (let i = 0; i < buf.length; i++) out[i] = (buf[i] - 128) / 128;
    return out;
  }
  /** Overall activity level 0..1: mic loudness when listening, speech-activity envelope when speaking. */
  getLevel(): number {
    if (this.s.phase === "speaking") {
      const t = performance.now();
      const env = this.pulse * Math.exp(-(t - this.pulseAt) / 260);
      const babble = 0.45 + 0.3 * Math.sin(t / 90) * Math.sin(t / 37) + 0.15 * Math.sin(t / 211);
      return Math.min(1, Math.max(0.12, babble * 0.7 + env * 0.5));
    }
    const wave = this.getWave();
    if (!wave) return 0;
    let sum = 0;
    for (let i = 0; i < wave.length; i++) sum += wave[i] * wave[i];
    return Math.min(1, Math.sqrt(sum / wave.length) * 4);
  }
}
