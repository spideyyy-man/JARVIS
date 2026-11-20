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
WAKE_SKIP_FRAMES = 5  # 0.4 s after the wake word: the end of "Jarvis" and the chime are not the request
PRE_ROLL_FRAMES = 6  # 0.48 s kept before a follow-up starts, so its first word is not cut off
RETRY_BELOW = -0.6  # base.en less sure than this -> ask the bigger model too and keep the more confident text
UNCLEAR_BELOW = -0.95  # still this unsure -> "didn't catch that" instead of acting on it. On the owner's real recordings
#   every garbled transcription scored -0.96..-1.28 ("Return to the pre-order", "...link in the description below") and
#   the right ones -0.05..-0.5 (2026-11-20).
# Prompt for the plain-"Jarvis" check: naming the look-alikes lets the model tell them apart. Measured on 9 cases
# (synthetic voices + the owner's real "JARVIS, I want you..." and "Hey JARVIS"): 9/9 with this prompt; 5/9 with no
# prompt ("Jair is", "Jardus"); a prompt starting "Jarvis," made it write "Jarvis" before "Javascript is fun".
VERIFY_PROMPT = "Jarvis, Travis, JavaScript, service, nervous."
RETRY_MARGIN = 0.15
VERIFY_GAP = 1.0  # s between plain-"Jarvis" checks, so background chatter cannot keep the CPU busy
WAKE_PREFILTER = 0.03  # any hint of "jarvis" in a speech segment -> confirm with the English model (ordinary speech: 0.00)
WAKE_WHILE_TALKING = 0.85  # while JARVIS speaks, only a very clear wake word counts (it must not wake itself)
MIN_WAKE_FRAMES = 4  # 0.32 s: shorter sounds are not "Jarvis"
SEG_END_FRAMES = 6  # 0.48 s of quiet ends a speech segment
SEG_MAX_FRAMES = 75  # 6 s: longer speech is conversation, not "Jarvis, <request>"
WAKE_START = re.compile(r"^\W*(?:(?:hey|hi|ok|okay|yo)\W+)?(?:jarvis|jervis|jarvi|jarves|javis)\b\W*", re.I)
FOLLOW_START = 0.6  # speech probability that starts a follow-up (stricter than VAD_START: no wake word to confirm it)
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

    def __init__(self, noise: float = 120.0, start_timeout: float = 7.0, end_silence: float = 0.7, max_length: float = 15.0, min_speech: float = 0.35):
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
            if not self.started and self.silence >= 0.3:
                self.speech = 0.0  # scattered noise must not add up to "speech started"
        if not self.started:
            return "timeout" if self.elapsed >= self.start_timeout else "wait"
        if self.elapsed >= self.max_length:
            return "done"
        if self.silence >= self.end_silence:
            if self.speech >= self.min_speech:
                return "done"
            # Only a short sound before the pause - the end of "Jarvis", a cough: not the request. Keep waiting for it
            # (it used to end here, so JARVIS stopped listening before the user had said anything; 2026-11-20).
            self.started, self.speech, self.silence = False, 0.0, 0.0
            return "timeout" if self.elapsed >= self.start_timeout else "wait"
        return "speech"


WAKE_PREFIX = re.compile(r"^\s*(?:(?:hey|hi|ok|okay|ஹே)[\s,.!]*)?(?:jarvis|ஜார்விஸ்|ஜார்வீஸ்)[\s,.!?:]*", re.I)
# What Whisper tends to "hear" in silence or noise.
HALLUCINATIONS = {"", "you", "thank you", "thanks", "thank you for watching", "thanks for watching", "bye", "okay", "ok", "so", "uh", "um",
                  "subtitles by the amara.org community", "please subscribe", "like and subscribe", "oh", "நன்றி"}
HALLUCINATION_PARTS = ("link in the description", "subscribe to", "thanks for watching", "see you in the next")


def seg_quiet_at_end(frames: list, n: int) -> int:
    """A segment always ends with the quiet frames that closed it; they are not speech."""
    return min(n, len(frames))


