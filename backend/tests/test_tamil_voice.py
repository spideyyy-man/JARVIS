"""Tamil works the way English does (2026-11-20): JARVIS says Tamil replies itself (offline Tamil voice), "Jarvis,
<Tamil request>" in one breath, Tamil follow-ups, unclear Tamil, Tamil conversation ends, and instant Tamil everyday
commands with Tamil replies."""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import make, run
from jarvis.listen import CONVERSATION_ENDS, LocalListener, VoiceService, strip_tamil_wake
from jarvis.llm.base import ToolCall
from jarvis.llm.offline import Env, OfflineProvider, match_rules
from jarvis.speak import MmsTamilEngine, make_engines, tamil_number, tamil_speakable
from jarvis.tools.everyday import due_message, parse_clock, parse_duration, ta_clock, ta_duration
from test_speak_fast_voice import FakeListener, FakeSpeaker, _voice_app

ENV = Env(Path("/home/u"), lambda: "/proj", lambda: [])

TAMIL_EVERYDAY = [
    ("5 நிமிடம் டைமர் வை", "set_timer", {"duration": "5 minutes", "language": "ta"}),
    ("ஐந்து நிமிஷத்துக்கு டைமர் வை", "set_timer", {"duration": "5 minutes"}),
    ("அரை மணி நேரம் டைமர்", "set_timer", {"duration": "30 minutes"}),
    ("டைமர் 1 மணி 30 நிமிடம்", "set_timer", {"duration": "1 hours 30 minutes"}),
    ("டைமரை ரத்து செய்", "cancel_timer", {"which": "timer"}),
    ("எல்லா அலாரங்களையும் நிறுத்து", "cancel_timer", {"which": "all"}),
    ("டைமர்ல எவ்வளவு நேரம் இருக்கு", "list_timers", {}),
    ("காலை 6 மணிக்கு அலாரம் வை", "set_alarm", {"time_of_day": "6:00 am", "language": "ta"}),
    ("இரவு 9:30க்கு அலாரம் வை", "set_alarm", {"time_of_day": "9:30 pm"}),
    ("நாளை காலை ஏழு மணிக்கு எழுப்பு", "set_alarm", {"time_of_day": "7:00 am"}),
    ("சென்னையில் வானிலை எப்படி", "weather", {"place": "Chennai", "day": "today"}),
    ("நாளைக்கு மழை வருமா", "weather", {"place": "", "day": "tomorrow"}),
    ("கோவையில் வெப்பநிலை என்ன", "weather", {"place": "Coimbatore"}),
    ("என் ஊர் மதுரை", "set_home_city", {"city": "Madurai"}),
    ("25 கூட்டல் 17 எவ்வளவு", "calculate", {"expression": "25 plus 17"}),
    ("240 இல் 15 சதவீதம் என்ன", "calculate", {"expression": "15 percent of 240"}),
    ("12 பெருக்கல் 8", "calculate", {"expression": "12 times 8"}),
    ("ஷாப்பிங் லிஸ்ட்ல பால் சேர்", "list_add", {"items": "பால்", "list_name": "shopping"}),
    ("பால் மற்றும் முட்டை லிஸ்ட்ல சேர்த்து விடு", "list_add", {"items": "பால், முட்டை"}),
    ("ஷாப்பிங் லிஸ்ட்ல என்ன இருக்கு", "list_show", {}),
    ("லிஸ்ட்ல இருந்து பால் நீக்கு", "list_remove", {"item": "பால்"}),
    ("இளையராஜா பாட்டு போடு", "play_music", {"query": "இளையராஜா பாட்டு"}),
    # older Tamil rules keep their phrases
    ("அடுத்த பாட்டு போடு", "system_control", {"action": "media_next"}),
    ("சென்னை வானிலை பற்றி இணையத்தில் தேடு", "web_search", {}),
    ("சத்தத்தை அதிகமாக்கு", "system_control", {"action": "volume_up"}),
    ("10 நிமிடத்தில் மீட்டிங் என்று நினைவூட்டு", "add_dev_task", {"due_in_minutes": 10}),
]


@pytest.mark.parametrize("phrase,tool,args", TAMIL_EVERYDAY)
def test_tamil_everyday_command_routes_instantly(phrase, tool, args):
    hit = match_rules(phrase, ENV)
    assert hit is not None, f"no rule matched: {phrase}"
    assert hit[1][0] == tool, f"{phrase!r} routed to {hit[1][0]} {hit[1][1]}"
    assert args.items() <= hit[1][1].items(), hit[1][1]


