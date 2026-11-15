// Local speech models. When the backend has a server-side STT and/or TTS provider configured
// (JARVIS_STT_PROVIDER / JARVIS_TTS_PROVIDER = openai_compat), this installs drop-in replacements for the
// browser's SpeechRecognition / speechSynthesis. The VoiceEngine reads those from `window` at call time,
// so wake word, push-to-talk, continuous mode and barge-in all work unchanged - but the audio goes to
// YOUR models through the JARVIS backend instead of the browser vendor's speech service.

interface VoiceConfig { stt: { provider: string; server_side: boolean }; tts: { provider: string; server_side: boolean } }
type W = Window & Record<string, unknown>;

const SPEECH_RMS = 0.035; // energy above this counts as speech
const SILENCE_MS = 800; // this much quiet after speech ends the utterance
const MIN_SPEECH_MS = 250;
const MAX_UTTERANCE_MS = 20_000;
const IDLE_RECYCLE_MS = 8_000; // throw away the buffer if nobody spoke, so memory stays bounded
const PREROLL_KEEP_MS = 600;

/** SpeechRecognition look-alike: microphone -> energy-based utterance detection -> POST /api/voice/transcribe. */
class ServerRecognition {
  static readonly jarvisLocal = true;
  continuous = true;
  interimResults = false;
  lang = "";
  onresult: ((e: unknown) => void) | null = null;
  onend: (() => void) | null = null;
  onerror: ((e: { error?: string }) => void) | null = null;
  onstart: (() => void) | null = null;
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private timer: number | undefined;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private recStarted = 0;
  private speechStart = 0;
  private lastLoud = 0;
  private stopped = false;

  start() {
    const media = navigator.mediaDevices;
    if (!media?.getUserMedia || typeof MediaRecorder === "undefined") {
      window.setTimeout(() => { this.onerror?.({ error: "audio-capture" }); this.onend?.(); }, 0);
      return;
    }
    media.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }).then(
      (stream) => {
        if (this.stopped) { stream.getTracks().forEach((t) => t.stop()); return; }
        this.stream = stream;
        const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        this.ctx = new Ctx();
        const analyser = this.ctx.createAnalyser();
        analyser.fftSize = 1024;
        this.ctx.createMediaStreamSource(stream).connect(analyser);
        const buf = new Float32Array(analyser.fftSize);
        this.beginSegment();
        this.onstart?.();
        this.timer = window.setInterval(() => {
          analyser.getFloatTimeDomainData(buf);
          let sum = 0;
          for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
          this.tick(Math.sqrt(sum / buf.length), performance.now());
        }, 50);
      },
      () => { this.onerror?.({ error: "not-allowed" }); this.onend?.(); },
    );
  }

  private beginSegment() {
    if (!this.stream || this.stopped) return;
    this.chunks = [];
    this.speechStart = 0;
    this.recStarted = performance.now();
    const rec = new MediaRecorder(this.stream);
    rec.ondataavailable = (e) => { if (e.data.size) this.chunks.push(e.data); };
    this.recorder = rec;
    rec.start();
  }

  private tick(rms: number, now: number) {
    if (rms > SPEECH_RMS) {
      if (!this.speechStart) this.speechStart = now;
      this.lastLoud = now;
    }
    if (this.speechStart) {
      const spoke = this.lastLoud - this.speechStart;
      if (now - this.lastLoud > SILENCE_MS || now - this.speechStart > MAX_UTTERANCE_MS) this.endSegment(spoke >= MIN_SPEECH_MS);
    } else if (now - this.recStarted > IDLE_RECYCLE_MS) this.endSegment(false);
  }

  private endSegment(send: boolean) {
    const rec = this.recorder;
    if (!rec) return;
    this.recorder = null;
    const chunks = this.chunks;
    rec.onstop = () => { if (send && !this.stopped) void this.transcribe(new Blob(chunks, { type: rec.mimeType || "audio/webm" })); };
    try { rec.stop(); } catch { /* already stopped */ }
    // Start the next segment immediately so the first syllable of the next utterance is not clipped.
    window.setTimeout(() => this.beginSegment(), send ? 0 : Math.min(PREROLL_KEEP_MS, 50));
  }

  private async transcribe(blob: Blob) {
    const fd = new FormData();
    fd.append("file", blob, blob.type.includes("ogg") ? "speech.ogg" : blob.type.includes("mp4") ? "speech.mp4" : "speech.webm");
    if (this.lang) fd.append("language", this.lang.split("-")[0]);
    try {
      const res = await fetch("/api/voice/transcribe", { method: "POST", body: fd, credentials: "same-origin" });
      if (!res.ok) throw new Error(String(res.status));
      const text = String(((await res.json()) as { text?: string }).text ?? "").trim();
      if (text && !this.stopped) this.onresult?.({ resultIndex: 0, results: { length: 1, 0: { isFinal: true, length: 1, 0: { transcript: text } } } });
    } catch {
      this.onerror?.({ error: "network" });
    }
  }

  stop() {
    if (this.stopped) return;
    this.stopped = true;
    window.clearInterval(this.timer);
    try { this.recorder?.stop(); } catch { /* ignore */ }
    this.recorder = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close().catch(() => undefined);
    window.setTimeout(() => this.onend?.(), 0);
  }
  abort() { this.stop(); }
}

