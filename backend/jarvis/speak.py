"""Spoken replies from JARVIS itself, with Windows' own offline voices (the ones Narrator uses).

Why: replies used to be spoken only by the JARVIS browser window. On the owner's laptop that window, minimised, lost
its connection to the server (Edge froze the page) and spoken questions got no audible answer (2026-11-20). Now the
server speaks what the offline listener asked, so no window is needed: Windows.Media.SpeechSynthesis (WinRT, ~0.3 s to
synthesise a sentence on this laptop) -> a WAV file -> winsound. Sentences are spoken as the reply streams in.

Voices on the owner's laptop: Heera and Ravi (English, India), David, Zira, Mark (English, US); no Tamil voice is
installed, so Tamil text returns False from `say` and the caller lets the browser window speak it instead. Adding a
Tamil voice in Windows (Settings > Time & language > Speech > Add voices) makes Tamil work here too.
"""
from __future__ import annotations

import asyncio
import os
import queue
import re
import tempfile
import threading
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
    """The chosen engine first, then Windows' voices for whatever it cannot speak. Missing models are skipped."""
    tts = Path(models_dir) / "tts"
    engines = {"piper": PiperEngine(tts / f"{piper_voice}.onnx"),
               "kokoro": KokoroEngine(tts / "kokoro-v1.0.int8.onnx", tts / "voices-v1.0.bin", kokoro_voice),
               "windows": WindowsEngine()}
    order = [choice, "windows"] if choice in engines and choice != "windows" else ["windows"]
    return [engines[n] for n in order if engines[n].available()]


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
        self._queue.put((self._gen, lang, words))
        return True

    def idle(self) -> bool:
        """Nothing queued, being synthesised or playing."""
        ready = getattr(self, "_ready", None)
        return not self.speaking and self._queue.empty() and (ready is None or ready.empty())

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
                continue
            try:
                audio = self._synth_one(text, lang)
            except Exception as e:  # noqa: BLE001 - one bad sentence must not silence the rest
                self._error = f"{type(e).__name__}: {e}"
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
            try:
                gen, path = self._ready.get(timeout=0.3)
            except queue.Empty:
                if self._queue.empty():
                    self._set(False)
                continue
            try:
                if gen == self._gen:
                    self._set(True)
                    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)  # blocks; stop() cuts it
            except Exception as e:  # noqa: BLE001
                self._error = f"{type(e).__name__}: {e}"
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if self._queue.empty() and self._ready.empty():
                self._set(False)


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


def download_voices(dest: Path, kokoro: bool = False, log=print) -> bool:
    """Fetch the Piper voice (~63 MB) - and Kokoro (~115 MB) when asked - into `dest`; resumes, skips what is there."""
    import httpx

    files = [(PIPER_URL + "en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx"), (PIPER_URL + "en_US-lessac-medium.onnx.json", "en_US-lessac-medium.onnx.json")]
    if kokoro:
        files += [(KOKORO_URL + "kokoro-v1.0.int8.onnx", "kokoro-v1.0.int8.onnx"), (KOKORO_URL + "voices-v1.0.bin", "voices-v1.0.bin")]
    dest.mkdir(parents=True, exist_ok=True)
    ok = True
    for url, name in files:
        path = dest / name
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
