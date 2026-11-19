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


# -- language decision, English model, Tamil model ----------------------------------------------------------------------
class FakeWhisper:
    def __init__(self, name, language, probs, text):
        self.name, self.language, self.probs, self.text, self.calls = name, language, probs, text, []

    def detect_language(self, audio):
        return self.language, 0.9, list(self.probs.items())

    def transcribe(self, audio, language=None, **opts):
        self.calls.append((language, opts.get("initial_prompt")))
        seg = SimpleNamespace(text=self.text, no_speech_prob=0.05, avg_logprob=-0.2)
        info = SimpleNamespace(language=language or self.language, language_probability=0.9, all_language_probs=list(self.probs.items()))
        return iter([seg]), info


def _listener(tmp_path, detector, english, tamil):
    lst = LocalListener(tmp_path, lambda *a: None, lambda *a: None, model_size="small", fast_size="base", english_size="base.en")
    lst._whisper, lst._english, lst._accurate = detector, english, tamil
    lst._accurate_model = lambda: tamil
    lst.keep_samples = False
    lst._whisper_ready.set()
    return lst


def test_english_goes_to_the_english_model_with_jarvis_vocabulary(tmp_path):
    from jarvis.listen import VOCABULARY

    detector = FakeWhisper("base", "ms", {"ms": 0.5, "en": 0.3, "ta": 0.01}, "unused")  # accented English heard as Malay
    english = FakeWhisper("base.en", "en", {}, " Create a file named hi.txt on my Desktop.")
    tamil = FakeWhisper("small", "ta", {}, "unused")
    text, lang, info = _listener(tmp_path, detector, english, tamil).transcribe(np.zeros(16000, "float32"))
    assert (text, lang, info["model"]) == ("Create a file named hi.txt on my Desktop.", "en", "base.en")
    assert english.calls == [("en", VOCABULARY)] and detector.calls == [] and tamil.calls == []


def test_tamil_is_written_by_the_tamil_model(tmp_path):
    detector = FakeWhisper("base", "te", {"te": 0.4, "ta": 0.3, "en": 0.05}, "unused")  # Tamil heard as Telugu
    english = FakeWhisper("base.en", "en", {}, "unused")
    tamil = FakeWhisper("small", "ta", {}, "ஸ்ரீரங்கம் கோவிலில்")
    text, lang, info = _listener(tmp_path, detector, english, tamil).transcribe(np.zeros(16000, "float32"))
    assert (text, lang, info["model"]) == ("ஸ்ரீரங்கம் கோவிலில்", "ta", "small") and tamil.calls == [("ta", None)] and english.calls == []


def test_utterances_are_kept_locally_and_only_the_last_ones(tmp_path, monkeypatch):
    import jarvis.listen as listen

    monkeypatch.setattr(listen, "KEEP_SAMPLES", 3)
    lst = LocalListener(tmp_path / "models", lambda *a: None, lambda *a: None)
    lst.transcribe = lambda audio: ("open notepad", "en", {"seconds": 1.0, "model": "base.en"})
    import time as _t
    for i in range(5):
        monkeypatch.setattr(listen.time, "strftime", lambda fmt, i=i: f"2026-{i}-" + fmt.split("-")[-1])
        lst._transcribe(np.zeros(1600, "float32"))
    kept = sorted(p.name for p in (tmp_path / "voice-samples").glob("*.wav"))
    assert len(kept) == 3 and all((tmp_path / "voice-samples" / k.replace(".wav", ".txt")).read_text(encoding="utf-8").startswith("open notepad") for k in kept)
    lst.keep_samples = False
    lst._transcribe(np.zeros(1600, "float32"))
    assert len(list((tmp_path / "voice-samples").glob("*.wav"))) == 3


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


# -- fixes from the owner's 2026-11-20 session ----------------------------------------------------------------------------
class SlowLLM:
    """A model that takes a while, so a second message can arrive while it is still answering the first."""
    name, model = "openai_compat", "local"

    def __init__(self):
        self.calls = []

    async def complete(self, system, messages, tools, on_text=None, meta=None):
        self.calls.append(messages[-1]["content"])
        await asyncio.sleep(0.3)
        return LLMResponse(text=f"answer to: {messages[-1]['content'][-30:]}", tool_calls=[], provider=self.name, model=self.model)


