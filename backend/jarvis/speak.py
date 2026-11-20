"""Spoken replies from JARVIS itself, with Windows' own offline voices (the ones Narrator uses).

Why: replies used to be spoken only by the JARVIS browser window. On the owner's laptop that window, minimised, lost
its connection to the server (Edge froze the page) and spoken questions got no audible answer (2026-11-20). Now the
server speaks what the offline listener asked, so no window is needed: Windows.Media.SpeechSynthesis (WinRT, ~0.3 s to
synthesise a sentence on this laptop) -> a WAV file -> winsound. Sentences are spoken as the reply streams in.

Voices: Piper (English, natural) by default; Tamil with MMS Tamil (MmsTamilEngine, offline); Windows' own voices for
anything else (on the owner's laptop: Heera, Ravi, David, Zira, Mark - English only). Text no voice can say returns
False from `say` and the caller lets the browser window speak it instead.
"""
from __future__ import annotations

import asyncio
import os
import queue
import re
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

TAMIL = re.compile(r"[஀-௿]")
SENTENCE_END = re.compile(r"[.!?:;](?=\s)|\n+")


def speakable(md: str) -> str:
    """Markdown -> plain words worth saying (same rules as the page's speakable())."""
    t = re.sub(r"```[\s\S]*?```", " code block omitted. ", md)
    t = re.sub(r"```[\s\S]*$", " ", t)
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+|file:///\S+", "link", t)
    t = re.sub(r"(?m)^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+", "", t)
    t = re.sub(r"(?m)^\s*\|?[\s:|-]{3,}\|?\s*$", "", t)
    t = t.replace("|", ", ")
    t = re.sub(r"[*_`~#]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def language_of_text(text: str) -> str:
    return "ta" if TAMIL.search(text) else "en"


class SentenceSplitter:
    """Collects streamed text and hands out whole sentences, never cutting inside an unfinished code block."""

    def __init__(self):
        self.buf = ""

    def feed(self, piece: str) -> list[str]:
        self.buf += piece
        if self.buf.count("```") % 2:
            return []
        cut = -1
        for m in SENTENCE_END.finditer(self.buf):
            cut = m.end()
        if cut <= 0:
            return []
        out, self.buf = self.buf[:cut], self.buf[cut:]
        return [out]

    def rest(self) -> str:
        out, self.buf = self.buf, ""
        return out


def _wav_bytes(samples, sr: int) -> bytes:
    import io

    import numpy as np

    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class PiperEngine:
    """Neural voice, offline (Piper, ONNX): natural, ~10x faster than real time on the owner's laptop (0.2 s for a
    short sentence). English only here. The Windows voices sounded robotic (owner, 2026-11-20)."""
    name = "piper"
    langs = ("en",)

    def __init__(self, model: Path):
        self.model, self._voice = Path(model), None

    def available(self) -> bool:
        import importlib.util

        return self.model.is_file() and importlib.util.find_spec("piper") is not None

    def display(self) -> str:
        return "Piper " + self.model.stem.split("-")[1] if "-" in self.model.stem else "Piper"

    def wav(self, text: str, lang: str, rate: float) -> bytes:
        import io

        from piper import PiperVoice, SynthesisConfig

        if self._voice is None:
            self._voice = PiperVoice.load(str(self.model))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self._voice.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1 / max(0.5, min(2.0, rate or 1))))
        return buf.getvalue()


class KokoroEngine:
    """The most natural offline voice (Kokoro 82M int8), but only about as fast as real time on the owner's laptop
    (~2 s before a short sentence) and it competes with the language model for the CPU. English only."""
    name = "kokoro"
    langs = ("en",)

    def __init__(self, model: Path, voices: Path, voice: str = "af_heart"):
        self.model, self.voices_file, self.voice, self._k = Path(model), Path(voices), voice, None

    def available(self) -> bool:
        import importlib.util

        return self.model.is_file() and self.voices_file.is_file() and importlib.util.find_spec("kokoro_onnx") is not None

    def display(self) -> str:
        return f"Kokoro {self.voice}"

    def wav(self, text: str, lang: str, rate: float) -> bytes:
        from kokoro_onnx import Kokoro

        if self._k is None:
            self._k = Kokoro(str(self.model), str(self.voices_file))
        samples, sr = self._k.create(text, voice=self.voice, speed=max(0.5, min(2.0, rate or 1)),
                                     lang="en-gb" if self.voice.startswith("b") else "en-us")
        return _wav_bytes(samples, sr)