def clean_transcript(text: str) -> str:
    """Drop a leading "Hey Jarvis" (the wake phrase is often still in the recording) and tidy spacing."""
    text = re.sub(r"\s+", " ", text).strip()
    return WAKE_PREFIX.sub("", text, count=1).strip()


def is_noise(text: str, no_speech: float, logprob: float) -> bool:
    plain = re.sub(r"[^\w\s]", "", text.lower()).strip()
    if plain in HALLUCINATIONS or any(part in plain for part in HALLUCINATION_PARTS):
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
        self._drain = threading.Event()
        self._jarvis_talking = False
        self._spoke_until = 0.0
        self._follow_until = 0.0
        self._trigger = ""
        self._last_lang: str | None = None
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

    def set_talking(self, on: bool) -> None:
        """JARVIS is (not) speaking: while it does, its own voice must not wake it or count as the user's follow-up."""
        self._jarvis_talking = on
        if not on:
            self._spoke_until = time.time() + 0.6  # the room still echoes for a moment

    def _verify_wake(self, audio) -> tuple[str, dict] | None:
        """Was this short speech segment "Jarvis ..."? (text after the wake word, info) or None. ~1 s with base.en."""
        if not self._whisper_ready.wait(timeout=0.1) or self._whisper is None:
            return None
        t0 = time.time()
        model = self._english_model() if self.english_size else self._whisper
        segments = list(model.transcribe(audio, language="en", beam_size=1, vad_filter=True, condition_on_previous_text=False,
                                         without_timestamps=True, initial_prompt=VERIFY_PROMPT)[0])
        text = " ".join(x.text.strip() for x in segments).strip()
        m = WAKE_START.match(text)
        if not m:
            return None
        rest = text[m.end():].strip()
        no_speech = max((x.no_speech_prob for x in segments), default=1.0)
        logprob = min((x.avg_logprob for x in segments), default=-9.0)
        if rest and (is_noise(rest, no_speech, logprob) or logprob < UNCLEAR_BELOW):
            rest = ""  # the wake word was clear but the rest was not: just "Jarvis" - listen for the request again
        return rest, {"seconds": round(time.time() - t0, 1), "audio_seconds": round(len(audio) / RATE, 1),
                      "model": self.english_size or self.fast_size, "detected": "en", "probability": 1.0,
                      "no_speech": round(no_speech, 2), "logprob": round(logprob, 2), "fast_seconds": 0.0}

    def follow_up(self, seconds: float = 8.0) -> None:
        """Conversation mode: for `seconds` the next thing the user says is taken without the wake word. Called when
        JARVIS has finished saying its reply (what the mic heard while it spoke is dropped first)."""
        if not self.running():
            return
        self._drain.set()
        self._follow_until = time.time() + seconds
        if self.state == "waiting":
            self._set("following", seconds=seconds)

    def end_follow_up(self) -> None:
        self._follow_until = 0.0
        if self.state == "following":
            self._set("waiting")

    def _end_follow_up(self) -> None:
        self._follow_until = 0.0
        if self.state == "following":
            self._set("waiting", note="conversation ended - say Hey Jarvis to start again")

    def listen_now(self) -> None:
        """Hotkey / Talk button: take the next utterance without the wake word."""
        self._listen_now.set()

    def status(self) -> dict:
        model = f"whisper-{self.english_size} (English)" if self.english_size else f"whisper-{self.fast_size}"
        if self.model_size != self.fast_size:
            model += f" + {self.model_size} (Tamil)"
        return {"state": self.state, "error": self.error, "model": model, "speech_model_ready": self._whisper_ready.is_set(),
                "wake_phrase": "Jarvis", "last": self.last}

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
        pre: collections.deque = collections.deque(maxlen=PRE_ROLL_FRAMES)  # the moment before a follow-up starts
        heard_run = 0  # consecutive speech frames seen during a follow-up window
        seg: list = []  # the current speech segment while waiting (checked for a plain "Jarvis")
        seg_score, seg_quiet, quiet_run, seg_after_quiet, next_verify = 0.0, 0, 99, False, 0.0
        try:
            while not self._stop.is_set():
                try:
                    frame = self._frames.get(timeout=0.5)
                except queue.Empty:
                    if self._follow_until and time.time() > self._follow_until:
                        self._end_follow_up()
                    continue
                if self._drain.is_set():  # JARVIS was talking: what the mic heard meanwhile is its own voice
                    self._drain.clear()
                    while not self._frames.empty():
                        self._frames.get_nowait()
                    pre.clear()
                    heard_run = 0
                    if vad:
                        vad.reset_states()
                    continue
                rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                if self.state in ("waiting", "following"):
                    endpoint.learn(rms)
                    score = max(wake.predict(frame).values())
                    prob = vad.predict(frame, frame_size=640) if vad else (1.0 if rms >= endpoint.threshold else 0.0)
                    now = time.time()
                    talking = self._jarvis_talking or now < self._spoke_until  # JARVIS's own voice is in the mic
                    pre.append(frame)
                    # Speech segments, for plain "Jarvis" (the ready-made model is trained on "hey jarvis" and scored
                    # plain "Jarvis" anywhere from 0.02 to 1.00 across voices; 2026-11-20).
                    if prob >= (VAD_STAY if seg else VAD_START):
                        if not seg:
                            seg_after_quiet = quiet_run >= SEG_END_FRAMES  # a separate utterance, not mid-conversation
                        seg = seg + [frame] if seg else list(pre)
                        seg_quiet, seg_score, quiet_run = 0, max(seg_score, score), 0
                    else:
                        quiet_run += 1
                        if seg:
                            seg.append(frame)
                            seg_quiet += 1
                    trigger = None
                    if self._listen_now.is_set():
                        trigger = "hotkey"
                    elif score >= (WAKE_WHILE_TALKING if talking else self.wake_threshold) and now >= cooldown_until:
                        trigger = "wake word"
                    elif self.state == "following" and not talking:
                        # conversation mode: the user just keeps talking - no wake word needed
                        heard_run = heard_run + 1 if prob >= FOLLOW_START else 0
                        if heard_run >= 2:
                            trigger = "follow-up"
                    if not trigger and seg and (seg_quiet >= SEG_END_FRAMES or len(seg) >= SEG_MAX_FRAMES):
                        candidate, cand_score = seg, seg_score
                        seg, seg_score, seg_quiet = [], 0.0, 0
                        speech_len = len(candidate) - seg_quiet_at_end(candidate, SEG_END_FRAMES)
                        worth = cand_score >= WAKE_PREFILTER or (seg_after_quiet and speech_len >= MIN_WAKE_FRAMES and now >= next_verify)
                        if worth and not talking and now >= cooldown_until and len(candidate) < SEG_MAX_FRAMES:
                            next_verify = time.time() + VERIFY_GAP
                            found = self._verify_wake(np.concatenate(candidate).astype(np.float32) / 32768.0)
                            if found is not None:
                                text, info = found
                                if text:  # "Jarvis, what time is it?" in one breath: that is the request - act on it now
                                    self._trigger = "jarvis"
                                    self._follow_until = 0.0
                                    self.on_event("voice.wake", {"trigger": "jarvis", "direct": True})
                                    self.last = {"heard": text, "language": "en", "at": time.time(), **info}
                                    self.on_utterance(text, "en", info)
                                    while not self._frames.empty():
                                        self._frames.get_nowait()
                                    cooldown_until = time.time() + 1.5
                                    pre.clear()
                                    if self.state == "following":
                                        self._set("waiting")
                                    continue
                                trigger = "jarvis"  # just "Jarvis": chime, then listen for the request
                    if self.state == "following" and now > self._follow_until:
                        self._end_follow_up()
                    if trigger:
                        self._listen_now.clear()
                        self._follow_until = 0.0
                        wake.reset()
                        endpoint.reset()
                        # follow-ups and "yes"/"no" are short; requests after the wake word get room for a thinking pause
                        endpoint.min_speech = 0.15 if trigger == "follow-up" else 0.35
                        endpoint.end_silence = (SPEECH_END_VAD if vad else 0.7) + (0.0 if trigger == "follow-up" else 0.25)
                        speaking = trigger == "follow-up"
                        if trigger == "follow-up":
                            recording = list(pre)
                            endpoint.feed_speech(True)
                            endpoint.feed_speech(True)  # already talking: the two frames that started it
                        else:
                            # "hey jarvis what time..." in one go: keep what was already said (the wake phrase is
                            # removed from the text later), but count speech afresh from here
                            recording = list(seg) if trigger == "wake word" and seg else []
                            if vad:
                                vad.reset_states()
                        pre.clear()
                        seg, seg_score, seg_quiet, heard_run = [], 0.0, 0, 0
                        skip = CHIME_FRAMES if trigger in ("wake word", "hotkey", "jarvis") else 0  # the chime is not speech
                        self._trigger = trigger
                        self._set("recording", trigger=trigger, score=round(float(score), 2))
                        self.on_event("voice.wake", {"trigger": trigger})
                    continue
                if self.state == "recording":
                    recording.append(frame)  # everything is kept; the chime only pauses the end-of-speech count
                    if skip:
                        skip -= 1
                        continue
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
                        if self.state == "transcribing":
                            self._set("waiting")
        finally:
            stream.stop()
            stream.close()

    def transcribe(self, audio, language: str | None = None) -> tuple[str, str, dict]:
        """(text, language, info) for float32 mono 16 kHz audio. Blocks until the speech model is loaded. With
        `language` (a follow-up in a conversation: the user is still speaking the same language) detection is skipped."""
        self._whisper_ready.wait()
        if self._whisper is None:
            raise RuntimeError(self.error or "speech model not loaded")
        t0 = time.time()
        opts = {"beam_size": 1, "vad_filter": True, "condition_on_previous_text": False, "without_timestamps": True}
        # 1. English or Tamil - never anything else. Left to choose freely, the model heard accented English through a
        #    laptop mic as Malay or Indonesian and wrote gibberish (29% word errors on 28 test clips; 28/28 + 12/12 right
        #    when only English and Tamil are allowed).
        if language in self.languages:
            detected, probability, lang = language, 1.0, language  # saves a model pass (~1-2 s on the busy laptop)
        else:
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
        if lang == "en" and logprob < RETRY_BELOW and self.model_size not in (self.english_size, self.fast_size):
            # unsure: the bigger model hears some of these right ("What is the weather right now?"), others worse -
            # keep whichever is more confident
            alt = list(self._accurate_model().transcribe(audio, language="en", **opts, initial_prompt=VOCABULARY)[0])
            alt_lp = min((x.avg_logprob for x in alt), default=-9.0)
            if alt and alt_lp > logprob + RETRY_MARGIN:  # clearly more confident only ("Follow me" -> "Ah, mama" was not)
                segments, logprob, model_used = alt, alt_lp, self.model_size
                text = " ".join(x.text.strip() for x in alt)
                no_speech = max((x.no_speech_prob for x in alt), default=1.0)
        info_out = {"seconds": round(time.time() - t0, 1), "audio_seconds": round(len(audio) / RATE, 1), "detected": detected,
                    "probability": round(probability, 2), "no_speech": round(no_speech, 2), "logprob": round(logprob, 2),
                    "model": model_used, "fast_seconds": round(detect_s, 1)}
        if is_noise(text, no_speech, logprob):
            return "", lang, info_out
        if lang == "en" and logprob < UNCLEAR_BELOW:
            info_out["unclear"] = text  # what it thought it heard, for the log - not acted on
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
            hint = self._last_lang if self._trigger == "follow-up" else None
            text, lang, info = self.transcribe(audio) if hint is None else self.transcribe(audio, language=hint)
            self._last_lang = lang
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
            if info.get("unclear"):
                self.last["note"] = f"unclear (heard {info['unclear'][:40]!r}) - asked to repeat"
                self.on_event("voice.unclear", {"guess": info["unclear"], "logprob": info.get("logprob")})
            self.on_event("voice.state", {"state": "waiting", "note": "didn't catch that"})
            return
        self.on_utterance(text, lang, info)