@pytest.mark.parametrize("phrase", ["set a timer for 5 minutes", "what's the weather in Chennai", "add milk to my shopping list", "25 plus 17"])
def test_tamil_everyday_rules_never_take_english(phrase):
    hit = match_rules(phrase, ENV)
    assert hit and "language" not in hit[1][1] and "டைமர" not in hit[0].pattern.pattern and "வானிலை" not in hit[0].pattern.pattern


def test_tamil_durations_and_clock_times_parse():
    assert ta_duration("பத்து நிமிடம்") == "10 minutes" and parse_duration(ta_duration("பத்து நிமிடம்")) == 600
    assert parse_duration(ta_duration("ஒன்றரை மணி நேரம்")) == 5400 and parse_duration(ta_duration("45 வினாடி")) == 45
    assert ta_duration("டைமர் வை") is None
    assert ta_clock("மதியம் 2 மணிக்கு") == "2:00 pm" and ta_clock("இரவு 12 மணிக்கு") == "12:00 am" and ta_clock("7 மணி 15 நிமிடத்துக்கு") == "7:15"
    assert ta_clock("அலாரம் வை") is None and parse_clock(ta_clock("காலை 6 மணிக்கு")) is not None


def _after(question, tool, args, result):
    turn = [{"role": "user", "content": question}, {"role": "assistant", "content": "", "tool_calls": [ToolCall("c1", tool, args)]},
            {"role": "tool", "results": [{"id": "c1", "name": tool, "content": json.dumps(result), "is_error": False}]}]
    return run(OfflineProvider(ENV).complete("", turn, [{"name": tool}], meta={"agent": "orchestrator"})).text


def test_tamil_everyday_replies_are_in_tamil():
    assert _after("5 நிமிடம் டைமர் வை", "set_timer", {}, {"seconds": 300, "due": time.time() + 300, "kind": "timer", "label": "", "in": "5 minutes"}) == "சரி, 5 நிமிடம் டைமர் வைத்தேன்."
    w = _after("சென்னையில் வானிலை எப்படி", "weather", {}, {"place": "Chennai", "day": "today", "now": {"temp": 31.2, "sky": "light rain", "feels": 35},
                                                       "low": 26, "high": 33, "rain_chance": 60, "sky": "rain"})
    assert w == "சென்னை: இப்போது 31°, லேசான மழை. இன்று 26° முதல் 33° வரை, மழைக்கு 60% வாய்ப்பு."
    assert _after("25 கூட்டல் 17 எவ்வளவு", "calculate", {}, {"expression": "25+17", "result": "42"}) == "விடை 42."
    assert _after("ஷாப்பிங் லிஸ்ட்ல பால் சேர்", "list_add", {}, {"list": "shopping", "added": ["பால்"], "items": ["பால்"]}) == "பால் உங்கள் ஷாப்பிங் பட்டியலில் சேர்த்தேன்."
    assert _after("டைமரை ரத்து செய்", "cancel_timer", {}, {"cancelled": [], "remaining": 0}).startswith("ரத்து செய்ய")


def test_a_timer_set_in_tamil_rings_in_tamil():
    assert due_message({"kind": "timer", "label": "", "seconds": 300, "due": time.time(), "language": "ta"}) == "நேரம் முடிந்தது! உங்கள் 5 நிமிடம் டைமர் முடிந்தது."
    assert due_message({"kind": "alarm", "label": "", "due": time.time(), "language": "ta"}).startswith("இப்போது ")
    assert due_message({"kind": "timer", "label": "", "seconds": 300, "due": time.time()}).startswith("Time's up!")


def test_tamil_everyday_command_end_to_end_is_instant_and_in_tamil(home, tmp_path):
    j = make(home, tmp_path)
    r = run(j.chat("3 நிமிடம் டைமர் வை"))
    assert r["text"] == "சரி, 3 நிமிடம் டைமர் வைத்தேன்." and j.db.get_setting("timers")[0]["language"] == "ta"
    j.db.close()