class ServerUtterance {
  rate = 1; pitch = 1; voice: { name?: string } | null = null;
  onstart: (() => void) | null = null; onend: (() => void) | null = null; onerror: (() => void) | null = null; onboundary: (() => void) | null = null;
  constructor(public text: string) {}
}

/** speechSynthesis look-alike: each utterance is synthesised by POST /api/voice/speak and played in order. */
class ServerSynth {
  readonly jarvisLocal = true;
  speaking = false; pending = false; paused = false;
  private queue: ServerUtterance[] = [];
  private gen = 0;
  private audio: HTMLAudioElement | null = null;
  private busy = false;

  speak(u: ServerUtterance) { this.queue.push(u); this.pending = true; void this.pump(); }
  cancel() {
    this.gen++;
    this.queue = [];
    this.audio?.pause();
    this.audio = null;
    this.speaking = this.pending = this.busy = false;
  }
  getVoices(): SpeechSynthesisVoice[] { return []; }
  addEventListener() {} removeEventListener() {} pause() { this.audio?.pause(); } resume() { void this.audio?.play(); }

  private async pump() {
    if (this.busy) return;
    this.busy = true;
    const gen = this.gen;
    while (this.queue.length && gen === this.gen) {
      const u = this.queue.shift()!;
      this.pending = this.queue.length > 0;
      try {
        const res = await fetch("/api/voice/speak", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: u.text, voice: u.voice?.name || undefined }) });
        if (!res.ok) throw new Error(String(res.status));
        const url = URL.createObjectURL(await res.blob());
        if (gen !== this.gen) { URL.revokeObjectURL(url); break; }
        await new Promise<void>((resolve) => {
          const a = new Audio(url);
          a.playbackRate = Math.min(2, Math.max(0.5, u.rate || 1));
          this.audio = a;
          const done = () => { URL.revokeObjectURL(url); resolve(); };
          a.onplaying = () => { this.speaking = true; u.onstart?.(); };
          a.onended = done; a.onerror = done; a.onpause = () => { if (gen !== this.gen) done(); };
          a.play().catch(done);
        });
        if (gen === this.gen) u.onend?.();
      } catch {
        if (gen === this.gen) u.onerror?.();
      }
    }
    if (gen === this.gen) { this.speaking = false; this.busy = false; }
  }
}

/** Returns which halves are now handled by the user's own models. Safe to call when nothing is configured. */
export async function installServerSpeech(): Promise<{ stt: boolean; tts: boolean }> {
  const res = await fetch("/api/voice/config", { credentials: "same-origin" });
  if (!res.ok) return { stt: false, tts: false };
  const cfg = (await res.json()) as VoiceConfig;
  const w = window as unknown as W;
  if (cfg.stt.server_side) w.SpeechRecognition = ServerRecognition;
  if (cfg.tts.server_side) {
    Object.defineProperty(window, "speechSynthesis", { value: new ServerSynth(), configurable: true });
    w.SpeechSynthesisUtterance = ServerUtterance;
  }
  return { stt: cfg.stt.server_side, tts: cfg.tts.server_side };
}
