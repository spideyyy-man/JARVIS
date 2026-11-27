"""The floating icon: a graphite puck ringed by a bezel of light ticks. The bezel is the whole language of the icon.

Design (2026-11-27; the owner found the glass planet with orbiting rings "very mid"): one solid, opaque puck with a fine
light edge and a soft drop shadow, so it reads on dark AND light desktops (the old glow turned into a grey smudge on a
light one); 36 short ticks round the face carry the state - a single tick travelling slowly while waiting, a ripple while
listening, comets chasing while thinking, a stepping hand while working, a round equaliser while speaking. A small lit
core sits in the middle. A timer is a thin bright arc just outside the edge; an action waiting for approval turns the edge
amber and adds a badge. Colours follow the 3D core of the window (cyan ready, green listening, violet thinking, teal
working, blue speaking, red error) and ease from one state to the next.

Static layers (shadow, puck, halo, core) are drawn once and cached; a frame draws 36 ticks and downsamples (~1-2 ms).
`render(state)` is pure (unit-tested); `LayeredWindow` only presents a finished frame.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

SIZE = 72  # window size in px (puck + room for its shadow, halo and the timer arc)
ORB_R = 22  # puck radius in px
SS = 3  # supersampling factor

COLORS = {"idle": (34, 211, 238), "waiting": (34, 211, 238), "listening": (52, 211, 153), "recording": (52, 211, 153),
          "transcribing": (163, 230, 53), "thinking": (167, 139, 250), "executing": (45, 212, 191), "speaking": (96, 165, 250),
          "error": (248, 113, 113), "offline": (100, 116, 139), "loading": (148, 163, 184),
          "following": (74, 222, 128)}  # conversation mode: listening without the wake word
AMBER = (245, 158, 11)
TICKS = 36
TICK_OUT, TICK_IN = 19.0, 15.6  # tick radii in px (inside the puck's face)
DIM = (54, 66, 88)  # an unlit tick: barely there, so the lit ones carry the state


@dataclass
class OrbState:
    state: str = "waiting"
    t: float = 0.0  # seconds, for animation
    color: tuple[float, float, float] = COLORS["waiting"]  # current (eased) colour
    approval: bool = False
    timer: float | None = None  # fraction of the soonest timer still to go: a thin arc that shrinks as it runs down


def ease_color(current, target, k: float = 0.12) -> tuple[float, float, float]:
    return tuple(c + (t - c) * k for c, t in zip(current, target))


_CACHE: dict = {}


def _q(color) -> tuple[int, int, int]:
    """Colour rounded so easing between states reuses cached layers."""
    return tuple(int(c) // 16 * 16 for c in color)


def _shadow() -> Image.Image:
    if "shadow" not in _CACHE:
        s, c, r = SIZE * SS, SIZE * SS / 2, ORB_R * SS
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        dy = 1.6 * SS
        ImageDraw.Draw(img).ellipse((c - r, c - r + dy, c + r, c + r + dy), fill=(0, 0, 0, 120))
        _CACHE["shadow"] = img.filter(ImageFilter.GaussianBlur(2.6 * SS))
    return _CACHE["shadow"]


def _puck() -> Image.Image:
    """The graphite face: lit from the top left, a darker rim, a fine light edge and a faint bevel highlight."""
    if "puck" not in _CACHE:
        import numpy as np

        s, r = SIZE * SS, ORB_R * SS
        ys, xs = np.mgrid[0:s, 0:s].astype(np.float32)
        x, y = (xs - s / 2 + 0.5) / r, (ys - s / 2 + 0.5) / r
        rr = np.sqrt(x * x + y * y)
        light = np.clip(0.5 - 0.45 * (0.55 * x + 0.85 * y), 0, 1)  # brighter towards the top left
        rim = np.clip((rr - 0.78) / 0.22, 0, 1) ** 1.5  # darker towards the edge
        top, bottom = np.array([30, 38, 54], np.float32), np.array([11, 15, 23], np.float32)
        rgb = bottom + (top - bottom) * light[..., None]
        rgb = rgb * (1 - 0.35 * rim[..., None])
        edge = np.clip((1.0 - rr) * r / (1.3 * SS), 0, 1)  # soft anti-aliased outline
        arr = np.dstack([np.clip(rgb, 0, 255), edge * 255]).astype(np.uint8)
        img = Image.fromarray(arr, "RGBA")
        # ImageDraw on an RGBA image REPLACES pixels: a 30%-opaque line would punch a see-through hole in the solid
        # puck (on a light desktop the ticks showed up whitish). Lines go on their own layer, then are blended on.
        lines = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(lines)
        c = s / 2
        # a fine light edge separates the puck from dark desktops
        d.ellipse((c - r + 0.6 * SS, c - r + 0.6 * SS, c + r - 0.6 * SS, c + r - 0.6 * SS), outline=(203, 213, 225, 70), width=max(1, int(0.9 * SS)))
        # a bevel catching the light on the upper rim
        d.arc((c - r + 1.6 * SS, c - r + 1.6 * SS, c + r - 1.6 * SS, c + r - 1.6 * SS), 200, 320, fill=(255, 255, 255, 46), width=max(1, int(1.1 * SS)))
        img.alpha_composite(lines)
        _CACHE["puck"] = img
    return _CACHE["puck"]


def _halo(color, level: int) -> Image.Image:
    """A tight coloured glow behind the puck at one of 12 strengths - only while something is happening."""
    key = ("halo", _q(color), level)
    if key not in _CACHE:
        s, c, r = SIZE * SS, SIZE * SS / 2, (ORB_R + 3.5) * SS
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((c - r, c - r, c + r, c + r), fill=(*_q(color), int(115 * level / 12)))
        _CACHE[key] = img.filter(ImageFilter.GaussianBlur(3.2 * SS))
    return _CACHE[key]


def _core(color, size: int, active: bool) -> Image.Image:
    """The lit core: a small disc with a soft glow and a brighter centre."""
    key = ("core", _q(color), size, active)
    if key not in _CACHE:
        s, c = SIZE * SS, SIZE * SS / 2
        cr = size / 4 * SS
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        col = _q(color)
        glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((c - cr * 2.2, c - cr * 2.2, c + cr * 2.2, c + cr * 2.2), fill=(*col, 70 if active else 25))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(2 * SS)))
        d = ImageDraw.Draw(img)
        d.ellipse((c - cr, c - cr, c + cr, c + cr), fill=(*col, 255 if active else 150))
        hi = tuple(min(255, v + 90) for v in col)
        d.ellipse((c - cr * 0.45, c - cr * 0.45, c + cr * 0.45, c + cr * 0.45), fill=(*hi, 255 if active else 120))
        _CACHE[key] = img
    return _CACHE[key]


def _tail(pos: float, i: int, length: float) -> float:
    """Brightness of tick i behind a head at `pos` (in ticks) moving clockwise, fading over `length` ticks."""
    behind = (pos - i) % TICKS
    return max(0.0, 1.0 - behind / length) ** 1.6


def tick_levels(state: str, t: float) -> tuple[list[float], list[float]]:
    """(brightness 0..1, extra inward length 0..1) for each of the TICKS ticks - the icon's whole animation."""
    n = TICKS
    lv, ln = [0.0] * n, [0.0] * n
    if state in ("waiting", "idle"):
        head = (t * 0.22 % 1.0) * n  # one tick goes round every ~4.5 s, a calm heartbeat
        lv = [0.14 + 0.86 * _tail(head, i, 6) for i in range(n)]
    elif state in ("listening", "recording"):
        lv = [0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * (2 * i / n - t * 1.3))) ** 2 for i in range(n)]
        ln = [0.35 * (0.5 + 0.5 * math.sin(2 * math.pi * (2 * i / n - t * 1.3))) for i in range(n)]
    elif state == "following":
        lv = [0.18 + 0.6 * (0.5 + 0.5 * math.sin(2 * math.pi * (i / n - t * 0.35))) ** 2 for i in range(n)]
    elif state in ("thinking", "transcribing", "loading"):
        comets, speed, length = {"thinking": (3, 0.75, 7), "transcribing": (2, 1.1, 6), "loading": (1, 0.45, 9)}[state]
        for k in range(comets):
            head = ((t * speed + k / comets) % 1.0) * n
            for i in range(n):
                lv[i] = max(lv[i], _tail(head, i, length))
        lv = [0.1 + 0.9 * v for v in lv]
    elif state == "executing":
        step = int(t * 12) % n  # a hand stepping round, leaving a fading trail: work in progress
        lv = [0.12 + 0.88 * (1.0 if i == step else 0.6 * _tail(step, i, 10)) for i in range(n)]
    elif state == "speaking":
        for i in range(n):
            a = abs(math.sin(t * 8.5 + i * 0.9) * math.sin(t * 5.1 + i * 0.37) + 0.35 * math.sin(t * 3.3 + i * 1.7))
            lv[i], ln[i] = 0.3 + 0.7 * min(1.0, a), min(1.0, a)
    elif state == "error":
        lv = [0.3 + 0.5 * (0.5 + 0.5 * math.sin(t * 3.2))] * n
    else:  # offline
        lv = [0.06] * n
    return lv, ln