# -- Tamil -------------------------------------------------------------------------------------------------------------
_TA_UNITS = ["பூஜ்ஜியம்", "ஒன்று", "இரண்டு", "மூன்று", "நான்கு", "ஐந்து", "ஆறு", "ஏழு", "எட்டு", "ஒன்பது"]
_TA_TEENS = ["பத்து", "பதினொன்று", "பன்னிரண்டு", "பதிமூன்று", "பதினான்கு", "பதினைந்து", "பதினாறு", "பதினேழு", "பதினெட்டு", "பத்தொன்பது"]
# (alone, followed by more): இருபது / இருபத்தி ஐந்து
_TA_TENS = {2: ("இருபது", "இருபத்தி"), 3: ("முப்பது", "முப்பத்தி"), 4: ("நாற்பது", "நாற்பத்தி"), 5: ("ஐம்பது", "ஐம்பத்தி"),
            6: ("அறுபது", "அறுபத்தி"), 7: ("எழுபது", "எழுபத்தி"), 8: ("எண்பது", "எண்பத்தி"), 9: ("தொண்ணூறு", "தொண்ணூற்றி")}
_TA_HUNDREDS = {1: ("நூறு", "நூற்றி"), 2: ("இருநூறு", "இருநூற்றி"), 3: ("முந்நூறு", "முந்நூற்றி"), 4: ("நானூறு", "நானூற்றி"),
                5: ("ஐந்நூறு", "ஐந்நூற்றி"), 6: ("அறுநூறு", "அறுநூற்றி"), 7: ("எழுநூறு", "எழுநூற்றி"), 8: ("எண்ணூறு", "எண்ணூற்றி"),
                9: ("தொள்ளாயிரம்", "தொள்ளாயிரத்தி")}


