"""Spoken replies from the server (speak.py) and the fast/accurate speech model pair (listen.py), without audio."""
import asyncio
from types import SimpleNamespace

import numpy as np

from conftest import make, run
from jarvis.listen import LocalListener, VoiceService
from jarvis.llm.base import LLMResponse
from jarvis.speak import SentenceSplitter, language_of_text, speakable


def test_speakable_and_sentences():
    assert speakable("**Done.** See [the page](https://x.io) or https://y.io\n```py\nprint(1)\n```") == "Done. See the page or link code block omitted."
    s = SentenceSplitter()
    assert s.feed("It's 1:41") == [] and s.feed(" AM. How can") == ["It's 1:41 AM."] and s.rest() == " How can"
    s = SentenceSplitter()
    assert s.feed("Here:\n```\na. b. c.") == []  # never inside an unfinished code block
    assert language_of_text("இந்தியாவின் தலைநகரம் புது தில்லி.") == "ta" and language_of_text("New Delhi.") == "en"


class FakeSpeaker:
    def __init__(self, langs=("en",)):
        self.langs, self.said, self.rate, self.stopped = set(langs), [], 1.0, 0

    def can_speak(self, lang):
        return lang in self.langs

    def say(self, text, lang=None):
        lang = lang or language_of_text(text)
        if lang not in self.langs:
            return False
        self.said.append(text.strip())
        return True

    def stop(self):
        self.stopped += 1

    def chime(self):
        self.chimes = getattr(self, "chimes", 0) + 1

    def voices(self):
        return {lang: "fake" for lang in self.langs}


class StreamingLLM:
    """A local model that streams its reply in small pieces."""
    name, model = "openai_compat", "local"

    def __init__(self, reply):
        self.reply = reply

    async def complete(self, system, messages, tools, on_text=None, meta=None):
        for piece in [self.reply[i:i + 7] for i in range(0, len(self.reply), 7)]:
            if on_text:
                on_text(piece)
            await asyncio.sleep(0)
        return LLMResponse(text=self.reply, tool_calls=[], provider=self.name, model=self.model)


def _voice_app(home, tmp_path, reply):
    from jarvis.llm.base import LLMRouter

    j = make(home, tmp_path)
    j.llm = LLMRouter([StreamingLLM(reply), j.offline], backoff=0.01)
    j.db.set_setting("fast_path", False)
    j.settings.translate_via_english = False
    return j


def test_server_speaks_the_reply_sentence_by_sentence_while_it_streams(home, tmp_path):
    j = _voice_app(home, tmp_path, "Paris is the capital of France. It is on the Seine. Anything else?")
    speaker = FakeSpeaker()
    svc = VoiceService(j, speaker=speaker)
    q = j.bus.subscribe()
    r = run(svc.handle("What is the capital of France?", "en"))
    assert r["text"].startswith("Paris")
    assert speaker.said == ["Paris is the capital of France.", "It is on the Seine.", "Anything else?"]
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    heard = [e for e in events if e["type"] == "voice.heard"]
    assert heard and heard[0]["server_speaks"] is True  # the window must not speak it as well
    assert not [e for e in events if e["type"] == "voice.say"]
    j.db.close()


def test_without_a_voice_for_the_language_the_window_says_it(home, tmp_path):
    j = _voice_app(home, tmp_path, "இந்தியாவின் தலைநகரம் புது தில்லி.")
    speaker = FakeSpeaker(langs=("en",))  # like the owner's laptop: no Tamil voice installed in Windows
    svc = VoiceService(j, speaker=speaker)
    q = j.bus.subscribe()
    run(svc.handle("இந்தியாவின் தலைநகரம் எது?", "ta"))
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert [e for e in events if e["type"] == "voice.heard"][0]["server_speaks"] is False
    assert speaker.said == []  # nothing half-spoken by the server; the page speaks the streamed reply as before
    j.db.close()