def render(o: OrbState) -> Image.Image:
    """One frame (RGBA, SIZE x SIZE) for the given state and time."""
    if len(_CACHE) > 400:
        _CACHE.clear()
    s, c, R, t = SIZE * SS, SIZE * SS / 2, ORB_R * SS, o.t
    col = tuple(int(v) for v in o.color)
    active = o.state not in ("offline",)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    busy = o.state not in ("waiting", "idle", "offline")
    if busy or o.approval:  # a halo only while something is happening; at rest the icon is quiet
        breathe = 0.5 + 0.5 * math.sin(t * (2.4 if o.state in ("recording", "listening", "speaking") else 1.4))
        level = max(1, min(12, round(3 + 7 * breathe)))
        img.alpha_composite(_halo(AMBER if o.approval and not busy else col, level))
    img.alpha_composite(_shadow())
    img.alpha_composite(_puck())
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))  # lines are blended on, never drawn straight onto the puck (see _puck)
    d = ImageDraw.Draw(layer)
    lv, ln = tick_levels(o.state, t)
    for i in range(TICKS):
        a = -math.pi / 2 + 2 * math.pi * i / TICKS
        v = lv[i]
        ri = (TICK_IN - 2.6 * ln[i]) * SS
        ro = TICK_OUT * SS
        lit = tuple(min(255, int(col[k] + (255 - col[k]) * 0.35 * max(0.0, v - 0.7) / 0.3)) for k in range(3))  # hottest ticks go towards white
        tint = tuple(int(DIM[k] + (lit[k] - DIM[k]) * v) for k in range(3))
        alpha = int(70 + 185 * v ** 0.8) if active else 70
        w = (1.15 + 0.85 * v) * SS
        ca, sa = math.cos(a), math.sin(a)
        d.line([(c + ca * ri, c + sa * ri), (c + ca * ro, c + sa * ro)], fill=(*tint, alpha), width=max(1, int(w)))
    img.alpha_composite(layer)
    # the core: steady while waiting, beating while listening, pulsing gently otherwise
    pulse = {"recording": 0.18, "listening": 0.18, "following": 0.1}.get(o.state, 0.05)
    size = int(round(4.4 * 4 * (1 + pulse * math.sin(t * (6 if pulse > 0.1 else 2)))))
    img.alpha_composite(_core(col, size, active))
    if o.timer is None and not o.approval:
        return img.resize((SIZE, SIZE), Image.LANCZOS)
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if o.timer is not None:  # countdown: a thin bright arc just outside the edge, shrinking clockwise from the top
        rr, end = R + 2.4 * SS, -90 + 360 * max(0.004, o.timer)
        # a dark under-stroke keeps the bright arc visible on a light desktop too
        d.arc((c - rr, c - rr, c + rr, c + rr), -90, end, fill=(15, 20, 30, 170), width=max(1, int(2.6 * SS)))
        d.arc((c - rr, c - rr, c + rr, c + rr), -90, end, fill=(236, 240, 245, 245), width=max(1, int(1.4 * SS)))
    if o.approval:  # something needs the owner: an amber edge that breathes, and a badge at the top right
        k = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 4))
        d.ellipse((c - R + 0.6 * SS, c - R + 0.6 * SS, c + R - 0.6 * SS, c + R - 0.6 * SS), outline=(*AMBER, int(255 * k)), width=int(1.4 * SS))
        bx, by, br = c + R * 0.72, c - R * 0.72, 4.8 * SS
        d.ellipse((bx - br, by - br, bx + br, by + br), fill=(*AMBER, 255), outline=(15, 20, 30, 255), width=SS)
    img.alpha_composite(layer)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


