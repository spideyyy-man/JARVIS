"""Everyday assistant skills: timers and alarms, weather, maths and unit conversion, lists, playing music.

These are what people ask a voice assistant for most, and none of them existed (docs/ASSESSMENT.md, 2026-11-20). All
are deterministic and offline except the weather (Open-Meteo: free, no key, only the city name leaves the computer),
so the rule engine answers them instantly without the language model.
"""
from __future__ import annotations

import ast
import math
import operator
import re
import time
import uuid
from urllib.parse import quote_plus

from ..security import Level, SecurityError
from .registry import Tool, ToolContext, ToolRegistry

# -- spoken numbers and durations ----------------------------------------------------------------------------------------
_UNITS = {"zero": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
          "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def words_to_number(text: str) -> str:
    """'twenty five minutes' -> '25 minutes'; 'half an hour' -> '30 minutes'. Leaves digits alone."""
    t = re.sub(r"\bhalf an hour\b|\bhalf hour\b", "30 minutes", text, flags=re.I)
    t = re.sub(r"\ba quarter of an hour\b|\bquarter hour\b", "15 minutes", t, flags=re.I)
    t = re.sub(r"\b(\d+) and a half (hours?|minutes?)\b", lambda m: f"{int(m.group(1)) * 60 + 30} minutes" if m.group(2).startswith("hour") else f"{int(m.group(1)) * 60 + 30} seconds", t, flags=re.I)

    def tens(m):
        return str(_TENS[m.group(1).lower()] + (_UNITS[m.group(2).lower()] if m.group(2) else 0))
    t = re.sub(r"\b(" + "|".join(_TENS) + r")(?:[\s-](" + "|".join(k for k in _UNITS if k not in ("a", "an", "zero")) + r"))?\b", tens, t, flags=re.I)
    return re.sub(r"\b(" + "|".join(_UNITS) + r")\b(?=\s+(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)\b)",
                  lambda m: str(_UNITS[m.group(1).lower()]), t, flags=re.I)


def parse_duration(text: str) -> int | None:
    """Seconds in '5 minutes', '1 hour 30 minutes', '90 sec', 'two and a half minutes', '1:30' (m:ss); None if none."""
    t = words_to_number(text.lower())
    m = re.fullmatch(r"\s*(\d+):(\d{2})\s*", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    total, found = 0.0, False
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h\b|minutes?|mins?|m\b|seconds?|secs?|s\b)", t):
        found = True
        total += float(value) * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
    return int(round(total)) if found and total > 0 else None


# Tamil: number words as people say them before a unit (ஐந்து நிமிடம், அஞ்சு நிமிஷம், அரை மணி நேரம்)
TA_NUMBERS = {"ஒன்று": 1, "ஒண்ணு": 1, "ஒரு": 1, "இரண்டு": 2, "ரெண்டு": 2, "மூன்று": 3, "மூணு": 3, "நான்கு": 4, "நாலு": 4, "ஐந்து": 5,
              "அஞ்சு": 5, "ஆறு": 6, "ஏழு": 7, "எட்டு": 8, "ஒன்பது": 9, "பத்து": 10, "பதினொன்று": 11, "பன்னிரண்டு": 12, "பதினைந்து": 15,
              "இருபது": 20, "இருபத்தைந்து": 25, "முப்பது": 30, "நாற்பது": 40, "நாற்பத்தைந்து": 45, "ஐம்பது": 50, "அறுபது": 60, "தொண்ணூறு": 90}
_TA_NUM = r"(\d+(?:\.\d+)?|" + "|".join(sorted(TA_NUMBERS, key=len, reverse=True)) + r")"
_TA_HOUR = r"(?:மணி\s*நேர\S*|மணிநேர\S*|மணி\S*|ஹவர்\S*)"
_TA_MIN = r"(?:நிமிட\S*|நிமிஷ\S*|மினிட்\S*|நிமி\S*)"
_TA_SEC = r"(?:வினாடி\S*|நொடி\S*|செகண்ட்\S*|செக்கண்ட்\S*)"


def _ta_value(word: str) -> float:
    return float(word) if word[0].isdigit() else float(TA_NUMBERS[word])


def ta_duration(text: str) -> str | None:
    """'ஐந்து நிமிடம்' -> '5 minutes'; 'அரை மணி நேரம்' -> '30 minutes'; '1 மணி 30 நிமிடம்' -> '1 hours 30 minutes'."""
    t = re.sub(r"ஒன்றரை\s*" + _TA_HOUR, "90 நிமிடம்", text)
    t = re.sub(r"முக்கால்\s*" + _TA_HOUR, "45 நிமிடம்", t)
    t = re.sub(r"அரை\s*" + _TA_HOUR, "30 நிமிடம்", t)
    t = re.sub(r"கால்\s*" + _TA_HOUR, "15 நிமிடம்", t)
    parts = []
    for unit, pattern in (("hours", _TA_HOUR), ("minutes", _TA_MIN), ("seconds", _TA_SEC)):
        for m in re.finditer(_TA_NUM + r"\s*" + pattern, t):
            parts.append(f"{_ta_value(m.group(1)):g} {unit}")
    return " ".join(parts) or None


def ta_clock(text: str) -> str | None:
    """'காலை 6 மணிக்கு' -> '6:00 am'; 'இரவு 9:30' -> '9:30 pm'; '7 மணி 15 நிமிடத்துக்கு' -> '7:15'. None if no time."""
    m = re.search(r"(\d{1,2})(?:[:.](\d{2}))?\s*(?:மணி\S*)?(?:\s*(\d{1,2})\s*" + _TA_MIN + ")?", text)
    if m and (m.group(2) or re.search(r"\d\s*(?:மணி|[:.]\d)", text)):
        hour, minute = int(m.group(1)), int(m.group(2) or m.group(3) or 0)
    else:
        w = re.search(r"(" + "|".join(sorted(TA_NUMBERS, key=len, reverse=True)) + r")\s*மணி", text)
        if not w:
            return None
        hour, minute = int(TA_NUMBERS[w.group(1)]), 0
    if hour > 23 or minute > 59:
        return None
    ampm = ""
    if hour <= 12:
        if re.search(r"அதிகாலை|காலை|மார்னிங்", text):
            ampm = "am"
        elif re.search(r"மதியம்|பிற்பகல்", text):
            ampm = "pm"
        elif re.search(r"மாலை|சாயங்காலம்|சாயந்திரம்|ஈவினிங்", text):
            ampm = "pm"
        elif re.search(r"இரவு|ராத்திரி|ராத்ரி|நைட்", text):
            ampm = "am" if hour <= 4 or hour == 12 else "pm"
    return f"{hour}:{minute:02d}" + (f" {ampm}" if ampm else "")


def say_duration_ta(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = [f"{h} மணி நேரம்" if h else "", f"{m} நிமிடம்" if m else "", f"{sec} வினாடி" if sec and not h else ""]
    return " ".join(p for p in parts if p) or "0 வினாடி"


def ta_clock_text(ts: float) -> str:
    """A timestamp as Tamil says it: 'காலை 6:30' (the Tamil voice reads 6:30 as 'ஆறு மணி முப்பது நிமிடம்')."""
    from ..llm.offline import _ta_day_part

    lt = time.localtime(ts)
    return f"{_ta_day_part(lt.tm_hour)} {lt.tm_hour % 12 or 12}:{lt.tm_min:02d}"


def say_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = [f"{h} hour{'s' * (h != 1)}" if h else "", f"{m} minute{'s' * (m != 1)}" if m else "", f"{sec} second{'s' * (sec != 1)}" if sec and not h else ""]
    return " ".join(p for p in parts if p) or "0 seconds"


def parse_clock(text: str, now: float | None = None) -> float | None:
    """Next occurrence of '7:30', '7 am', '6.45 pm', '19:00', 'noon', 'midnight' as a timestamp."""
    now = now or time.time()
    t = text.lower().strip().replace(".", ":")
    t = {"noon": "12:00 pm", "midday": "12:00 pm", "midnight": "12:00 am"}.get(t, t)
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(a\s*m|p\s*m)?", t)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(" ", "")
    if hour > 23 or minute > 59:
        return None
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    lt = time.localtime(now)
    due = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0, 0, 0, -1))
    if not ampm and hour < 12 and due <= now:  # "7:30" said in the evening: the next 7:30, morning or evening
        due += 12 * 3600
    while due <= now:
        due += 24 * 3600
    return due