def tamil_number(n: int) -> str:
    """25 -> 'இருபத்தி ஐந்து', 1500 -> 'ஆயிரத்தி ஐந்நூறு'. The Tamil voice reads digits badly, so they become words."""
    if n < 0:
        return "மைனஸ் " + tamil_number(-n)
    if n < 10:
        return _TA_UNITS[n]
    if n < 20:
        return _TA_TEENS[n - 10]

    def join(head: tuple[str, str], rest: int) -> str:
        return head[1] + " " + tamil_number(rest) if rest else head[0]
    if n < 100:
        return join(_TA_TENS[n // 10], n % 10)
    if n < 1000:
        return join(_TA_HUNDREDS[n // 100], n % 100)
    for size, alone, more in ((10_000_000, "கோடி", "கோடியே"), (100_000, "லட்சம்", "லட்சத்தி"), (1000, "ஆயிரம்", "ஆயிரத்தி")):
        if n >= size:
            count, rest = divmod(n, size)
            if count >= 100 and size == 10_000_000:
                return " ".join(_TA_UNITS[int(d)] for d in str(n))  # too long to say as a number
            head = (alone, more) if count == 1 else (tamil_number(count) + " " + alone, tamil_number(count) + " " + more)
            if count == 1 and size != 1000:
                head = ("ஒரு " + alone, "ஒரு " + more)
            return join(head, rest)
    return str(n)


def tamil_speakable(text: str) -> str:
    """What the Tamil voice should say: numbers, times, decimals, % and degrees as Tamil words."""
    t = re.sub(r"(?<=\d),(?=\d{2,3}\b)", "", text)  # 1,500 / 1,00,000
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"{int(m.group(1))} மணி" + (f" {int(m.group(2))} நிமிடம்" if int(m.group(2)) else ""), t)
    t = re.sub(r"(\d)\s*°\s*[CcFf]?\b|(\d)\s*°", lambda m: (m.group(1) or m.group(2)) + " டிகிரி", t)
    t = re.sub(r"(\d)\s*%", r"\1 சதவீதம்", t)
    t = re.sub(r"(\d+)\.(\d+)", lambda m: f"{m.group(1)} புள்ளி " + " ".join(_TA_UNITS[int(d)] for d in m.group(2)), t)
    return re.sub(r"\d+", lambda m: tamil_number(int(m.group(0))) if len(m.group(0)) <= 9 else " ".join(_TA_UNITS[int(d)] for d in m.group(0)), t)


class MmsTamilEngine:
    """Offline Tamil voice: Meta's MMS Tamil (VITS, ONNX export, ~109 MB in <models>/tts/mms-tam, licence CC-BY-NC 4.0 -
    personal use, like the translation model). ~3x faster than real time on the owner's laptop (a 4 s sentence in
    ~1.3 s); Whisper reads its output back almost word for word. English words inside a Tamil reply (Chrome, GB...)
    are said by the English voice, so they are not dropped (2026-11-20; no Windows Tamil voice is installed here)."""
    name = "mms-ta"
    langs = ("ta",)
    RATE = 16000

    def __init__(self, folder: Path, english=None):
        self.folder, self.english = Path(folder), english
        self._session = self._tokens = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        import importlib.util

        return (self.folder / "model.onnx").is_file() and (self.folder / "tokens.txt").is_file() and importlib.util.find_spec("onnxruntime") is not None

    def display(self) -> str:
        return "MMS Tamil"

    def _load_tokens(self) -> dict:
        if self._tokens is None:
            tokens = {}
            for line in (self.folder / "tokens.txt").read_text(encoding="utf-8").splitlines():
                ch, _, idx = line.rpartition(" ")
                if idx.isdigit():
                    tokens[ch or " "] = int(idx)
            self._tokens = tokens
        return self._tokens

    def _load(self):
        if self._session is None:
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.intra_op_num_threads = 4
            self._session = ort.InferenceSession(str(self.folder / "model.onnx"), so, providers=["CPUExecutionProvider"])
        return self._session, self._load_tokens()

    def ids(self, text: str) -> list[int]:
        """Characters -> ids with a blank (0) between them (the model was trained with add_blank)."""
        import unicodedata

        tokens = self._load_tokens()
        out = [0]
        for ch in unicodedata.normalize("NFC", text.lower()):
            if ch in tokens:
                out += [tokens[ch], 0]
        return out

    def _tamil(self, text: str, rate: float):
        import numpy as np

        ids = self.ids(text)
        if len(ids) < 3:
            return np.zeros(0, np.float32)
        session, _ = self._load()
        x = np.array([ids], dtype=np.int64)
        with self._lock:
            y = session.run(None, {"x": x, "x_length": np.array([x.shape[1]], np.int64), "noise_scale": np.array([0.667], np.float32),
                                   "length_scale": np.array([1 / max(0.5, min(2.0, rate or 1))], np.float32),
                                   "noise_scale_w": np.array([0.8], np.float32)})[0]
        return y.reshape(-1).astype(np.float32)

    def _latin(self, text: str, rate: float):
        """An English word said by the English voice, resampled to this voice's rate."""
        import io

        import numpy as np

        if not self.english or not re.search(r"[A-Za-z]", text):
            return np.zeros(0, np.float32)
        try:
            with wave.open(io.BytesIO(self.english.wav(text, "en", rate))) as w:
                sr, pcm = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        except Exception:  # noqa: BLE001 - better a skipped word than a skipped sentence
            return np.zeros(0, np.float32)
        if sr != self.RATE and len(pcm):
            pcm = np.interp(np.arange(0, len(pcm), sr / self.RATE), np.arange(len(pcm)), pcm).astype(np.float32)
        return pcm

    def wav(self, text: str, lang: str, rate: float) -> bytes:
        import numpy as np

        pieces, pause = [], np.zeros(int(self.RATE * 0.12), np.float32)
        for clause in re.split(r"\s*[,;:]\s*", tamil_speakable(text)):
            # runs of Tamil and of Latin letters, so each is said by a voice that knows it
            for run in re.findall(r"[A-Za-z][A-Za-z0-9 .'&+-]*[A-Za-z0-9]|[A-Za-z]|[^A-Za-z]+", clause):
                audio = self._latin(run, rate) if re.match(r"[A-Za-z]", run) else self._tamil(run.strip(), rate)
                if len(audio):
                    pieces.append(audio)
            pieces.append(pause)
        samples = np.concatenate(pieces) if pieces else np.zeros(1, np.float32)
        return _wav_bytes(samples, self.RATE)


class WindowsEngine:
    """Windows' own voices (WinRT SpeechSynthesis): instant, but robotic. Used for languages the neural voice does
    not speak (a Tamil voice installed in Windows would be picked up here)."""
    name = "windows"

    def __init__(self):
        self._voices: dict | None = None
        self._loop = self._synth = None

    def available(self) -> bool:
        if os.name != "nt":
            return False
        import importlib.util

        return importlib.util.find_spec("winrt.windows.media.speechsynthesis") is not None

    def voices(self) -> dict:
        if self._voices is None:
            from winrt.windows.media.speechsynthesis import SpeechSynthesizer

            voices = list(SpeechSynthesizer.all_voices)

            def pick(lang: str, prefer: tuple[str, ...]):
                own = [v for v in voices if v.language.lower().startswith(lang)]
                for region in prefer:
                    for v in own:
                        if v.language.lower() == region and getattr(v.gender, "value", v.gender) == 1:  # 1 = female
                            return v
                    for v in own:
                        if v.language.lower() == region:
                            return v
                return own[0] if own else None
            self._voices = {lang: v for lang, v in (("en", pick("en", ("en-in", "en-gb", "en-us"))), ("ta", pick("ta", ("ta-in",)))) if v}
        return self._voices

    @property
    def langs(self) -> tuple[str, ...]:
        try:
            return tuple(self.voices()) if self.available() else ()
        except Exception:  # noqa: BLE001
            return ()

    def display(self) -> str:
        return ", ".join(v.display_name for v in self.voices().values())

    def wav(self, text: str, lang: str, rate: float) -> bytes:
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        from winrt.windows.storage.streams import DataReader

        if self._synth is None:  # created in the speaking thread that uses it
            self._loop, self._synth = asyncio.new_event_loop(), SpeechSynthesizer()

        async def run() -> bytes:
            self._synth.voice = self.voices()[lang]
            self._synth.options.speaking_rate = max(0.5, min(3.0, float(rate or 1)))
            stream = await self._synth.synthesize_text_to_stream_async(text)
            reader = DataReader(stream.get_input_stream_at(0))
            await reader.load_async(stream.size)
            buf = bytearray(stream.size)
            reader.read_bytes(buf)
            return bytes(buf)
        return self._loop.run_until_complete(run())


def make_engines(choice: str, models_dir: Path, piper_voice: str = "en_US-lessac-medium", kokoro_voice: str = "af_heart") -> list:
    """The chosen engine first, then the Tamil voice, then Windows' voices for whatever they cannot speak. Missing models
    are skipped."""
    tts = Path(models_dir) / "tts"
    engines = {"piper": PiperEngine(tts / f"{piper_voice}.onnx"),
               "kokoro": KokoroEngine(tts / "kokoro-v1.0.int8.onnx", tts / "voices-v1.0.bin", kokoro_voice),
               "mms-ta": MmsTamilEngine(tts / "mms-tam"),
               "windows": WindowsEngine()}
    order = [choice, "mms-ta", "windows"] if choice in engines and choice not in ("windows", "mms-ta") else ["mms-ta", "windows"]
    chosen = [engines[n] for n in order if engines[n].available()]
    engines["mms-ta"].english = next((e for e in chosen if "en" in e.langs), None)  # says the English words in Tamil replies
    return chosen


class Speaker:
    """One worker thread speaks queued text in order; `stop()` silences it at once (barge-in)."""

    def __init__(self, on_state: Callable[[bool], None] | None = None, engines: list | None = None):
        self.on_state = on_state or (lambda speaking: None)
        self.rate = 1.0
        self.speaking = False
        self._queue: queue.Queue = queue.Queue()
        self._gen = 0
        self.engines = engines if engines is not None else [WindowsEngine()]
        self._thread: threading.Thread | None = None
        self._error = ""
        # sentences queued and not yet finished playing. "Idle" used to mean "both queues empty", which was also true
        # while the next sentence was being synthesised: the follow-up window opened mid-reply and the microphone
        # took JARVIS's next sentence for the user's request (2026-11-20: "I can't provide money..." heard back).
        self._pending = 0
        self._pending_lock = threading.Lock()
        self.recent: list[tuple[float, str]] = []  # (time, words) said lately: what the microphone may hear back

    def engine_for(self, lang: str):
        return next((e for e in self.engines if lang in e.langs), None)

    # -- voices ----------------------------------------------------------------------------------------------------
    @staticmethod
    def supported() -> bool:
        """Playback uses winsound (Windows). Which voices exist is decided by make_engines."""
        return os.name == "nt"

    def voices(self) -> dict[str, str]:
        out = {}
        for lang in ("en", "ta"):
            try:
                e = self.engine_for(lang)
                if e:
                    out[lang] = e.display() if e.name != "windows" else e.voices()[lang].display_name
            except Exception as ex:  # noqa: BLE001
                self._error = str(ex)
        return out

    def can_speak(self, lang: str) -> bool:
        try:
            return self.engine_for(lang) is not None
        except Exception:  # noqa: BLE001
            return False

    # -- speaking ---------------------------------------------------------------------------------------------------------
    def say(self, text: str, lang: str | None = None) -> bool:
        """Queue `text` (Markdown is fine). False when there is no voice for its language - nothing is queued."""
        words = speakable(text)
        if not words:
            return True
        lang = lang or language_of_text(words)
        if not self.can_speak(lang):
            return False
        self._ensure_thread()
        self._add_pending(1)
        self._set(True)
        now = time.time()
        self.recent = [(t, w) for t, w in self.recent if now - t < 60][-12:] + [(now, words)]
        self._queue.put((self._gen, lang, words))
        return True

    def _add_pending(self, n: int) -> None:
        with self._pending_lock:
            self._pending = max(0, self._pending + n)
            done = self._pending == 0
        if done:
            self._set(False)

    def idle(self) -> bool:
        """Nothing queued, being synthesised or playing."""
        return self._pending == 0

    def chime(self) -> None:
        """A short rising two-note tone, played at once: "I heard 'Hey Jarvis', go ahead"."""
        if os.name != "nt":
            return
        try:
            import winsound

            if not getattr(self, "_chime_path", None):
                self._chime_path = _write_chime()
            winsound.PlaySound(self._chime_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception as e:  # noqa: BLE001 - a missing chime must never stop listening
            self._error = f"chime: {e}"

    def stop(self) -> None:
        """Barge-in: drop everything queued and cut the sentence being spoken."""
        self._gen += 1
        with self._pending_lock:
            self._pending = 0
        self._set(False)
        for q in (self._queue, getattr(self, "_ready", None)):
            try:
                while q is not None:
                    q.get_nowait()
            except queue.Empty:
                pass
        try:
            import winsound

            winsound.PlaySound(None, 0)
        except Exception:  # noqa: BLE001
            pass

    def _ensure_thread(self) -> None:
        if not (self._thread and self._thread.is_alive()):
            self._ready: queue.Queue = queue.Queue(maxsize=3)  # synthesised sentences waiting to be played
            self._thread = threading.Thread(target=self._synthesise, daemon=True, name="jarvis-speaker")
            self._thread.start()
            threading.Thread(target=self._play, daemon=True, name="jarvis-speaker-play").start()

    def _set(self, speaking: bool) -> None:
        if speaking != self.speaking:
            self.speaking = speaking
            try:
                self.on_state(speaking)
            except Exception:  # noqa: BLE001
                pass

    def _synthesise(self) -> None:
        """Turns queued sentences into WAV files ahead of playback, so the next sentence is ready the moment the
        current one ends (synthesising after each sentence left a ~0.3 s gap between sentences)."""
        while True:
            gen, lang, text = self._queue.get()
            if gen != self._gen:
                continue  # stop() already cleared the count
            try:
                audio = self._synth_one(text, lang)
            except Exception as e:  # noqa: BLE001 - one bad sentence must not silence the rest
                self._error = f"{type(e).__name__}: {e}"
                if gen == self._gen:
                    self._add_pending(-1)
                continue
            if gen != self._gen:
                continue
            fd, path = tempfile.mkstemp(prefix="jarvis-say-", suffix=".wav")
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
            self._ready.put((gen, path))

    def _synth_one(self, text: str, lang: str) -> bytes:
        """With the first engine that speaks `lang`; if a neural engine fails, Windows' voice says it instead."""
        errors = []
        for e in [x for x in self.engines if lang in x.langs]:
            try:
                return e.wav(text, lang, self.rate)
            except Exception as ex:  # noqa: BLE001
                errors.append(f"{e.name}: {type(ex).__name__}: {ex}")
        raise RuntimeError("; ".join(errors) or f"no voice for {lang}")

    def _play(self) -> None:
        import winsound

        while True:
            gen, path = self._ready.get()
            try:
                if gen == self._gen:
                    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)  # blocks; stop() cuts it
            except Exception as e:  # noqa: BLE001
                self._error = f"{type(e).__name__}: {e}"
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if gen == self._gen:
                self._add_pending(-1)  # finished: only now may the listener take the next words as the user's


def _write_chime() -> str:
    """880 Hz then 1320 Hz, 90 ms each, soft, as a 16 kHz WAV in the temp folder (made once)."""
    import math
    import struct
    import wave

    rate, notes, out = 16000, (880, 1320), bytearray()
    for freq in notes:
        n = int(rate * 0.09)
        for i in range(n):
            fade = min(1.0, i / 160, (n - i) / 160)  # no clicks
            out += struct.pack("<h", int(6000 * fade * math.sin(2 * math.pi * freq * i / rate)))
    path = os.path.join(tempfile.gettempdir(), "jarvis-chime.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(out))
    return path


PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


MMS_TAMIL_URL = "https://huggingface.co/willwade/mms-tts-multilingual-models-onnx/resolve/main/tam/"


def download_voices(dest: Path, kokoro: bool = False, tamil: bool = True, log=print) -> bool:
    """Fetch the Piper voice (~63 MB), the Tamil voice (~109 MB) and - when asked - Kokoro (~115 MB) into `dest`;
    skips what is there."""
    import httpx

    files = [(PIPER_URL + "en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx"), (PIPER_URL + "en_US-lessac-medium.onnx.json", "en_US-lessac-medium.onnx.json")]
    if tamil:
        files += [(MMS_TAMIL_URL + "tokens.txt", "mms-tam/tokens.txt"), (MMS_TAMIL_URL + "model.onnx", "mms-tam/model.onnx")]
    if kokoro:
        files += [(KOKORO_URL + "kokoro-v1.0.int8.onnx", "kokoro-v1.0.int8.onnx"), (KOKORO_URL + "voices-v1.0.bin", "voices-v1.0.bin")]
    dest.mkdir(parents=True, exist_ok=True)
    ok = True
    for url, name in files:
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > 0:
            continue
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(120, connect=20)) as r:
                r.raise_for_status()
                tmp = path.with_suffix(path.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                tmp.replace(path)
            log(f"   {name}: ok")
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f"   {name}: failed ({type(e).__name__})")
    return ok


if __name__ == "__main__":  # python -m jarvis.speak [--kokoro]  -> download the natural voices where JARVIS looks for them
    import sys

    from .config import load_settings

    s = load_settings()
    sys.exit(0 if download_voices(s.data_dir / "models" / "tts", kokoro="--kokoro" in sys.argv) else 1)
