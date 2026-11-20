"""Offline voice input: wake word + speech recognition on this computer, no browser speech service.

Why: the browser's recogniser (Web Speech API) sends audio to an online service; on the owner's laptop it failed with
"Speech recognition error: network". Here everything runs locally:

  microphone (sounddevice, 16 kHz) -> "Hey Jarvis" wake word (openWakeWord, ~1 MB ONNX model, a fraction of one core)
  -> record until the user stops talking (energy endpointing) -> Whisper `small` (faster-whisper, CTranslate2 int8)
  -> text + language, chosen between English and Tamil automatically.

Measured on the owner's laptop (2026-11-19, CPU at 100% from other apps): wake word 1.00 on "Hey Jarvis", 0.00 on other
speech; Whisper small picked the right language on 6/6 Tamil and 3/3 English clips, Tamil character error ~10% (base:
3/6 languages, ~33%). Whisper small uses ~0.5 GB while loaded.

The listener runs in its own thread. It reports through two callbacks (called from that thread): `on_event(type, data)`
and `on_utterance(text, language, info)`. `VoiceService` connects them to JARVIS (event bus + chat) on the server's loop.
"""
from __future__ import annotations

import asyncio
import collections
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable

RATE = 16000
CHIME_FRAMES = 3  # 0.24 s after the wake word: the chime (speak.Speaker.chime, 0.18 s) is not the user's speech
STALE_AFTER = 20.0  # s: a request heard this slowly is not acted on (on the laptop one took 912 s while the CPU was full)
FAST_TEXT_LANGUAGES = {"en"}
# The words people say to JARVIS, given to the English model as context. Measured on today's real commands spoken by
# Windows' Indian-English voices through a simulated laptop mic: 12.3% word errors without it, 2.3% with it; silence
# still comes out empty (no invented words).
VOCABULARY = ("JARVIS, what time is it? Open Notepad, Claude, Chrome, Google, YouTube. Create a file, a folder, on my Desktop, "
              "Documents, Downloads. index.html, notes.txt, hi.txt. Search the web. Volume, brightness. Remind me. Delete, rename, move.")
VAD_START, VAD_STAY = 0.5, 0.35  # speech probability to start talking / to keep counting as talking
SPEECH_END_VAD = 0.5  # s of non-speech that ends an utterance with the speech detector (0.7 s with loudness only)
KEEP_SAMPLES = 30  # the last utterances are kept (only on this computer) to tune recognition on the user's real voice
FRAME = 1280  # 80 ms: what openWakeWord expects per call
FRAME_S = FRAME / RATE


# -- pieces that are pure logic (unit-tested without a microphone or models) ------------------------------------------
class Endpointer:
    """Decides when an utterance has started and ended from per-frame loudness (RMS of int16 samples).

    The noise floor is learned while waiting for the wake word, so a fan or a noisy room raises the bar."""

    def __init__(self, noise: float = 120.0, start_timeout: float = 6.0, end_silence: float = 0.7, max_length: float = 15.0, min_speech: float = 0.25):
        self.noise, self.start_timeout, self.end_silence, self.max_length, self.min_speech = noise, start_timeout, end_silence, max_length, min_speech
        self.reset()

    def reset(self) -> None:
        self.elapsed = self.speech = self.silence = 0.0
        self.started = False

    def learn(self, rms: float) -> None:
        """Background level while idle: follows quiet frames quickly and loud ones (speech, music) slowly."""
        rate = 0.05 if rms < self.noise else 0.01
        self.noise = max(30.0, self.noise + (rms - self.noise) * rate)

    @property
    def threshold(self) -> float:
        return max(self.noise * 2.5, 250.0)

    def feed(self, rms: float) -> str:
        """Loudness-based (the fallback when the speech detector is unavailable): see feed_speech."""
        return self.feed_speech(rms >= self.threshold)

    def feed_speech(self, is_speech: bool) -> str:
        """'wait' (nothing said yet), 'speech', 'done' (finished talking), 'timeout' (never started)."""
        self.elapsed += FRAME_S
        if is_speech:
            self.speech += FRAME_S
            self.silence = 0.0
            if self.speech >= 0.16:
                self.started = True
        else:
            self.silence += FRAME_S
        if not self.started:
            return "timeout" if self.elapsed >= self.start_timeout else "wait"
        if self.elapsed >= self.max_length or (self.silence >= self.end_silence and self.speech >= self.min_speech):
            return "done"
        return "speech"