FOLLOW_UP_SECONDS = 5.0  # after a spoken reply, how long the next request is taken without the wake word
CONVERSATION_ENDS = re.compile(r"^(?:thanks?|thank you|that'?s all|that is all|never ?mind|stop|bye|goodbye|cancel|ok(?:ay)? thanks?)"
                               r"(?: jarvis)?[.!]*$", re.I)


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
        self._follow_pending = False
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

            self.speaker = Speaker(self._speaking, self.engines()) if Speaker.supported() else None
            if self.speaker and self.speaker.engines and self.speaker.engines[0].name != "windows":
                first = self.speaker.engines[0]  # load the neural voice now, not on the first reply (~2 s)
                threading.Thread(target=lambda: first.wav("Ready.", "en", 1.0), daemon=True, name="jarvis-voice-warm").start()
        if self.listener is None:
            s = self.app.settings
            self.listener = LocalListener(s.data_dir / "models", self._event, self._utterance, model_size=s.stt_local_model, fast_size=s.stt_fast_model,
                                          english_size=s.stt_english_model)
            self.listener.keep_samples = self.app.db.get_setting("voice", {}).get("keep_samples", True)
        self.listener.start()
        return True

    def engines(self) -> list:
        """Voices for spoken replies: the user's choice (voice setting local_tts, else JARVIS_LOCAL_TTS), then Windows'."""
        from .speak import make_engines

        s = self.app.settings
        choice = self.app.db.get_setting("voice", {}).get("local_tts") or s.local_tts
        return make_engines(choice, s.data_dir / "models", s.piper_voice, s.kokoro_voice)

    def set_engine(self) -> None:
        if self.speaker:
            self.speaker.stop()
            self.speaker.engines = self.engines()

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
        if type_ == "voice.wake":
            self._follow_pending = False
            if self.speaker:
                self.speaker.stop()  # "Hey Jarvis" while it talks: stop talking and listen
                if data.get("trigger") != "follow-up" and not data.get("direct"):
                    self.speaker.chime()  # ...and let the user hear that it is listening (a follow-up is already talking)
        if type_ == "voice.unclear" and self.speaker:
            self._follow_pending = True  # after the apology, listen again - no wake word needed to repeat it
            self.speaker.say("Sorry, I didn't catch that. Please say it again.")
        if type_ == "voice.slow" and self.speaker:
            self.speaker.say("Sorry, I was too slow to catch that. Please say it again.")
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(lambda: self.app.bus.emit(type_, **data))

    def _speaking(self, on: bool) -> None:
        if self.listener:
            self.listener.set_talking(on)
        if not on and self._follow_pending and self.listener:  # the whole reply has been said: listen for the next thing
            self._follow_pending = False
            self.listener.follow_up(FOLLOW_UP_SECONDS)
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

    def say_aloud(self, text: str, alert: bool = False) -> bool:
        """Announce something unprompted (a timer, an alarm, a reminder). True when this server said it."""
        from .speak import language_of_text

        if not self.speaks(language_of_text(text)):
            return False
        if alert:
            self.speaker.chime()
        return self.speaker.say(text)

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
        # Conversation mode: once the reply has been said, the next thing the user says needs no "Hey Jarvis"
        # (the owner could not hold a conversation: every question needed the wake word again; 2026-11-20).
        if self.listener and not CONVERSATION_ENDS.match(text.strip()):
            if self.speaker.speaking or not self.speaker.idle():
                self._follow_pending = True  # _speaking(False) opens the window when the last sentence has been said
            else:
                self.listener.follow_up(FOLLOW_UP_SECONDS)
        return result