# -- timers and alarms ------------------------------------------------------------------------------------------------------
def timers(app) -> list[dict]:
    return sorted(app.db.get_setting("timers", []), key=lambda t: t["due"])


def _save_timers(app, items: list[dict]) -> None:
    app.db.set_setting("timers", items)
    app.bus.emit("timers.changed", timers=items)


def set_timer(ctx: ToolContext, duration: str = "", minutes: float = 0, seconds: float = 0, label: str = "", language: str = "") -> dict:
    secs = parse_duration(duration) if duration else None
    secs = secs or int(minutes * 60 + seconds)
    if not secs or secs < 1:
        raise ValueError("How long should the timer be? For example: set a timer for 5 minutes")
    if secs > 24 * 3600:
        raise ValueError("Timers can be at most 24 hours - use an alarm or a reminder for longer")
    item = {"id": "tm_" + uuid.uuid4().hex[:8], "kind": "timer", "label": label.strip()[:40], "seconds": secs, "due": time.time() + secs, "created": time.time(),
            **({"language": language} if language in ("ta",) else {})}  # announced in the language it was asked in
    _save_timers(ctx.app, [*timers(ctx.app), item])
    return {**item, "in": say_duration(secs)}


def set_alarm(ctx: ToolContext, time_of_day: str, label: str = "", language: str = "") -> dict:
    due = parse_clock(time_of_day)
    if due is None:
        raise ValueError("What time? For example: set an alarm for 7:30 am")
    item = {"id": "al_" + uuid.uuid4().hex[:8], "kind": "alarm", "label": label.strip()[:40], "due": due, "created": time.time(),
            **({"language": language} if language in ("ta",) else {})}
    _save_timers(ctx.app, [*timers(ctx.app), item])
    return {**item, "at": time.strftime("%I:%M %p", time.localtime(due)).lstrip("0"), "in": say_duration(due - time.time())}


