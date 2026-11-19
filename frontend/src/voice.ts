// Voice engine: Web Speech API recognition (wake word / push-to-talk / continuous), streamed sentence-by-sentence
// speech synthesis, barge-in, and a real microphone analyser for the waveform.
// Browser dependencies (SpeechRecognition, speechSynthesis, SpeechSynthesisUtterance, getUserMedia) are read from
// `window` at call time, so tests can install fakes before the page loads.
import type { VoiceSettings } from "./api";
import { norm, wakeRegex, wakeWords } from "./wake";
import { AUTO, EN, TA, languageSwitch, nextListenLang, replyVoiceLang, switchedMessage, typedLang } from "./lang";

const AUTO_KEY = "jarvis.autoLang"; // last language heard in auto mode (per browser profile)
function loadAutoLang(): string {
  try {
    const v = localStorage.getItem(AUTO_KEY);
    return v === TA || v === EN ? v : EN;
  } catch {
    return EN;
  }
}

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
  listenLang: string; // the language speech is being recognised in right now (in auto mode it follows the user)
  local: boolean; // the offline listener on the JARVIS server hears the user (wake word + recognition); the page only speaks
  localState: string; // its state: waiting | recording | transcribing | loading | error
}
export interface VoiceDeps {
  sendChat: (text: string, conversationId: string) => Promise<unknown>;
  cancelChat: (conversationId: string) => Promise<unknown>;
  newConversationId: () => string;
  on: (type: string, fn: (e: Record<string, unknown>) => void) => () => void;
  report: (state: "listening" | "speaking" | "idle") => void;
  listenLocal?: () => Promise<boolean>; // ask the offline listener to take the next utterance (no wake word)
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

const DEFAULTS: VoiceSettings = { wake_word: "jarvis", continuous: false, voice_name: "", rate: 1, speak_replies: true, language: "" };

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
  private cancelling: Promise<unknown> | null = null; // cancel request for an interrupted turn, still in flight
  private ttsBuf = "";
  private ttsPending = 0;
  private ttsGen = 0;
  private spoken = ""; // normalised text spoken this turn (echo guard)
  private pulse = 0; // speech-activity envelope while speaking
  private pulseAt = 0;
  private audio: { ctx: AudioContext; analyser: AnalyserNode; stream: MediaStream; buf: Uint8Array<ArrayBuffer> } | null = null;
  private micRequested = false;
  private autoLang = loadAutoLang();

  constructor(private deps: VoiceDeps) {
    this.s = {
      sttSupported: !!recCtor(), ttsSupported: !!synth() && !!win().SpeechSynthesisUtterance, wakeMode: false, capturing: false, phase: "idle",
      interim: "", heard: "", heardStatus: "", wakeHeardAt: 0, reply: "", replyStreaming: false, micLive: false, error: "", conversationId: null, turns: [],
      listenLang: "", local: false, localState: "",
    };
    this.s.listenLang = this.lang;
    deps.on("chat.delta", (e) => this.onDelta(e));
    deps.on("chat.done", (e) => this.onDone(e));
    deps.on("voice.wake", () => this.onLocalWake());
    deps.on("voice.state", (e) => this.onLocalState(e));
    deps.on("voice.heard", (e) => this.onLocalHeard(e));
  }