# -- the Tamil voice --------------------------------------------------------------------------------------------------------
def test_numbers_become_tamil_words_for_the_tamil_voice():
    assert [tamil_number(n) for n in (7, 15, 25, 100, 150, 1500, 100000)] == [
        "ஏழு", "பதினைந்து", "இருபத்தி ஐந்து", "நூறு", "நூற்றி ஐம்பது", "ஆயிரத்தி ஐந்நூறு", "ஒரு லட்சம்"]
    assert tamil_speakable("இப்போது 5:10.") == "இப்போது ஐந்து மணி பத்து நிமிடம்."
    assert tamil_speakable("31°, மழைக்கு 40% வாய்ப்பு") == "முப்பத்தி ஒன்று டிகிரி, மழைக்கு நாற்பது சதவீதம் வாய்ப்பு"
    assert tamil_speakable("3.5 கிலோ, 1,500 ரூபாய்") == "மூன்று புள்ளி ஐந்து கிலோ, ஆயிரத்தி ஐந்நூறு ரூபாய்"


def test_tamil_voice_is_used_when_installed_and_says_english_words_with_the_english_voice(tmp_path):
    folder = tmp_path / "tts" / "mms-tam"
    folder.mkdir(parents=True)
    (folder / "model.onnx").write_bytes(b"x")
    (folder / "tokens.txt").write_text("_ 0\nவ 1\nண 2\nக 3\n் 4\nம 5\n  6\n", encoding="utf-8")
    engines = make_engines("piper", tmp_path)
    tamil = next(e for e in engines if e.name == "mms-ta")
    assert engines[0] is tamil or engines[0].name != "windows"  # the Tamil voice comes before Windows' voices
    assert tamil.ids("வணக்கம்") == [0, 1, 0, 2, 0, 3, 0, 4, 0, 3, 0, 5, 0, 4, 0]  # வ ண க ் க ம ் with a blank between

    class English:
        langs = ("en",)

        def wav(self, text, lang, rate):
            self.said = text
            import io
            import wave
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(22050), w.writeframes(b"\x00\x00" * 2205)
            return buf.getvalue()
    eng = MmsTamilEngine(folder, English())
    eng._tamil = lambda text, rate: np.ones(1600, np.float32) * 0.1  # no real model in a test
    import io
    import wave
    with wave.open(io.BytesIO(eng.wav("Chrome திறக்கிறேன்.", "ta", 1.0))) as w:
        assert w.getframerate() == 16000 and w.getnframes() > 1600
    assert eng.english.said == "Chrome"


# -- listening in Tamil -------------------------------------------------------------------------------------------------------
def test_tamil_wake_word_is_removed_however_it_is_written():
    for text in ("ஜார்விஸ், நேரம் என்ன?", "ஜாவிஸ் நேரம் என்ன?", "சார்விஸ் நேரம் என்ன?", "Jarvis, நேரம் என்ன?", "ஹே ஜார்வீஸ் நேரம் என்ன?"):
        assert strip_tamil_wake(text) == "நேரம் என்ன?", text
    assert strip_tamil_wake("சரி நன்றி") == "சரி நன்றி" and strip_tamil_wake("வணக்கம்") == "வணக்கம்"


@pytest.mark.parametrize("text", ["நன்றி", "சரி நன்றி", "போதும்", "நிறுத்து", "அவ்வளவுதான் ஜார்விஸ்", "thanks"])
def test_tamil_words_end_the_conversation(text):
    assert CONVERSATION_ENDS.match(text)


def test_tamil_questions_keep_the_conversation_going():
    assert not CONVERSATION_ENDS.match("நன்றி சொல்லும் கவிதை எழுது") and not CONVERSATION_ENDS.match("நேரம் என்ன")


class Model:
    """A speech model returning fixed text and confidence; records the languages it was asked for."""
    def __init__(self, text, lp, langs=None):
        self.text, self.lp, self.langs, self.calls = text, lp, langs or {}, []

    def detect_language(self, audio):
        return max(self.langs, key=self.langs.get) if self.langs else "en", 0.9, list(self.langs.items())

    def transcribe(self, audio, language=None, **opts):
        self.calls.append(language)
        text = self.text[language] if isinstance(self.text, dict) else self.text
        lp = self.lp[language] if isinstance(self.lp, dict) else self.lp
        return iter([SimpleNamespace(text=text, no_speech_prob=0.02, avg_logprob=lp)]), None


def _lst(tmp_path, detector, english, tamil, events=None, done=None):
    lst = LocalListener(tmp_path, (lambda t, d: events.append((t, d))) if events is not None else (lambda *a: None),
                        (lambda *a: done.append(a)) if done is not None else (lambda *a: None), model_size="small", fast_size="base", english_size="base.en")
    lst._whisper, lst._english, lst._accurate = detector, english, tamil
    lst._accurate_model = lambda: tamil
    lst.keep_samples = False
    lst._whisper_ready.set()
    return lst