def list_timers(ctx: ToolContext) -> dict:
    now = time.time()
    return {"timers": [{**t, "left": say_duration(max(0, t["due"] - now)), "at": time.strftime("%I:%M %p", time.localtime(t["due"])).lstrip("0")}
                       for t in timers(ctx.app)]}


def cancel_timer(ctx: ToolContext, which: str = "") -> dict:
    """`which`: a label ("tea"), "timer", "alarm", or "all"/"" (the only one, or all)."""
    items, w = timers(ctx.app), which.lower().strip()
    if w in ("", "all", "everything", "timers", "alarms"):
        gone = [t for t in items if w != "timers" or t["kind"] == "timer"] if w != "alarms" else [t for t in items if t["kind"] == "alarm"]
    elif w in ("timer", "alarm"):
        own = [t for t in items if t["kind"] == w]
        gone = own[:1]  # the soonest one
    else:
        gone = [t for t in items if w in (t["label"] or "").lower()]
    keep = [t for t in items if t not in gone]
    _save_timers(ctx.app, keep)
    return {"cancelled": [t["label"] or t["kind"] for t in gone], "remaining": len(keep)}


def due_message(t: dict) -> str:
    if t.get("language") == "ta":
        if t["kind"] == "alarm":
            return f"இப்போ {ta_clock_text(t['due'])}. அலாரம் நேரம்!" + (f" {t['label']}." if t["label"] else "")
        return f"நேரம் ஆச்சு! உங்க {say_duration_ta(t['seconds'])} டைமர் முடிஞ்சுது."
    if t["kind"] == "alarm":
        return f"It's {time.strftime('%I:%M %p', time.localtime(t['due'])).lstrip('0')}" + (f" - {t['label']}." if t["label"] else ". Time to get up!" if time.localtime(t["due"]).tm_hour < 11 else ".")
    return f"Time's up! Your {t['label'] + ' ' if t['label'] else ''}timer for {say_duration(t['seconds'])} is done."


# -- maths and units --------------------------------------------------------------------------------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "sin": lambda x: math.sin(math.radians(x)), "cos": lambda x: math.cos(math.radians(x)),
          "tan": lambda x: math.tan(math.radians(x)), "log": math.log10, "ln": math.log, "abs": abs, "round": round, "exp": math.exp}
_CONSTS = {"pi": math.pi, "e": math.e}