  // -- offline listener (server side) --------------------------------------------------
  /** The server's offline listener is running: it does the hearing, so the browser recogniser stays off. */
  setLocal(on: boolean, state = "") {
    if (on === this.s.local && state === this.s.localState) return;
    this.set({ local: on, localState: state || this.s.localState, ...(on ? { sttSupported: true, error: "" } : { sttSupported: !!recCtor() }) });
    this.syncRecognition();
  }
  private onLocalWake() {
    if (!this.s.local) return;
    if (this.busy) this.interrupt(); // "Hey Jarvis" over a reply: stop talking and listen
    this.set({ phase: "listening", wakeHeardAt: Date.now(), interim: "Listening…", capturing: true });
    this.deps.report("listening");
  }
  private onLocalState(e: Record<string, unknown>) {
    const st = String(e.state ?? "");
    if (!st) return;
    if (st === "off") return this.setLocal(false, st);
    if (!this.s.local && st !== "error") this.setLocal(true, st);
    const patch: Partial<VoiceState> = { localState: st };
    if (st === "transcribing") patch.interim = "…";
    if (st === "waiting" && this.s.phase === "listening") {
      Object.assign(patch, { phase: "idle", interim: "", capturing: false, ...(e.note ? { heard: String(e.note), heardStatus: "ignored" } : {}) });
      this.deps.report("idle");
    }
    if (st === "error") patch.error = `Offline listener: ${String(e.error ?? "error")}`;
    this.set(patch);
  }
  /** The offline listener heard a request and the server is answering it: show it and speak the reply. */
  private onLocalHeard(e: Record<string, unknown>) {
    const text = String(e.text ?? "").trim();
    const cid = String(e.conversation_id ?? "");
    if (!text || !cid) return;
    if (this.busy) this.interrupt();
    this.capture = "none";
    this.activeTurn = true;
    this.ttsBuf = "";
    this.spoken = "";
    this.set({ local: true, conversationId: cid, phase: "thinking", reply: "", replyStreaming: true, error: "", interim: "", capturing: false,
      heard: text, heardStatus: "sent", turns: [...this.s.turns, { role: "user", text }] });
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
    const before = this.lang;
    this.cfg = { ...this.cfg, ...v };
    if (!this.cfg.wake_word?.trim()) this.cfg.wake_word = DEFAULTS.wake_word;
    if (this.lang !== before) this.restartRecognition();
    if (this.s.listenLang !== this.lang) this.set({ listenLang: this.lang });
  }
  /** Speech-recognition language: in auto mode the one the user last used, else the user's choice, else the browser's. */
  get lang(): string {
    if (this.cfg.language === AUTO) return this.autoLang;
    return this.cfg.language || navigator.language || "en-US";
  }
  private restartRecognition() {
    // recognition keeps the language it started with; stopping it makes onend restart it in the new one
    if (!this.rec) return;
    try {
      this.rec.stop();
    } catch {
      /* ignore */
    }
  }
  /** Auto mode: listen in `lang` from the next utterance on (remembered across restarts). */
  private setListenLang(lang: string) {
    if (this.cfg.language !== AUTO || lang === this.autoLang) return;
    this.autoLang = lang;
    try {
      localStorage.setItem(AUTO_KEY, lang);
    } catch {
      /* not remembered after a restart, still used now */
    }
    this.set({ listenLang: lang });
    this.restartRecognition();
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
    if (this.s.local) return this.startLocal();
    if (this.busy) {
      this.interrupt();
      this.startCapture("ptt");
    } else if (this.capture !== "none") this.stopCapture();
    else this.startCapture("ptt");
  }
  /** Hotkey / tray: listen for one request now, without the wake word (interrupting a reply in progress). */
  listenNow() {
    if (this.s.local) return this.startLocal();
    if (this.capture === "none" || this.busy) this.toggleMic();
  }
  private startLocal() {
    if (this.busy) this.interrupt();
    this.set({ phase: "listening", interim: "Listening…", capturing: true, error: "" });
    this.deps.report("listening");
    void this.deps.listenLocal?.().then((ok) => {
      if (!ok) this.set({ phase: "idle", interim: "", capturing: false, error: "The offline listener is not running." });
    });
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
      this.cancelling = this.deps.cancelChat(this.s.conversationId).catch(() => {});
      if (this.s.reply.trim()) this.set({ turns: [...this.s.turns, { role: "assistant", text: this.s.reply.trim() + " (interrupted)" }] });
    }
    this.activeTurn = false;
    if (wasBusy) {
      this.set({ phase: this.capture !== "none" ? "listening" : "idle", replyStreaming: false });
      this.deps.report(this.capture !== "none" ? "listening" : "idle");
    }
  }
  /** Typed input (also the fallback when speech recognition is unavailable). */
  /** JARVIS speaks first (a due reminder, the daily briefing). Never talks over an ongoing exchange. */
  announce(text: string) {
    if (this.busy) return;
    const say = speakable(text);
    if (say) this.speak(say);
  }
  sendText(text: string) {
    const t = text.trim();
    if (!t) return;
    if (this.cfg.language === AUTO) {
      const sw = languageSwitch(t, true);
      if (sw) return this.switchLanguage(sw, t);
      this.setListenLang(typedLang(this.autoLang, t));
    }
    this.submit(t);
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
    return !this.s.local && (this.s.wakeMode || this.capture !== "none");
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
    rec.lang = this.lang;
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
  /** Wake-word spellings to accept: in auto mode both English and Tamil script, whichever language is listening. */
  private wakeList() {
    return wakeWords(this.cfg.wake_word, this.cfg.language === AUTO ? TA : this.lang);
  }
  private wakeRe() {
    return wakeRegex(this.wakeList());
  }
  /** "Jarvis, Tamil" and the like: change the listening language, confirm in that language, and listen for the
   * command without needing the wake word again. */
  private switchLanguage(lang: string, heard: string) {
    this.setListenLang(lang);
    this.capture = "none";
    this.armedUntil = Date.now() + 12000;
    this.set({ capturing: false, interim: "", heard, heardStatus: "", wakeHeardAt: Date.now(), phase: "listening", turns: [...this.s.turns, { role: "user", text: heard }, { role: "assistant", text: switchedMessage(lang) }] });
    this.deps.report("listening");
    this.speak(switchedMessage(lang));
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
    const stripped = text.replace(wakeRegex(this.wakeList(), true), " ").replace(/\s+/g, " ").trim();
    const direct = this.capture !== "none" || Date.now() < this.armedUntil;
    if (this.cfg.language === AUTO && (direct || hasWake)) {
      const sw = languageSwitch(text, direct);
      if (sw) return this.switchLanguage(sw, text);
      if (stripped || direct) this.setListenLang(nextListenLang(this.autoLang, hasWake ? stripped : text)); // follow the language actually spoken
    }
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
    // Send only once a cancel of the interrupted turn has been handled: arriving later, it would cancel this new turn.
    const prior = this.cancelling;
    this.cancelling = null;
    (prior ?? Promise.resolve()).then(() => this.deps.sendChat(text, cid)).catch((err: unknown) => {
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
    const msg = e.message as { content?: string; meta?: { interrupted?: boolean } } | undefined;
    if (msg?.meta?.interrupted) return; // the end of the turn we interrupted, not the reply to the current one
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
    try {
      const voices = engine.getVoices();
      // the chosen voice, else one for the language the reply is written in - Tamil text gets a Tamil voice and
      // English text an English one, whatever is being listened to
      const lang = replyVoiceLang(text, this.cfg.language ?? "");
      const chosen = this.cfg.voice_name ? voices.find((x) => x.name === this.cfg.voice_name) : undefined;
      const fits = chosen && (!lang || chosen.lang.split("-")[0] === lang.split("-")[0]);
      const v = (fits && chosen) || (lang && (voices.find((x) => x.lang === lang) || voices.find((x) => x.lang.split("-")[0] === lang.split("-")[0]))) || chosen;
      if (v) u.voice = v;
      if (lang && !(fits && chosen)) u.lang = lang;
    } catch {
      /* ignore */
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
