"""Written (formal) Tamil -> spoken Tamil, the way people talk.

The owner asked JARVIS to "speak normal Tamil" (2026-11-21: "நார்மல் தமிழ்ல பேசு"): its replies came from the
translation model and its own phrases in formal written Tamil (உள்ளது, திறக்கிறேன், முடியவில்லை), which sounds like a
newsreader. This turns the common written forms into the spoken ones (இருக்கு, திறக்குறேன், முடியல) with word rules and
verb-ending rules. Deterministic and instant; anything it does not know is left as it is. Setting: JARVIS_TAMIL_STYLE
(spoken | formal).
"""
from __future__ import annotations

import re

_T = r"[஀-௿]"  # a Tamil character (letters and vowel signs)
_START, _END = rf"(?<!{_T})", rf"(?!{_T})"

# whole words (written -> spoken)
WORDS = {
    "உள்ளது": "இருக்கு", "உள்ளன": "இருக்கு", "உள்ளார்": "இருக்கார்", "உள்ளனர்": "இருக்காங்க", "உள்ளீர்கள்": "இருக்கீங்க",
    "இருக்கிறது": "இருக்கு", "இருக்கின்றது": "இருக்கு", "இருக்கின்றன": "இருக்கு", "இருக்கிறேன்": "இருக்கேன்",
    "இருக்கிறீர்கள்": "இருக்கீங்க", "இருக்கிறார்": "இருக்கார்", "இருக்கிறார்கள்": "இருக்காங்க", "இருக்கிறோம்": "இருக்கோம்",
    "இல்லை": "இல்ல", "வேண்டும்": "வேணும்", "வேண்டுமா": "வேணுமா", "வேண்டாம்": "வேணாம்", "வேண்டுமானால்": "வேணும்னா",
    "நீங்கள்": "நீங்க", "உங்கள்": "உங்க", "உங்களை": "உங்கள", "அவர்கள்": "அவங்க", "இவர்கள்": "இவங்க", "நாங்கள்": "நாங்க",
    "எங்கள்": "எங்க", "அவர்களுக்கு": "அவங்களுக்கு", "வேறு": "வேற", "எப்போது": "எப்போ", "இப்போது": "இப்போ",
    "அப்போது": "அப்போ", "எங்கே": "எங்க", "இங்கே": "இங்க", "அங்கே": "அங்க", "மிகவும்": "ரொம்ப", "நன்றாக": "நல்லா",
    "மகிழ்ச்சி": "சந்தோஷம்", "என்ன": "என்ன", "எதுவும்": "எதுவும்", "செய்கிறேன்": "செய்றேன்", "செய்தேன்": "செஞ்சேன்",
    "செய்து": "செஞ்சு", "செய்ய": "செய்ய", "சொல்லுங்கள்": "சொல்லுங்க", "கேளுங்கள்": "கேளுங்க", "பாருங்கள்": "பாருங்க",
    "முடியும்": "முடியும்", "தயவுசெய்து": "தயவு செஞ்சு", "என்பது": "அப்படின்னா", "போன்ற": "மாதிரி", "போல": "மாதிரி",
    "வைத்தேன்": "வச்சேன்", "வைத்து": "வச்சு", "வைக்கிறேன்": "வைக்குறேன்", "மாற்றினேன்": "மாத்தினேன்", "மாற்றி": "மாத்தி",
    "ஆம்": "ஆமா", "மீண்டும்": "திரும்ப", "இன்று": "இன்னைக்கு", "நாளை": "நாளைக்கு", "மன்னிக்கவும்": "மன்னிச்சுக்கோங்க", "போட்டேன்": "போட்டேன்",
    "சொல்கிறேன்": "சொல்றேன்", "பார்க்கிறேன்": "பார்க்குறேன்", "தெரியும்": "தெரியும்", "அதைச்": "அத", "அதை": "அத", "இதை": "இத",
}

# endings (applied to the end of a word, longest first): written -> spoken
ENDINGS = [
    ("க்கிறீர்கள்", "க்குறீங்க"), ("கிறீர்கள்", "றீங்க"), ("க்கிறேன்", "க்குறேன்"), ("கின்றேன்", "றேன்"), ("கிறேன்", "றேன்"),
    ("ய்கிறது", "ய்யுது"), ("ய்கிறேன்", "ய்றேன்"),
    ("க்கிறோம்", "க்குறோம்"), ("கிறோம்", "றோம்"), ("க்கிறது", "க்குது"), ("ுகிறது", "ுது"), ("ிகிறது", "ியுது"), ("கிறது", "குது"),
    ("க்கிறார்கள்", "க்குறாங்க"), ("கிறார்கள்", "றாங்க"), ("க்கிறார்", "க்குறார்"), ("கிறார்", "றார்"),
    ("வில்லை", "ல"), ("ீர்கள்", "ீங்க"), ("ுங்கள்", "ுங்க"), ("ார்கள்", "ாங்க"),
    ("த்தில்", "த்துல"), ("யில்", "யில"), ("ில்", "ில"), ("ாகக்", "ா"), ("ாக", "ா"),
]
_WORD = re.compile(rf"{_START}{_T}+{_END}")


def _spoken_word(w: str) -> str:
    if w in WORDS:
        return WORDS[w]
    if len(w) <= 3:
        return w
    for written, spoken in ENDINGS:
        if w.endswith(written) and len(w) > len(written) + 1:
            return w[: -len(written)] + spoken
    return w


def spoken_tamil(text: str) -> str:
    """Written Tamil in `text` -> spoken Tamil; other text (English, numbers, code) is untouched."""
    if not text or not re.search(_T, text):
        return text
    parts = re.split(r"(```[\s\S]*?```|`[^`]*`)", text)  # never inside code
    out = []
    for p in parts:
        if not p.startswith("`"):
            # "<word> என்று" -> "<word>னு" (முடியும் என்று -> முடியும்னு, சரி என்று -> சரின்னு)
            p = re.sub(rf"({_T}+) என்று{_END}", lambda m: m.group(1) + ("னு" if m.group(1).endswith("்") else "ன்னு"), p)
            p = _WORD.sub(lambda m: _spoken_word(m.group(0)), p)
        out.append(p)
    return "".join(out)


def styled(text: str, style: str = "spoken") -> str:
    return spoken_tamil(text) if style == "spoken" else text