def spoken_math(text: str) -> str:
    """'what is 15 percent of 240' -> '(15/100)*240'; '23 times 47' -> '23*47'; '2 to the power of 10' -> '2**10'."""
    t = words_to_number(text.lower())
    t = re.sub(r"^\s*(?:what(?:'s| is)|calculate|compute|how much is|tell me|solve|evaluate)\s+", "", t)
    t = t.replace("×", "*").replace("÷", "/").replace("−", "-").replace(",", "")
    t = re.sub(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+", r"(\1/100)*", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", r"(\1/100)", t)
    t = re.sub(r"\bsquare root of\s+([\d.]+)", r"sqrt(\1)", t)
    t = re.sub(r"\b([\d.]+)\s+squared\b", r"(\1**2)", t)
    t = re.sub(r"\b([\d.]+)\s+cubed\b", r"(\1**3)", t)
    for words, op in (("to the power of", "**"), ("multiplied by", "*"), ("divided by", "/"), ("times", "*"), ("plus", "+"),
                      ("minus", "-"), ("over", "/"), (" x ", "*"), ("mod", "%")):
        t = t.replace(words, op)
    t = t.replace("^", "**")
    return t.strip(" ?.=")


def evaluate(expr: str) -> float:
    """Arithmetic only (no names except a few maths functions/constants, no attribute access, bounded powers)."""
    node = ast.parse(expr, mode="eval").body

    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
                raise ValueError("That power is too large")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and len(n.args) == 1 and not n.keywords:
            return _FUNCS[n.func.id](ev(n.args[0]))
        if isinstance(n, ast.Name) and n.id in _CONSTS:
            return _CONSTS[n.id]
        raise ValueError("Only numbers and + - * / ^ % and sqrt, sin, cos, tan, log are allowed")
    return ev(node)


def is_math(text: str) -> bool:
    expr = spoken_math(text)
    if not re.search(r"\d", expr) or not re.search(r"[-+*/%]|sqrt\(", expr) or re.search(r"[a-z]{2,}", re.sub(r"sqrt|sin|cos|tan|log|ln|abs|round|exp|pi", "", expr)):
        return False
    try:
        evaluate(expr)
        return True
    except Exception:  # noqa: BLE001
        return False


def nice(x: float) -> str:
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        x = int(x)
    if isinstance(x, int):
        return f"{x:,}"
    return f"{x:,.6g}" if abs(x) < 1e6 else f"{x:,.2f}"


def calculate(ctx: ToolContext, expression: str) -> dict:
    expr = spoken_math(expression)
    return {"expression": expr, "result": nice(evaluate(expr))}