def test_jarvis_then_a_tamil_request_in_one_breath(tmp_path):
    """The English model is never sure of Tamil after "Jarvis" (every Tamil clip < -0.6): ask which language, and let the
    Tamil model write the request."""
    detector = Model("", -0.3, {"ta": 0.8, "en": 0.1})
    english = Model("Jarvis, epo do neram enna?", -1.1)
    tamil = Model("ஜார்விஸ் இப்போது நேரம் என்ன?", -0.25)
    rest, info = _lst(tmp_path, detector, english, tamil)._verify_wake(np.zeros(16000, "float32"))
    assert rest == "இப்போது நேரம் என்ன?" and info["language"] == "ta" and tamil.calls == ["ta"]


def test_jarvis_then_tamil_when_the_english_model_does_not_even_write_jarvis(tmp_path):
    """Real recordings: "Jarvis, ஸ்ரீரங்கம் கோவிலில்..." came out of the English model as "She's a young comedian..." - the start
    alone still says "Jarvis", and the Tamil model's own spelling of it (ஜால்பேச்) is dropped."""
    class English(Model):
        def transcribe(self, audio, language=None, **opts):
            text = "Jarvis." if len(audio) <= 1.6 * 16000 else "She's a young comedian, very good in the marathon."
            return iter([SimpleNamespace(text=text, no_speech_prob=0.03, avg_logprob=-1.09)]), None
    detector = Model("", -0.3, {"ta": 0.16, "en": 0.05})
    tamil = Model("ஜால்பேச் சிரங்கம் கோவிலில் விடிகுண்டு மிரட்டல்", -0.42)
    rest, info = _lst(tmp_path, detector, English("", 0), tamil)._verify_wake(np.zeros(16000 * 6, "float32"))
    assert rest == "சிரங்கம் கோவிலில் விடிகுண்டு மிரட்டல்" and info["language"] == "ta"


def test_unsure_speech_that_does_not_start_with_jarvis_never_reaches_the_tamil_model(tmp_path):
    """Tamil chatter in the room: the start is not "Jarvis", so the slow Tamil model is not run."""
    detector = Model("", -0.3, {"ta": 0.9})
    tamil = Model("unused", -0.2)
    assert _lst(tmp_path, detector, Model("Jumpers, before this", -1.1), tamil)._verify_wake(np.zeros(16000 * 4, "float32")) is None
    assert tamil.calls == []


def test_a_clear_english_one_breath_request_skips_the_tamil_check(tmp_path):
    detector = Model("", -0.3, {"ta": 0.9})
    detector.detect_language = lambda audio: (_ for _ in ()).throw(AssertionError("no language check for clear English"))
    tamil = Model("unused", -0.2)
    rest, info = _lst(tmp_path, detector, Model("Jarvis, what time is it?", -0.15), tamil)._verify_wake(np.zeros(16000, "float32"))
    assert rest == "what time is it?" and info["language"] == "en" and tamil.calls == []


def test_unclear_tamil_after_jarvis_is_asked_for_again(tmp_path):
    detector = Model("", -0.3, {"ta": 0.8, "en": 0.1})
    rest, info = _lst(tmp_path, detector, Model("Jarvis, mumble", -1.2), Model("ஜார்விஸ் கவல்", -0.9))._verify_wake(np.zeros(16000, "float32"))
    assert rest == "" and info.get("unclear")  # the wake word counts: chime and listen for the request


def test_unsure_tamil_that_was_really_english_is_rescued_by_the_english_model(tmp_path):
    detector = Model("", -0.3, {"ta": 0.6, "en": 0.4})
    text, lang, info = _lst(tmp_path, detector, Model(" Increase the font.", -0.1), Model("குத்துக்கொண்டு", -0.7)).transcribe(np.zeros(16000, "float32"))
    assert (text, lang, info["model"]) == ("Increase the font.", "en", "base.en")


def test_tamil_misjudged_as_english_gets_a_tamil_second_opinion(tmp_path):
    """Otherwise the bigger model translates it: "இப்போது நேரம் என்ன?" came back as "What is time now?"."""
    detector = Model("", -0.3, {"en": 0.6, "ta": 0.3})
    tamil = Model({"ta": "இப்போது நேரம் என்ன?", "en": "What is time now?"}, {"ta": -0.1, "en": -0.84})
    text, lang, info = _lst(tmp_path, detector, Model(" Epo do neram?", -1.0), tamil).transcribe(np.zeros(16000, "float32"))
    assert (text, lang) == ("இப்போது நேரம் என்ன?", "ta")