WAKE_PREFIX = re.compile(r"^\s*(?:(?:hey|hi|ok|okay|ஹே)[\s,.!]*)?(?:jarvis|ஜார்விஸ்|ஜார்வீஸ்)[\s,.!?:]*", re.I)
# What Whisper tends to "hear" in silence or noise.
HALLUCINATIONS = {"", "you", "thank you", "thanks", "thank you for watching", "thanks for watching", "bye", "okay", "ok", "so", "uh", "um",
                  "subtitles by the amara.org community", "please subscribe", "நன்றி"}


def clean_transcript(text: str) -> str:
    """Drop a leading "Hey Jarvis" (the wake phrase is often still in the recording) and tidy spacing."""
    text = re.sub(r"\s+", " ", text).strip()
    return WAKE_PREFIX.sub("", text, count=1).strip()


def is_noise(text: str, no_speech: float, logprob: float) -> bool:
    plain = re.sub(r"[^\w\s]", "", text.lower()).strip()
    if plain in HALLUCINATIONS:
        return True
    return no_speech > 0.6 and logprob < -0.8


def pick_language(detected: str, probs: dict[str, float], allowed: tuple[str, ...]) -> str:
    """Whisper's language, limited to the ones the user speaks (it calls Tamil Telugu or Hindi now and then)."""
    if not allowed or detected in allowed:
        return detected
    return max(allowed, key=lambda lang: probs.get(lang, 0.0))