class LayeredWindow:
    """Shows RGBA frames on a Windows top-level window with per-pixel alpha (UpdateLayeredWindow). Fully transparent
    pixels let clicks through; the orb itself receives them."""

    def __init__(self, hwnd: int, size: int = SIZE):
        import ctypes
        from ctypes import wintypes

        self.ct, self.size = ctypes, size
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        self.u, self.g = u, g
        u.GetDC.restype = wintypes.HDC
        u.GetDC.argtypes = [wintypes.HWND]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        u.UpdateLayeredWindow.restype = wintypes.BOOL
        u.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                          ctypes.POINTER(wintypes.POINT), wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        self.hwnd = hwnd
        ex = u.GetWindowLongPtrW(hwnd, -20)  # GWL_EXSTYLE
        u.SetWindowLongPtrW(hwnd, -20, ex | 0x00080000 | 0x00000080)  # WS_EX_LAYERED | WS_EX_TOOLWINDOW (no taskbar button)
        self.screen = u.GetDC(None)
        self.mem = g.CreateCompatibleDC(self.screen)
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bits = ctypes.c_void_p()
        self.bmp = g.CreateDIBSection(self.screen, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        if not self.bmp:
            raise OSError("CreateDIBSection failed")
        g.SelectObject(self.mem, self.bmp)
        self.size_s = wintypes.SIZE(size, size)
        self.src = wintypes.POINT(0, 0)
        self.blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, per-pixel alpha (AC_SRC_ALPHA)

    def show(self, img: Image.Image) -> bool:
        import numpy as np

        a = np.asarray(img, dtype=np.uint16)
        alpha = a[..., 3:4]
        rgb = (a[..., :3] * alpha + 127) // 255  # premultiplied, as UpdateLayeredWindow requires
        bgra = np.concatenate([rgb[..., ::-1], alpha], axis=2).astype(np.uint8)
        self.ct.memmove(self.bits, bgra.tobytes(), self.size * self.size * 4)
        return bool(self.u.UpdateLayeredWindow(self.hwnd, self.screen, None, self.ct.byref(self.size_s), self.mem,
                                               self.ct.byref(self.src), 0, self.ct.byref(self.blend), 2))  # ULW_ALPHA