def test_replies_are_not_spoken_when_the_user_turned_speaking_off(home, tmp_path):
    j = _voice_app(home, tmp_path, "Done.")
    j.db.set_setting("voice", {"speak_replies": False})
    speaker = FakeSpeaker()
    run(VoiceService(j, speaker=speaker).handle("hello there", "en"))
    assert speaker.said == []
    j.db.close()


def test_wake_word_or_hotkey_stops_jarvis_talking(home, tmp_path):
    j = _voice_app(home, tmp_path, "x")
    speaker = FakeSpeaker()
    svc = VoiceService(j, speaker=speaker)
    svc._event("voice.wake", {"trigger": "wake word"})
    svc.listener = SimpleNamespace(running=lambda: True, listen_now=lambda: None)
    assert svc.listen_now() and speaker.stopped == 2 and speaker.chimes == 2  # heard: stop talking, chime
    j.db.close()


# -- fast model for English, accurate model for Tamil -------------------------------------------------------------------
class FakeWhisper:
    def __init__(self, name, language, probs, text):
        self.name, self.language, self.probs, self.text, self.calls = name, language, probs, text, []

    def transcribe(self, audio, language=None, **opts):
        self.calls.append(language)
        seg = SimpleNamespace(text=self.text, no_speech_prob=0.05, avg_logprob=-0.2)
        info = SimpleNamespace(language=language or self.language, language_probability=0.9, all_language_probs=list(self.probs.items()))
        return iter([seg]), info


def _listener(tmp_path, fast, accurate):
    lst = LocalListener(tmp_path, lambda *a: None, lambda *a: None, model_size="small", fast_size="base")
    lst._whisper, lst._accurate = fast, accurate
    lst._whisper_ready.set()
    return lst


def test_english_uses_only_the_fast_model(tmp_path):
    fast = FakeWhisper("base", "en", {"en": 0.95, "ta": 0.01}, " What time is it?")
    accurate = FakeWhisper("small", "en", {}, "should not be used")
    text, lang, info = _listener(tmp_path, fast, accurate).transcribe(np.zeros(16000, "float32"))
    assert (text, lang, info["model"]) == ("What time is it?", "en", "base") and accurate.calls == []


def test_tamil_is_written_by_the_accurate_model(tmp_path):
    fast = FakeWhisper("base", "te", {"te": 0.4, "ta": 0.3, "en": 0.05}, "Sheerangam Kovilil")  # base's Tamil text is poor
    accurate = FakeWhisper("small", "ta", {}, "ஸ்ரீரங்கம் கோவிலில்")
    lst = _listener(tmp_path, fast, accurate)
    lst._accurate_model = lambda: accurate
    text, lang, info = lst.transcribe(np.zeros(16000, "float32"))
    assert (text, lang, info["model"]) == ("ஸ்ரீரங்கம் கோவிலில்", "ta", "small") and accurate.calls == ["ta"]


def test_a_request_heard_too_slowly_is_not_acted_on(tmp_path, monkeypatch):
    """On the laptop one transcription took 912 s while the CPU was full; acting on it then would surprise the user."""
    import jarvis.listen as listen

    events, done = [], []
    lst = LocalListener(tmp_path, lambda t, d: events.append((t, d)), lambda *a: done.append(a), model_size="base", fast_size="base")
    lst.transcribe = lambda audio: ("delete my notes", "en", {"seconds": 25.0, "model": "base"})
    lst._transcribe(np.zeros(16000, "float32"))
    assert done == [] and events[-1][0] == "voice.slow" and "too slow" in lst.last["note"]
    lst.transcribe = lambda audio: ("what time is it", "en", {"seconds": 2.0, "model": "base"})
    lst._transcribe(np.zeros(16000, "float32"))
    assert done and done[0][0] == "what time is it"
    assert listen.STALE_AFTER == 20.0


def test_slow_request_is_apologised_for_out_loud(home, tmp_path):
    j = make(home, tmp_path)
    speaker = FakeSpeaker()
    VoiceService(j, speaker=speaker)._event("voice.slow", {"text": "x", "seconds": 30})
    assert speaker.said == ["Sorry, I was too slow to catch that. Please say it again."]
    j.db.close()
