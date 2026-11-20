"""Everyday skills (tools/everyday.py): timers and alarms, weather, maths, units, lists, music - and their instant routing."""
import asyncio
import json
import time

import httpx
import pytest

from conftest import run
from jarvis.llm.offline import match_rules
from jarvis.security import SecurityError
from jarvis.tools.everyday import (_unit, evaluate, is_math, music_url, parse_clock, parse_duration, say_duration, spoken_math,
                                   words_to_number)
from jarvis.tools.registry import ToolContext


@pytest.mark.parametrize("text, seconds", [("5 minutes", 300), ("1 hour 30 minutes", 5400), ("90 seconds", 90), ("half an hour", 1800),
                                           ("two and a half minutes", None), ("twenty five minutes", 1500), ("a minute", 60), ("1:30", 90),
                                           ("set a timer for 10 min", 600), ("nothing here", None)])
def test_durations(text, seconds):
    assert parse_duration(text) == seconds or (seconds is None and parse_duration(text) in (None, 150))


def test_spoken_numbers_and_durations_back_to_words():
    assert words_to_number("thirty five minutes") == "35 minutes"
    assert say_duration(5400) == "1 hour 30 minutes" and say_duration(61) == "1 minute 1 second"


def test_clock_times_are_the_next_occurrence():
    now = time.mktime((2026, 11, 20, 20, 0, 0, 0, 0, -1))  # 8 pm
    assert time.localtime(parse_clock("7:30 am", now))[:5] == (2026, 11, 21, 7, 30)
    assert time.localtime(parse_clock("9 pm", now))[3:5] == (21, 0)
    assert time.localtime(parse_clock("noon", now))[2:4] == (21, 12)
    assert parse_clock("25:00", now) is None


@pytest.mark.parametrize("text, value", [("what is 15% of 240", 36), ("23 times 47", 1081), ("2 to the power of 10", 1024),
                                         ("square root of 144", 12), ("100 divided by 8", 12.5), ("7 plus 5 minus 2", 10)])
def test_spoken_maths(text, value):
    assert is_math(text) and evaluate(spoken_math(text)) == value


def test_maths_is_safe_and_only_arithmetic():
    for bad in ("__import__('os').system('x')", "open('f')", "9**9**9", "a + 1"):
        with pytest.raises(Exception):
            evaluate(bad)
    assert not is_math("remind me in 20 minutes to call Sam") and not is_math("set brightness to 40%")


def test_units():
    assert _unit("km") == ("length", "km") and _unit("miles") == ("length", "mile") and _unit("°F") == ("temperature", "f")
    assert _unit("parsecs") is None


@pytest.fixture
def ctx(jarvis):
    return ToolContext(jarvis, agent="orchestrator", actor="test", conversation_id="c1")


def test_timers_alarms_and_cancel(jarvis, ctx):
    ex = jarvis.executor
    r = run(ex.call("set_timer", {"duration": "3 minutes", "label": "tea"}, ctx))
    assert r.ok and r.data["in"] == "3 minutes"
    run(ex.call("set_alarm", {"time_of_day": "7:30 am"}, ctx))
    left = run(ex.call("list_timers", {}, ctx)).data["timers"]
    assert [t["kind"] for t in left] == ["timer", "alarm"] and left[0]["label"] == "tea"
    assert run(ex.call("cancel_timer", {"which": "tea"}, ctx)).data["cancelled"] == ["tea"]
    assert run(ex.call("cancel_timer", {"which": "all"}, ctx)).data["remaining"] == 0
    assert not run(ex.call("set_timer", {"duration": "forever"}, ctx)).ok


def test_a_due_timer_rings_and_is_announced(home, tmp_path):
    from conftest import make

    j = make(home, tmp_path)
    spoken = []
    j.voice.say_aloud = lambda text, alert=False: spoken.append((text, alert)) or True
    j.db.set_setting("timers", [{"id": "tm_x", "kind": "timer", "label": "tea", "seconds": 180, "due": time.time() - 1, "created": time.time() - 181}])
    q = j.bus.subscribe()

    async def scenario():
        task = asyncio.create_task(j._timers_loop())
        await asyncio.sleep(0.2)
        task.cancel()

    run(scenario())
    assert spoken == [("Time's up! Your tea timer for 3 minutes is done.", True)] and j.db.get_setting("timers") == []
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    ann = [e for e in events if e["type"] == "assistant.announce"]
    assert ann and ann[0]["server_speaks"] is True  # the window must not say it a second time
    j.db.close()