# units: dimension -> {name: factor to the base unit}
UNITS = {
    "length": {"mm": 0.001, "millimeter": 0.001, "cm": 0.01, "centimeter": 0.01, "m": 1, "meter": 1, "metre": 1, "km": 1000, "kilometer": 1000,
               "kilometre": 1000, "inch": 0.0254, "in": 0.0254, "foot": 0.3048, "feet": 0.3048, "ft": 0.3048, "yard": 0.9144, "yd": 0.9144,
               "mile": 1609.344, "mi": 1609.344},
    "mass": {"mg": 1e-6, "milligram": 1e-6, "g": 0.001, "gram": 0.001, "kg": 1, "kilogram": 1, "kilo": 1, "tonne": 1000, "ton": 1000,
             "pound": 0.45359237, "lb": 0.45359237, "lbs": 0.45359237, "ounce": 0.028349523, "oz": 0.028349523},
    "volume": {"ml": 0.001, "milliliter": 0.001, "millilitre": 0.001, "l": 1, "liter": 1, "litre": 1, "gallon": 3.785411784, "gal": 3.785411784,
               "cup": 0.24, "teaspoon": 0.00492892, "tablespoon": 0.0147868, "pint": 0.473176},
    "speed": {"kmh": 1 / 3.6, "kph": 1 / 3.6, "km/h": 1 / 3.6, "mph": 0.44704, "m/s": 1, "knot": 0.514444},
    "data": {"byte": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kilobyte": 1e3, "megabyte": 1e6, "gigabyte": 1e9, "terabyte": 1e12},
    "time": {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400, "week": 604800},
}
TEMPS = {"c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}


def _unit(name: str) -> tuple[str, str] | None:
    n = name.lower().strip().strip(".").replace("degrees ", "").replace("degree ", "").replace("°", "")
    n = {"kilometers per hour": "kmh", "kilometres per hour": "kmh", "miles per hour": "mph", "meters per second": "m/s"}.get(n, n)
    if n in TEMPS:
        return "temperature", TEMPS[n]
    for cand in (n, n[:-1] if n.endswith("s") else n, n[:-2] if n.endswith("es") else n):
        for dim, table in UNITS.items():
            if cand in table:
                return dim, cand
    return None


def convert_units(ctx: ToolContext, value: float, from_unit: str, to_unit: str) -> dict:
    a, b = _unit(from_unit), _unit(to_unit)
    if not a or not b:
        raise ValueError(f"I don't know the unit '{from_unit if not a else to_unit}'")
    if a[0] != b[0]:
        raise ValueError(f"Can't convert {a[0]} to {b[0]}")
    v = float(value)
    if a[0] == "temperature":
        c = {"c": v, "f": (v - 32) * 5 / 9, "k": v - 273.15}[a[1]]
        out = {"c": c, "f": c * 9 / 5 + 32, "k": c + 273.15}[b[1]]
    else:
        out = v * UNITS[a[0]][a[1]] / UNITS[b[0]][b[1]]
    shown = round(out, 2) if abs(out) >= 1 else float(f"{out:.3g}")  # read aloud: "37.78", not "37.7778"
    return {"value": nice(v), "from": from_unit, "result": nice(shown), "to": to_unit}


# -- weather (Open-Meteo: free, no key) -------------------------------------------------------------------------------------
WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
       55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain", 71: "light snow",
       73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers", 82: "violent showers", 85: "snow showers",
       86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail"}


def home_city(app) -> str:
    city = app.db.get_setting("profile", {}).get("city", "")
    if city:
        return city
    for m in app.memory.recall("I live in my city", k=3):  # "I live in Chennai" is learned by auto-memory
        hit = re.search(r"\b(?:live in|my (?:home )?city is|i'?m from)\s+([A-Z][\w .'-]+?)(?:[.\"!,]|$)", m["content"], re.I)
        if hit:
            return hit.group(1).strip()
    return ""


def set_home_city(ctx: ToolContext, city: str) -> dict:
    city = city.strip(" .!?").title()
    if not city:
        raise ValueError("Which city?")
    ctx.app.db.set_setting("profile", {**ctx.app.db.get_setting("profile", {}), "city": city})
    return {"city": city}


def weather(ctx: ToolContext, place: str = "", day: str = "today") -> dict:
    import httpx

    place = (place or home_city(ctx.app)).strip(" ?.!")
    if not place:
        raise ValueError("Which city? Say for example: my city is Chennai")
    with httpx.Client(timeout=15, transport=ctx.app.http_transport) as c:
        g = c.get(f"https://geocoding-api.open-meteo.com/v1/search?name={quote_plus(place)}&count=1&language=en&format=json").json()
        if not g.get("results"):
            raise ValueError(f"I couldn't find a place called {place}")
        loc = g["results"][0]
        f = c.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto", "forecast_days": 3,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"}).json()
    cur, daily = f.get("current", {}), f.get("daily", {})
    i = 1 if day.lower().startswith("tom") else 0
    return {"place": loc.get("name", place), "country": loc.get("country", ""), "day": "tomorrow" if i else "today",
            "now": {"temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"), "humidity": cur.get("relative_humidity_2m"),
                    "wind": cur.get("wind_speed_10m"), "sky": WMO.get(cur.get("weather_code"), "")},
            "high": (daily.get("temperature_2m_max") or [None] * 3)[i], "low": (daily.get("temperature_2m_min") or [None] * 3)[i],
            "rain_chance": (daily.get("precipitation_probability_max") or [None] * 3)[i],
            "sky": WMO.get((daily.get("weather_code") or [None] * 3)[i], ""), "source": "Open-Meteo"}


# -- lists ------------------------------------------------------------------------------------------------------------------
def _lists(app) -> dict:
    return app.db.get_setting("lists", {})


def _list_name(name: str) -> str:
    n = re.sub(r"\b(?:my|the|list)\b", "", (name or "shopping").lower()).strip()
    return {"grocery": "shopping", "groceries": "shopping", "todo": "to-do", "to do": "to-do"}.get(n, n) or "shopping"


def _split_items(items: str) -> list[str]:
    parts = re.split(r",|\band\b|\n", items)
    return [p.strip(" .!?").strip() for p in parts if p.strip(" .!?").strip()]


def list_add(ctx: ToolContext, items: str, list_name: str = "shopping") -> dict:
    name, lists = _list_name(list_name), _lists(ctx.app)
    current = lists.get(name, [])
    added = [i for i in _split_items(items) if i.lower() not in {x.lower() for x in current}]
    lists[name] = current + added
    ctx.app.db.set_setting("lists", lists)
    return {"list": name, "added": added, "items": lists[name]}