def test_unsure_english_with_no_sign_of_tamil_is_not_sent_to_the_tamil_model(tmp_path):
    """The owner's "Follow me" (Tamil share 0.07) must not become "அமாமா"."""
    detector = Model("", -0.3, {"en": 0.93, "ta": 0.07})
    tamil = Model({"ta": "அமாமா", "en": "Ah, mama"}, {"ta": -0.46, "en": -0.7})
    text, lang, _ = _lst(tmp_path, detector, Model(" Follow me.", -0.65), tamil).transcribe(np.zeros(16000, "float32"))
    assert (text, lang) == ("Follow me.", "en") and "ta" not in tamil.calls


def test_garbled_speech_heard_as_tamil_is_not_acted_on(tmp_path):
    """The owner's '...link in the description below' was heard as 88% Tamil: Tamil -0.66, English -1.11."""
    events, done = [], []
    detector = Model("", -0.3, {"ta": 0.88, "en": 0.12})
    lst = _lst(tmp_path, detector, Model("And if I can un-aid one", -1.11), Model("இந்த வணக்கம் நடைத்துக்கொண்டு வா.", -0.66), events, done)
    lst._transcribe(np.zeros(16000, "float32"))
    unclear = [d for t, d in events if t == "voice.unclear"]
    assert done == [] and unclear and unclear[0]["language"] == "en"  # first request, unsure language: apologise in English


def test_clear_tamil_is_acted_on(tmp_path):
    done = []
    detector = Model("", -0.3, {"ta": 0.97, "en": 0.03})
    lst = _lst(tmp_path, detector, Model("unused", -1.0), Model("நான் தமிழ்ல பேசுறேன் உங்களுக்குப் புரியுமா?", -0.29), done=done)
    lst._transcribe(np.zeros(16000, "float32"))
    assert done and done[0][:2] == ("நான் தமிழ்ல பேசுறேன் உங்களுக்குப் புரியுமா?", "ta")


def test_a_tamil_follow_up_still_checks_the_language(tmp_path):
    """A Tamil speaker often answers "yes" / "okay" in English: Tamil follow-ups keep the language check."""
    done = []
    detector = Model("", -0.3, {"en": 0.9, "ta": 0.1})
    lst = _lst(tmp_path, detector, Model(" Yes.", -0.1), Model("யெஸ்", -0.3), done=done)
    lst._last_lang, lst._trigger = "ta", "follow-up"
    lst._transcribe(np.zeros(16000, "float32"))
    assert done[0][:2] == ("Yes.", "en")


# -- JARVIS speaking Tamil ----------------------------------------------------------------------------------------------------
def test_tamil_replies_are_spoken_by_jarvis_and_open_the_conversation(home, tmp_path):
    j = _voice_app(home, tmp_path, "இந்தியாவின் தலைநகரம் புது தில்லி. வேறு ஏதாவது?")
    speaker, listener = FakeSpeaker(langs=("en", "ta")), FakeListener()
    svc = VoiceService(j, speaker=speaker)
    svc.listener = listener
    q = j.bus.subscribe()
    run(svc.handle("இந்தியாவின் தலைநகரம் எது?", "ta"))
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert [e for e in events if e["type"] == "voice.heard"][0]["server_speaks"] is True
    assert speaker.said == ["இந்தியாவின் தலைநகரம் புது தில்லி.", "வேறு ஏதாவது?"] and listener.follow_ups == [5.0]
    run(svc.handle("நன்றி", "ta"))
    assert listener.follow_ups == [5.0]  # "thanks" in Tamil ends the conversation too
    j.db.close()


def test_sorry_is_said_in_tamil_when_the_conversation_is_in_tamil(home, tmp_path):
    j = make(home, tmp_path)
    speaker = FakeSpeaker(langs=("en", "ta"))
    svc = VoiceService(j, speaker=speaker)
    svc.listener = FakeListener()
    svc._event("voice.unclear", {"guess": "x", "language": "ta"})
    svc._event("voice.slow", {"text": "x", "seconds": 30, "language": "ta"})
    assert speaker.said[0].startswith("மன்னிக்கவும், சரியாகக் கேட்கவில்லை") and speaker.said[1].startswith("மன்னிக்கவும்") and svc._follow_pending
    english_only = FakeSpeaker(langs=("en",))
    VoiceService(j, speaker=english_only)._event("voice.unclear", {"guess": "x", "language": "ta"})
    assert english_only.said == ["Sorry, I didn't catch that. Please say it again."]  # no Tamil voice: say it in English
    j.db.close()
