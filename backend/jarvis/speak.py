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


class Speaker:
    """One worker thread speaks queued text in order; `stop()` silences it at once (barge-in)."""

    def __init__(self, on_state: Callable[[bool], None] | None = None):
        self.on_state = on_state or (lambda speaking: None)
        self.rate = 1.0
        self.speaking = False
        self._queue: queue.Queue = queue.Queue()
        self._gen = 0
        self._voices: dict[str, object] | None = None
        self._thread: threading.Thread | None = None
        self._error = ""

    # -- voices ----------------------------------------------------------------------------------------------------
    @staticmethod
    def supported() -> bool:
        if os.name != "nt":
            return False
        import importlib.util

        return importlib.util.find_spec("winrt.windows.media.speechsynthesis") is not None

    def _load_voices(self) -> dict[str, object]:
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

    def voices(self) -> dict[str, str]:
        try:
            return {lang: v.display_name for lang, v in self._load_voices().items()}
        except Exception as e:  # noqa: BLE001
            self._error = str(e)
            return {}

    def can_speak(self, lang: str) -> bool:
        return self.supported() and lang in self.voices()

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
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            import winsound

            winsound.PlaySound(None, 0)
        except Exception:  # noqa: BLE001
            pass

    def _ensure_thread(self) -> None:
        if not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-speaker")
            self._thread.start()

    def _set(self, speaking: bool) -> None:
        if speaking != self.speaking:
            self.speaking = speaking
            try:
                self.on_state(speaking)
            except Exception:  # noqa: BLE001
                pass

    def _run(self) -> None:
        import winsound

        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        from winrt.windows.storage.streams import DataReader

        loop = asyncio.new_event_loop()
        synth = SpeechSynthesizer()

        async def to_wav(text: str, lang: str) -> bytes:
            synth.voice = self._load_voices()[lang]
            synth.options.speaking_rate = max(0.5, min(3.0, float(self.rate or 1)))
            stream = await synth.synthesize_text_to_stream_async(text)
            reader = DataReader(stream.get_input_stream_at(0))
            await reader.load_async(stream.size)
            buf = bytearray(stream.size)
            reader.read_bytes(buf)
            return bytes(buf)

        while True:
            try:
                gen, lang, text = self._queue.get(timeout=0.3)
            except queue.Empty:
                self._set(False)
                continue
            if gen != self._gen:
                continue
            try:
                audio = loop.run_until_complete(to_wav(text, lang))
                if gen != self._gen:
                    continue
                fd, path = tempfile.mkstemp(prefix="jarvis-say-", suffix=".wav")
                with os.fdopen(fd, "wb") as f:
                    f.write(audio)
                self._set(True)
                try:
                    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)  # blocks; stop() cuts it
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            except Exception as e:  # noqa: BLE001 - one bad sentence must not silence the rest
                self._error = f"{type(e).__name__}: {e}"
            if self._queue.empty():
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