def test_lists(jarvis, ctx):
    ex = jarvis.executor
    assert run(ex.call("list_add", {"items": "milk, eggs and bread"}, ctx)).data["added"] == ["milk", "eggs", "bread"]
    assert run(ex.call("list_add", {"items": "Milk"}, ctx)).data["added"] == []  # no duplicates
    assert run(ex.call("list_remove", {"item": "eggs"}, ctx)).data["items"] == ["milk", "bread"]
    assert run(ex.call("list_show", {"list_name": "groceries"}, ctx)).data["items"] == ["milk", "bread"]  # same list
    assert run(ex.call("list_clear", {}, ctx)).pending  # emptying a whole list asks first


def test_weather_uses_the_home_city(jarvis, ctx):
    def handler(req):
        if "geocoding" in req.url.host:
            assert req.url.params["name"] == "Chennai"
            return httpx.Response(200, json={"results": [{"name": "Chennai", "country": "India", "latitude": 13.08, "longitude": 80.27}]})
        return httpx.Response(200, json={"current": {"temperature_2m": 31.6, "apparent_temperature": 35.0, "relative_humidity_2m": 70, "weather_code": 2,
                                                     "wind_speed_10m": 12}, "daily": {"temperature_2m_max": [33, 32, 31], "temperature_2m_min": [25, 24, 24],
                                                                                    "precipitation_probability_max": [10, 60, 20], "weather_code": [2, 63, 3]}})
    jarvis.http_transport = httpx.MockTransport(handler)
    assert not run(jarvis.executor.call("weather", {}, ctx)).ok  # no city known yet: it asks which
    run(jarvis.executor.call("set_home_city", {"city": "chennai"}, ctx))
    w = run(jarvis.executor.call("weather", {"day": "tomorrow"}, ctx)).data
    assert w["place"] == "Chennai" and w["rain_chance"] == 60 and w["sky"] == "rain" and w["day"] == "tomorrow"


def test_music_opens_a_search_not_anything_else():
    assert music_url("shape of you") == "https://www.youtube.com/results?search_query=shape+of+you"
    with pytest.raises(SecurityError):
        music_url("   ")


@pytest.mark.parametrize("text, tool", [
    ("set a timer for 5 minutes", "set_timer"), ("set a tea timer for 3 minutes", "set_timer"), ("5 minute timer", "set_timer"),
    ("how much time is left", "list_timers"), ("cancel the tea timer", "cancel_timer"), ("cancel all timers", "cancel_timer"),
    ("set an alarm for 7:30 am", "set_alarm"), ("wake me up at 6", "set_alarm"),
    ("what's the weather", "weather"), ("what's the weather in Chennai tomorrow", "weather"), ("will it rain today", "weather"),
    ("my city is Chennai", "set_home_city"), ("what is 15% of 240", "calculate"), ("what's 23 times 47", "calculate"),
    ("convert 5 km to miles", "convert_units"), ("100 fahrenheit to celsius", "convert_units"), ("how many cm in 3 inches", "convert_units"),
    ("add milk and eggs to my shopping list", "list_add"), ("what's on my shopping list", "list_show"),
    ("remove milk from my shopping list", "list_remove"), ("clear my shopping list", "list_clear"), ("play shape of you", "play_music"),
    # older commands keep their routes
    ("what's on my to-do list", "list_dev_tasks"), ("play some music", "system_control"), ("pause the music", "system_control"),
    ("remind me in 20 minutes to call Sam", "add_dev_task"), ("set brightness to 40%", "system_control"), ("open notepad", "open_application"),
])
def test_everyday_routing(jarvis, text, tool):
    hit = match_rules(text, jarvis.offline.env)
    assert hit and hit[1][0] == tool, (text, hit and hit[1])


def test_questions_are_not_mistaken_for_commands(jarvis):
    for text in ("what is the capital of France?", "tell me about the weather in ancient Rome's literature please and its meaning in poems",
                 "what time is it"):
        hit = match_rules(text, jarvis.offline.env)
        assert not hit or hit[1][0] not in ("calculate", "convert_units", "set_timer"), (text, hit and hit[1])


def test_instant_replies_read_naturally(jarvis):
    r = run(jarvis.chat("add milk, eggs and bread to my shopping list", "c1"))
    assert r["text"] == "Added milk, eggs and bread to your shopping list."
    assert run(jarvis.chat("what is 15% of 240", "c1"))["text"] == "That's 36."
    assert run(jarvis.chat("convert 100 fahrenheit to celsius", "c1"))["text"] == "100 fahrenheit is 37.78 celsius."
    run(jarvis.chat("set a tea timer for 3 minutes", "c1"))
    run(jarvis.chat("set an alarm for 7:30 am", "c1"))
    text = run(jarvis.chat("how much time is left", "c1"))["text"]
    assert text.startswith("The tea timer has") and "your alarm is set for 7:30 AM" in text
    assert run(jarvis.chat("cancel all timers", "c1"))["text"] == "Cancelled the tea timer and the alarm."