def test_a_new_message_replaces_the_answer_still_running(home, tmp_path):
    from jarvis.llm.base import LLMRouter

    j = make(home, tmp_path)
    llm = SlowLLM()
    j.llm = LLMRouter([llm, j.offline], backoff=0.01)
    j.db.set_setting("fast_path", False)
    j.settings.translate_via_english = False

    async def scenario():
        first = asyncio.create_task(j.chat("create a tab opening google", "c1"))
        await asyncio.sleep(0.05)
        second = await j.chat("in my account", "c1")
        await asyncio.gather(first, return_exceptions=True)
        return second

    second = run(scenario())
    replies = [m["content"] for m in j.db.query("SELECT content FROM messages WHERE role='assistant' ORDER BY created")]
    assert replies[-1] == second["text"] and second["text"].startswith("answer to:")
    assert sum(1 for r in replies if r.startswith("answer to:")) == 1  # only one real answer; the first was interrupted
    assert "interrupted" in replies[0]
    j.db.close()


def test_promise_without_action_gets_one_nudge(home, tmp_path):
    from conftest import ScriptedLLM
    from jarvis.llm.base import LLMRouter

    llm = ScriptedLLM([("I can create a file named html.html directly. Let me do that now.", []),
                       ("", [("write_file", {"path": str(home / "html.html"), "content": "<html></html>"})]),
                       ("Creating html.html awaits your approval.", [])])
    llm.name = "openai_compat"
    j = make(home, tmp_path)
    j.llm = LLMRouter([llm, j.offline], backoff=0.01)
    j.db.set_setting("fast_path", False)
    j.settings.translate_via_english = False
    r = run(j.chat("I want you to create a file named html.html", "c1"))
    assert [t["tool"] for t in r["tools"]] == ["write_file"] and r["pending_approvals"]
    assert "called no tool" in llm.seen[1]["messages"][-1]["content"]
    j.db.close()


def test_the_model_knows_where_the_desktop_is(jarvis):
    from jarvis.agents import desktop_folder, flat_system

    assert f"Desktop: {desktop_folder(jarvis.settings.allowed_roots[0])}" in flat_system(jarvis)


def test_app_names_and_web_addresses():
    import pytest as _pt

    from jarvis.security import SecurityError
    from jarvis.tools.system import clean_app_name, normalize_url

    assert clean_app_name("claude which is already open !") == "claude"
    assert clean_app_name("the notepad app") == "notepad" and clean_app_name("google chrome please") == "google chrome"
    assert normalize_url("google.com") == "https://google.com"
    for bad in ("javascript:alert(1)", "file:///C:/x", "data:text/html,hi", "not a url"):
        with _pt.raises(SecurityError):
            normalize_url(bad)


def test_no_access_excuse_gets_sent_to_the_web(home, tmp_path):
    """"I don't have access to specific portals like TCS DCS" - said by the local model instead of searching (2026-11-20)."""
    from conftest import ScriptedLLM
    from jarvis.agents import NO_ACCESS
    from jarvis.llm.base import LLMRouter

    assert NO_ACCESS.search("I don't have access to specific portals or internal websites like TCS DCS.")
    assert NO_ACCESS.search("I don't have direct access to specific corporate portals or internal systems like TCS DCS.")
    assert not NO_ACCESS.search("The portal gives employees access to payslips.")
    llm = ScriptedLLM([("I don't have access to specific portals like Tata Consultancy Services DCS.", []),
                       ("", [("web_search", {"query": "Tata Consultancy Services DCS portal"})]),
                       ("DCS is TCS's portal for ...", [])])
    llm.name = "openai_compat"
    j = make(home, tmp_path)
    j.llm = LLMRouter([llm, j.offline], backoff=0.01)
    j.db.set_setting("fast_path", False)
    j.settings.translate_via_english = False
    import httpx

    j.http_transport = httpx.MockTransport(lambda req: httpx.Response(200, text="<html></html>"))  # no real network in tests
    r = run(j.chat("Can you tell me the portal called Tata Consultancy Services DCS?", "c1"))
    assert "Call web_search" in llm.seen[1]["messages"][-1]["content"]
    assert [t["tool"] for t in r["tools"]] == ["web_search"]
    j.db.close()