# -- the listener -------------------------------------------------------------------------------------------------------
class LocalListener:
    def __init__(self, models_dir: Path, on_event: Callable[[str, dict], None], on_utterance: Callable[[str, str, dict], None], *,
                 model_size: str = "small", fast_size: str = "base", english_size: str = "base.en", languages: tuple[str, ...] = ("en", "ta"), wake_threshold: float = 0.5,
                 threads: int = 0, accurate_idle: float = 15 * 60):
        self.models_dir, self.on_event, self.on_utterance = Path(models_dir), on_event, on_utterance
        # all cores: measured on the owner's laptop under load, 8 threads took 4-8 s per request where 4 took ~15 s
        self.model_size, self.languages, self.wake_threshold, self.threads = model_size, languages, wake_threshold, threads or min(8, os.cpu_count() or 4)
        # Two models (measured on the owner's laptop, CPU half busy): base hears the language right and writes English
        # well in ~2.5 s; small takes ~8 s but is needed for Tamil text. So base first, small only for Tamil.
        self.fast_size, self.accurate_idle, self.english_size = fast_size, accurate_idle, english_size
        self._english = None
        self.keep_samples = True
        self._accurate = None
        self._accurate_lock = threading.Lock()
        self._accurate_timer: threading.Timer | None = None
        self.state = "off"  # off | loading | waiting | recording | transcribing | error
        self.error = ""
        self.last: dict = {}
        self._stop = threading.Event()
        self._listen_now = threading.Event()
        self._thread: threading.Thread | None = None
        self._whisper = None
        self._whisper_ready = threading.Event()
        self._frames: queue.Queue = queue.Queue(maxsize=200)

    @staticmethod
    def missing() -> str:
        """Empty when the packages are installed, else what to install. Only looks them up - importing them loads
        native libraries (seconds, minutes while antivirus scans them), and the floating icon asks twice a second.
        A package that is present but broken shows up as the listener's error when it starts."""
        import importlib.util

        mods = (("sounddevice", "sounddevice"), ("openwakeword", "openwakeword"), ("faster_whisper", "faster-whisper"))
        return " ".join(pkg for mod, pkg in mods if importlib.util.find_spec(mod) is None)

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-listener")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._set("off")

    def listen_now(self) -> None:
        """Hotkey / Talk button: take the next utterance without the wake word."""
        self._listen_now.set()

    def status(self) -> dict:
        model = f"whisper-{self.english_size} (English)" if self.english_size else f"whisper-{self.fast_size}"
        if self.model_size != self.fast_size:
            model += f" + {self.model_size} (Tamil)"
        return {"state": self.state, "error": self.error, "model": model, "speech_model_ready": self._whisper_ready.is_set(),
                "wake_phrase": "Hey Jarvis", "last": self.last}

    # -- internals ---------------------------------------------------------------------------------------------------
    def _set(self, state: str, **data) -> None:
        self.state = state
        try:
            self.on_event("voice.state", {"state": state, **data})
        except Exception:  # noqa: BLE001 - a reporting problem must not stop listening
            pass

    def _load_whisper(self) -> None:
        try:
            from faster_whisper import WhisperModel

            self._whisper = self._model(self.fast_size)
            # The first real decode is slow (it sets everything up); do it now. transcribe() is lazy: consume it.
            list(self._whisper.transcribe(__import__("numpy").zeros(16000, "float32"), language="en")[0])
            if self.english_size:
                self._english = self._model(self.english_size)
                list(self._english.transcribe(__import__("numpy").zeros(16000, "float32"), language="en")[0])
        except Exception as e:  # noqa: BLE001
            self.error = f"speech model failed to load: {e}"
        self._whisper_ready.set()

    def _model(self, size: str):
        from faster_whisper import WhisperModel

        return WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=self.threads, download_root=str(self.models_dir / "whisper"))

    def _english_model(self):
        if self._english is None:
            self._english = self._model(self.english_size)
        return self._english

    def _accurate_model(self):
        """The bigger model for Tamil text: loaded on first use (~8 s), given back after quiet minutes (~0.5 GB)."""
        if self.model_size == self.fast_size:
            return self._whisper
        with self._accurate_lock:
            if self._accurate is None:
                self._accurate = self._model(self.model_size)
            if self._accurate_timer:
                self._accurate_timer.cancel()
            self._accurate_timer = threading.Timer(self.accurate_idle, self._drop_accurate)
            self._accurate_timer.daemon = True
            self._accurate_timer.start()
            return self._accurate

    def _drop_accurate(self) -> None:
        with self._accurate_lock:
            self._accurate = None

    def _run(self) -> None:
        try:
            import numpy as np
            import openwakeword
            import sounddevice as sd
            from openwakeword.model import Model

            self._set("loading")
            threading.Thread(target=self._load_whisper, daemon=True, name="jarvis-whisper-load").start()
            openwakeword.utils.download_models(["hey_jarvis"])  # ~6 MB once; no-op when present
            wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            try:  # Silero speech detector (shipped with openWakeWord): tells speech from fans, typing and music
                from openwakeword.vad import VAD

                vad = VAD()
            except Exception:  # noqa: BLE001 - fall back to loudness
                vad = None
        except Exception as e:  # noqa: BLE001
            self.error = f"could not start: {e}"
            self._set("error", error=self.error)
            return

        def callback(indata, _frames, _time, _status):
            try:
                self._frames.put_nowait(indata[:, 0].copy())
            except queue.Full:  # the processing thread is behind (CPU busy): drop audio rather than lag further
                pass

        endpoint = Endpointer(end_silence=SPEECH_END_VAD if vad else 0.7)
        speaking = False
        try:
            stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, callback=callback)
            stream.start()
        except Exception as e:  # noqa: BLE001 - no microphone, or it is blocked in Windows privacy settings
            self.error = f"microphone unavailable: {e}"
            self._set("error", error=self.error)
            return
        self.error = ""
        self._set("waiting")
        recording: list = []
        skip = 0
        cooldown_until = 0.0
        try:
            while not self._stop.is_set():
                try:
                    frame = self._frames.get(timeout=0.5)
                except queue.Empty:
                    continue
                rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                if self.state == "waiting":
                    endpoint.learn(rms)
                    score = max(wake.predict(frame).values())
                    now = time.time()
                    if self._listen_now.is_set() or (score >= self.wake_threshold and now >= cooldown_until):
                        by_hotkey = self._listen_now.is_set()
                        self._listen_now.clear()
                        wake.reset()
                        endpoint.reset()
                        speaking = False
                        if vad:
                            vad.reset_states()
                        recording = []
                        skip = CHIME_FRAMES  # the "I'm listening" chime is playing: neither speech nor silence
                        self._set("recording", trigger="hotkey" if by_hotkey else "wake word", score=round(float(score), 2))
                        self.on_event("voice.wake", {"trigger": "hotkey" if by_hotkey else "wake word"})
                    continue
                if self.state == "recording":
                    if skip:
                        skip -= 1
                        continue
                    recording.append(frame)
                    if vad:
                        prob = vad.predict(frame, frame_size=640)
                        speaking = prob >= (VAD_STAY if speaking else VAD_START)  # hysteresis: no flicker inside words
                        verdict = endpoint.feed_speech(speaking)
                    else:
                        verdict = endpoint.feed(rms)
                    if verdict == "timeout":
                        self.last = {"heard": "", "note": "nothing said", "at": time.time()}
                        self._set("waiting", note="nothing said")
                        cooldown_until = time.time() + 1.0
                    elif verdict == "done":
                        self._set("transcribing")
                        audio = np.concatenate(recording).astype(np.float32) / 32768.0
                        self._transcribe(audio)
                        while not self._frames.empty():  # audio that arrived while transcribing: stale
                            self._frames.get_nowait()
                        wake.reset()
                        cooldown_until = time.time() + 1.5
                        self._set("waiting")
        finally:
            stream.stop()
            stream.close()

    def transcribe(self, audio) -> tuple[str, str, dict]:
        """(text, language, info) for float32 mono 16 kHz audio. Blocks until the speech model is loaded."""
        self._whisper_ready.wait()
        if self._whisper is None:
            raise RuntimeError(self.error or "speech model not loaded")
        t0 = time.time()
        opts = {"beam_size": 1, "vad_filter": True, "condition_on_previous_text": False, "without_timestamps": True}
        # 1. English or Tamil - never anything else. Left to choose freely, the model heard accented English through a
        #    laptop mic as Malay or Indonesian and wrote gibberish (29% word errors on 28 test clips; 28/28 + 12/12 right
        #    when only English and Tamil are allowed).
        detected, probability, all_probs = self._whisper.detect_language(audio)
        lang = pick_language(detected, dict(all_probs or []), self.languages)
        detect_s = time.time() - t0
        # 2. English: the English-only model with the words the user says to JARVIS (2.3% word errors, ~1 s);
        #    Tamil: the bigger multilingual model, which writes Tamil properly.
        if lang == "en" and self.english_size:
            model, model_used, extra = self._english_model(), self.english_size, {"initial_prompt": VOCABULARY}
        elif lang not in FAST_TEXT_LANGUAGES and self.model_size != self.fast_size:
            model, model_used, extra = self._accurate_model(), self.model_size, {}
        else:
            model, model_used, extra = self._whisper, self.fast_size, ({"initial_prompt": VOCABULARY} if lang == "en" else {})
        segments = list(model.transcribe(audio, language=lang, **opts, **extra)[0])
        text = " ".join(s.text.strip() for s in segments)
        no_speech = max((s.no_speech_prob for s in segments), default=1.0)
        logprob = min((s.avg_logprob for s in segments), default=-9.0)
        info_out = {"seconds": round(time.time() - t0, 1), "audio_seconds": round(len(audio) / RATE, 1), "detected": detected,
                    "probability": round(probability, 2), "no_speech": round(no_speech, 2), "logprob": round(logprob, 2),
                    "model": model_used, "fast_seconds": round(detect_s, 1)}
        if is_noise(text, no_speech, logprob):
            return "", lang, info_out
        return clean_transcript(text), lang, info_out

    def _keep_sample(self, audio, text: str, lang: str, info: dict) -> None:
        """Keep the last utterances (audio + what was heard) in <data>/voice-samples, on this computer only, so
        recognition can be measured on the user's real voice. The voice setting keep_samples=false turns it off."""
        if not self.keep_samples:
            return
        try:
            import wave

            import numpy as np

            folder = self.models_dir.parent / "voice-samples"
            folder.mkdir(parents=True, exist_ok=True)
            stem = folder / time.strftime(f"%Y%m%d-%H%M%S-{lang}")
            with wave.open(str(stem) + ".wav", "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
            (stem.with_suffix(".txt")).write_text(f"{text}\n{info}\n", encoding="utf-8")
            for old in sorted(folder.glob("*.wav"))[:-KEEP_SAMPLES]:
                old.unlink(missing_ok=True)
                old.with_suffix(".txt").unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - keeping a sample must never break listening
            pass

    def _transcribe(self, audio) -> None:
        try:
            text, lang, info = self.transcribe(audio)
        except Exception as e:  # noqa: BLE001
            self.error = f"transcription failed: {e}"
            self.on_event("voice.state", {"state": "error", "error": self.error})
            return
        self.last = {"heard": text, "language": lang, "at": time.time(), **info}
        self._keep_sample(audio, text, lang, info)
        if text and info["seconds"] > STALE_AFTER:  # the user has long moved on: say so instead of acting on it
            self.last["note"] = f"too slow ({info['seconds']:.0f} s) - not acted on"
            self.on_event("voice.slow", {"text": text, "seconds": info["seconds"]})
            return
        if not text:
            self.on_event("voice.state", {"state": "waiting", "note": "didn't catch that"})
            return
        self.on_utterance(text, lang, info)


# -- connection to JARVIS -----------------------------------------------------------------------------------------------
class VoiceService:
    """Owns the listener and the speaker and turns what is heard into chat turns. Lives on the server's event loop; the
    listener's and speaker's callbacks arrive on their own threads and are handed over with call_soon_threadsafe."""

    CONVERSATION_IDLE = 10 * 60  # a new spoken request after 10 quiet minutes starts a new conversation

    def __init__(self, app, speaker=None):
        self.app = app
        self.loop: asyncio.AbstractEventLoop | None = None
        self.listener: LocalListener | None = None
        self.conversation: str | None = None
        self.last_used = 0.0
        self._tasks: set = set()
        self.speaker = speaker  # made in start(): only the desktop app speaks out loud (never tests or the bare server)

    def enabled(self) -> bool:
        """On unless the user switched the voice engine to the browser's recogniser."""
        return self.app.db.get_setting("voice", {}).get("engine", "local") == "local"

    def available(self) -> str:
        """Empty when local listening can run, else why not."""
        missing = LocalListener.missing()
        return f"not installed: pip install {missing}" if missing else ""

    def status(self) -> dict:
        base = {"enabled": self.enabled(), "unavailable": self.available(), "running": bool(self.listener and self.listener.running()),
                "voices": self.speaker.voices() if self.speaker else {}}
        if self.listener:
            base.update(self.listener.status())
        return base

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> bool:
        self.loop = loop or self.loop or asyncio.get_running_loop()
        if not self.enabled() or self.available():
            return False
        if self.speaker is None:
            from .speak import Speaker

            self.speaker = Speaker(self._speaking) if Speaker.supported() else None
        if self.listener is None:
            s = self.app.settings
            self.listener = LocalListener(s.data_dir / "models", self._event, self._utterance, model_size=s.stt_local_model, fast_size=s.stt_fast_model,
                                          english_size=s.stt_english_model)
            self.listener.keep_samples = self.app.db.get_setting("voice", {}).get("keep_samples", True)
        self.listener.start()
        return True

    def stop(self) -> None:
        if self.speaker:
            self.speaker.stop()
        if self.listener:
            self.listener.stop()

    def listen_now(self) -> bool:
        if not (self.listener and self.listener.running()):
            return False
        if self.speaker:
            self.speaker.stop()
            self.speaker.chime()
        self.listener.listen_now()
        return True

    # called on the listener / speaker threads
    def _event(self, type_: str, data: dict) -> None:
        if type_ == "voice.wake" and self.speaker:
            self.speaker.stop()  # "Hey Jarvis" while it talks: stop talking and listen
            self.speaker.chime()  # ...and let the user hear that it is listening
        if type_ == "voice.slow" and self.speaker:
            self.speaker.say("Sorry, I was too slow to catch that. Please say it again.")
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(lambda: self.app.bus.emit(type_, **data))

    def _speaking(self, on: bool) -> None:
        if self.loop and not self.loop.is_closed():
            def report():
                if on:
                    self.app.bus.set_state("speaking")
                elif self.app.bus.assistant_state == "speaking":
                    self.app.bus.set_state("idle")
            self.loop.call_soon_threadsafe(report)

    def _utterance(self, text: str, lang: str, info: dict) -> None:
        if self.loop and not self.loop.is_closed():
            fut = asyncio.run_coroutine_threadsafe(self.handle(text, lang, info), self.loop)
            self._tasks.add(fut)
            fut.add_done_callback(self._tasks.discard)

    def speaks(self, lang: str) -> bool:
        """True when this server will say the reply out loud (else the JARVIS window does, if it is open)."""
        voice = self.app.db.get_setting("voice", {})
        return bool(self.speaker and voice.get("speak_replies", True) and self.speaker.can_speak(lang))

    async def handle(self, text: str, lang: str, info: dict | None = None) -> dict:
        """A spoken request: announce it (the window shows it), run it, and say the reply as it streams in."""
        from .db import new_id
        from .speak import SentenceSplitter

        now = time.time()
        if not self.conversation or now - self.last_used > self.CONVERSATION_IDLE:
            self.conversation = new_id("c_")
        self.last_used = now
        cid = self.conversation
        server_speaks = self.speaks(lang)
        self.app.bus.emit("voice.heard", conversation_id=cid, text=text, language=lang, server_speaks=server_speaks, **(info or {}))
        if not server_speaks:
            return await self.app.chat(text, cid, source="voice")
        self.speaker.rate = float(self.app.db.get_setting("voice", {}).get("rate", 1) or 1)
        events, streamed, split = self.app.bus.subscribe(), [], SentenceSplitter()

        async def follow():
            while True:
                e = await events.get()
                if e["type"] == "chat.delta" and e.get("conversation_id") == cid:
                    streamed.append(e.get("text", ""))
                    for sentence in split.feed(e.get("text", "")):
                        self.speaker.say(sentence)

        follower = asyncio.create_task(follow())
        try:
            result = await self.app.chat(text, cid, source="voice")
        finally:
            await asyncio.sleep(0)  # let the follower take the last deltas
            follower.cancel()
            self.app.bus.unsubscribe(events)
        so_far = "".join(streamed)
        final = result.get("text", "")
        rest = split.rest() + (final[len(so_far):] if final.startswith(so_far) else ("" if so_far else final))
        if rest.strip() and not self.speaker.say(rest):
            self.app.bus.emit("voice.say", conversation_id=cid, text=rest)  # no voice for this language here: the window says it
        return result