def list_show(ctx: ToolContext, list_name: str = "shopping") -> dict:
    name = _list_name(list_name)
    lists = _lists(ctx.app)
    return {"list": name, "items": lists.get(name, []), "other_lists": sorted(k for k in lists if k != name and lists[k])}


def list_remove(ctx: ToolContext, item: str, list_name: str = "shopping") -> dict:
    name, lists = _list_name(list_name), _lists(ctx.app)
    want = {i.lower() for i in _split_items(item)}
    before = lists.get(name, [])
    lists[name] = [x for x in before if x.lower() not in want]
    ctx.app.db.set_setting("lists", lists)
    return {"list": name, "removed": [x for x in before if x.lower() in want], "items": lists[name]}


def list_clear(ctx: ToolContext, list_name: str = "shopping") -> dict:
    name, lists = _list_name(list_name), _lists(ctx.app)
    gone = lists.pop(name, [])
    ctx.app.db.set_setting("lists", lists)
    return {"list": name, "cleared": len(gone)}


# -- music ----------------------------------------------------------------------------------------------------------------------
def music_url(query: str, service: str = "youtube") -> str:
    q = quote_plus(query.strip())
    if not q:
        raise SecurityError("What should I play?")
    return f"https://music.youtube.com/search?q={q}" if "music" in service.lower() else f"https://www.youtube.com/results?search_query={q}"


def play_music(ctx: ToolContext, query: str, service: str = "youtube") -> dict:
    """Opens the search in the user's own browser (signed in, their account): the first result is one click away."""
    from .system import open_url

    return {"playing": query, **open_url(ctx, music_url(query, service))}


def register(reg: ToolRegistry) -> None:
    S, N = {"type": "string"}, {"type": "number"}
    reg.register(Tool("set_timer", "Start a countdown timer, e.g. duration '5 minutes' or '1 hour 30 minutes', with an optional label like 'tea'.",
                      {"duration": S, "minutes": N, "seconds": N, "label": S, "language": S}, set_timer))
    reg.register(Tool("set_alarm", "Set an alarm for a time of day, e.g. '7:30 am'.", {"time_of_day": S, "label": S, "language": S}, set_alarm, required=["time_of_day"]))
    reg.register(Tool("list_timers", "Running timers and alarms, with the time left.", {}, list_timers))
    reg.register(Tool("cancel_timer", "Cancel a timer or alarm by its label, or 'timer', 'alarm', or 'all'.", {"which": S}, cancel_timer))
    reg.register(Tool("weather", "Current weather and today's/tomorrow's forecast for a place (default: the user's city).",
                      {"place": S, "day": {"type": "string", "enum": ["today", "tomorrow"]}}, weather, timeout=25))
    reg.register(Tool("set_home_city", "Remember the user's city (used for the weather).", {"city": S}, set_home_city, required=["city"]))
    reg.register(Tool("calculate", "Exact arithmetic: + - * / ^ %, percentages, square roots. Use instead of mental maths.", {"expression": S},
                      calculate, required=["expression"]))
    reg.register(Tool("convert_units", "Convert length, weight, volume, temperature, speed, data or time units.",
                      {"value": N, "from_unit": S, "to_unit": S}, convert_units, required=["value", "from_unit", "to_unit"]))
    reg.register(Tool("list_add", "Add items (comma-separated) to a list - shopping by default, or any named list.", {"items": S, "list_name": S},
                      list_add, required=["items"]))
    reg.register(Tool("list_show", "What is on a list (shopping by default).", {"list_name": S}, list_show))
    reg.register(Tool("list_remove", "Remove items from a list.", {"item": S, "list_name": S}, list_remove, required=["item"]))
    reg.register(Tool("list_clear", "Empty a whole list.", {"list_name": S}, list_clear, level=Level.CONFIRM, trust_eligible=True,
                      preview=lambda a: f"Empty your {_list_name(a.get('list_name', 'shopping'))} list"))
    reg.register(Tool("play_music", "Play a song, artist or playlist: opens YouTube (or YouTube Music) search in the user's browser.",
                      {"query": S, "service": {"type": "string", "enum": ["youtube", "youtube music"]}}, play_music, required=["query"],
                      level=Level.CONFIRM, trust_eligible=True, preview=lambda a: f"Play \"{a['query']}\" - open {music_url(a['query'], a.get('service', 'youtube'))} in your browser"))
