"""The floating icon's orb: smooth, anti-aliased frames with a soft glow, shown with per-pixel transparency.

The first icon was drawn with Tk's canvas (no anti-aliasing, hard colour-key edge, ~25 fps): the owner found it static
and jagged (2026-11-20). Now every frame is rendered with Pillow at 2x and scaled down (smooth edges), the glow is a
real blur, and on Windows the frame is shown through a layered window (UpdateLayeredWindow), so the glow fades into
whatever is behind it. Colours ease from one state to the next instead of jumping.

`render(state)` is pure (unit-tested); `LayeredWindow` only presents a finished frame.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

SIZE = 72  # window size in px (orb + room for the glow and ripples)
ORB_R = 21  # orb radius in px
SS = 3  # supersampling factor (3x: crisper edges than 2x)

COLORS = {"idle": (34, 211, 238), "waiting": (34, 211, 238), "listening": (34, 197, 94), "recording": (34, 197, 94),
          "transcribing": (163, 230, 53), "thinking": (245, 158, 11), "executing": (251, 146, 60), "speaking": (96, 165, 250),
          "error": (239, 68, 68), "offline": (100, 116, 139), "loading": (148, 163, 184),
          "following": (74, 222, 128)}  # conversation mode: listening without the wake word
AMBER = (245, 158, 11)


@dataclass
class OrbState:
    state: str = "waiting"
    t: float = 0.0  # seconds, for animation
    color: tuple[float, float, float] = COLORS["waiting"]  # current (eased) colour
    approval: bool = False
    timer: float | None = None  # fraction of the soonest timer still to go: a thin ring that shrinks as it runs down


def ease_color(current, target, k: float = 0.12) -> tuple[float, float, float]:
    return tuple(c + (t - c) * k for c, t in zip(current, target))


_GLOW: dict = {}
_BODY: dict = {}


def _glow(color: tuple[int, int, int]) -> Image.Image:
    """A soft disc of the state colour, blurred - cached per colour (rounded so easing reuses entries)."""
    key = tuple(int(c) // 24 * 24 for c in color)
    if key not in _GLOW:
        s = SIZE * SS
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        r = (ORB_R + 4) * SS  # a tight halo: the wide one made the orb look blurry (2026-11-20)
        c = s // 2
        d.ellipse((c - r, c - r, c + r, c + r), fill=(*key, 120))
        _GLOW[key] = img.filter(ImageFilter.GaussianBlur(3.5 * SS))
    return _GLOW[key]


def _glow_at(color, k: float) -> Image.Image:
    """The glow at one of 16 brightness levels (blending every frame cost ~3 ms)."""
    level = max(0, min(16, round(k * 16)))
    key = (tuple(int(c) // 24 * 24 for c in color), level)
    if key not in _GLOW:
        g = _glow(color)
        _GLOW[key] = Image.blend(Image.new("RGBA", g.size, (0, 0, 0, 0)), g, level / 16)
    return _GLOW[key]


def _core_glow(color, cr: float, active: bool) -> Image.Image:
    key = ("core", tuple(int(c) // 24 * 24 for c in color), round(cr), active)
    if key not in _GLOW:
        s, c = SIZE * SS, SIZE * SS / 2
        core = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(core).ellipse((c - cr * 1.9, c - cr * 1.9, c + cr * 1.9, c + cr * 1.9), fill=(*key[1], 60 if active else 25))
        _GLOW[key] = core.filter(ImageFilter.GaussianBlur(1.5 * SS))
    return _GLOW[key]


def _comet(color, inset: float, span: float, n: int, width: float) -> Image.Image:
    """An arc that fades from a bright head into a tail, drawn once per colour; frames just rotate it (drawing the
    arcs every frame cost ~10 ms)."""
    key = ("comet", tuple(int(v) // 24 * 24 for v in color), inset, span, n, width)
    if key not in _GLOW:
        s, c, r = SIZE * SS, SIZE * SS / 2, ORB_R * SS
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        box = (c - r + inset * SS, c - r + inset * SS, c + r - inset * SS, c + r - inset * SS)
        for i in range(n):
            a = int(255 * (1 - i / n) ** 1.5)
            d.arc(box, -i * span - span, -i * span, fill=(*key[1], a), width=int(width * SS))
        img.info["key"] = key  # rotated copies are cached under the same key
        _GLOW[key] = img
    return _GLOW[key]


def _rotated(sprite: Image.Image, angle: float) -> Image.Image:
    """`sprite` turned by `angle`, from 60 pre-rotated copies (6 degree steps; rotating every frame cost ~3 ms)."""
    step = int(round((angle % 360) / 6)) % 60
    key = ("rot", sprite.info.get("key"), step)
    if key not in _GLOW:
        _GLOW[key] = sprite.rotate(step * 6, resample=Image.BICUBIC)
    return _GLOW[key]


def _body() -> Image.Image:
    """The dark glass sphere: a vertical gradient with a faint highlight - drawn once."""
    if "body" not in _BODY:
        s, r, c = SIZE * SS, ORB_R * SS, SIZE * SS // 2
        grad = Image.new("RGBA", (s, s))
        top, bottom = (24, 36, 64), (6, 10, 22)
        for y in range(s):
            f = y / (s - 1)
            ImageDraw.Draw(grad).line([(0, y), (s, y)], fill=tuple(int(a + (b - a) * f) for a, b in zip(top, bottom)) + (255,))
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).ellipse((c - r, c - r, c + r, c + r), fill=255)
        body = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        body.paste(grad, (0, 0), mask)
        hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(hl).ellipse((c - r * 0.6, c - r * 0.85, c + r * 0.6, c - r * 0.15), fill=(255, 255, 255, 28))
        body.alpha_composite(hl.filter(ImageFilter.GaussianBlur(3 * SS)))
        _BODY["body"] = body
    return _BODY["body"]


def _sphere(color) -> Image.Image:
    """A lit 3D glass sphere (2026-11-21: the owner wanted the icon 3D): per-pixel shading from a light at the top
    left - dark glass, soft diffuse light, a specular glint and a rim light in the state colour. Drawn once per
    colour (rounded so easing reuses entries), ~15 ms; frames just paste it."""
    key = ("sphere", tuple(int(v) // 24 * 24 for v in color))
    if key not in _BODY:
        import numpy as np

        s, r = SIZE * SS, ORB_R * SS
        ys, xs = np.mgrid[0:s, 0:s].astype(np.float32)
        x, y = (xs - s / 2 + 0.5) / r, (ys - s / 2 + 0.5) / r
        rr = x * x + y * y
        inside = rr <= 1.0
        z = np.sqrt(np.clip(1.0 - rr, 0, 1))
        light = np.array([-0.45, -0.6, 0.66], np.float32)
        light /= np.linalg.norm(light)
        diffuse = np.clip(x * light[0] + y * light[1] + z * light[2], 0, 1)
        half = light + np.array([0, 0, 1], np.float32)
        half /= np.linalg.norm(half)
        spec = np.clip(x * half[0] + y * half[1] + z * half[2], 0, 1) ** 40
        rim = (1 - z) ** 2.6
        tint = np.array(key[1], np.float32)
        base = np.array([8, 12, 26], np.float32)
        rgb = (base[None, None] + diffuse[..., None] * np.array([30, 44, 78], np.float32)
               + rim[..., None] * tint * 0.85 + spec[..., None] * 255 * 0.85)
        # a soft edge (1.5 px) so the outline is smooth after downscaling
        edge = np.clip((1.0 - np.sqrt(rr)) * r / (1.5 * SS), 0, 1)
        alpha = np.where(inside, edge * 255, 0)
        arr = np.dstack([np.clip(rgb, 0, 255), alpha]).astype(np.uint8)
        _BODY[key] = Image.fromarray(arr, "RGBA")
        if len(_BODY) > 40:
            _BODY.clear()
            _BODY[key] = Image.fromarray(arr, "RGBA")
    return _BODY[key]


# Two rings orbiting the sphere in 3D: (radius factor, tilt about x in degrees, tilt about z, spin speed, precession speed)
RINGS = ((1.42, 72.0, 18.0, 1.0, 0.18), (1.25, -60.0, -28.0, -1.35, -0.12))
RING_SEGMENTS = 56


def _ring_points(t: float, ring: tuple, speed: float):
    """Points (x, y, depth, along) of a tilted ring, rotated over time; along is 0..1 from its bright head."""
    radius, tilt_x, tilt_z, spin, precess = ring
    cx, cz = math.radians(tilt_x), math.radians(tilt_z)
    ph, pr = t * spin * speed, t * precess * speed
    out = []
    for i in range(RING_SEGMENTS + 1):
        th = 2 * math.pi * i / RING_SEGMENTS
        x0, y0 = math.cos(th + ph) * radius, math.sin(th + ph) * radius
        y1, z1 = y0 * math.cos(cx), y0 * math.sin(cx)  # tilt about x
        x2, y2 = x0 * math.cos(cz) - y1 * math.sin(cz), x0 * math.sin(cz) + y1 * math.cos(cz)  # tilt about z
        x3, z3 = x2 * math.cos(pr) + z1 * math.sin(pr), -x2 * math.sin(pr) + z1 * math.cos(pr)  # slow precession about y
        out.append((x3, y2, z3, i / RING_SEGMENTS))
    return out


def _draw_rings(d: ImageDraw.ImageDraw, t: float, col, speed: float, front: bool) -> None:
    """The rings' halves in front of (front=True) or behind the sphere; brighter towards each ring's head."""
    s, r = SIZE * SS, ORB_R * SS
    c = s / 2
    for ring in RINGS:
        pts = _ring_points(t, ring, speed)
        for (xa, ya, za, fa), (xb, yb, zb, _fb) in zip(pts, pts[1:]):
            depth = (za + zb) / 2
            if (depth >= 0) != front:
                continue
            if not front and (xa * xa + ya * ya) < 1.0:  # behind the sphere: hidden
                continue
            head = (1 - fa) ** 1.4  # bright head, fading tail: the spin is visible
            a = int((70 + 170 * head) * (1.0 if front else 0.45))
            w = (1.1 + 1.0 * head) * SS * (1.0 if front else 0.75)
            d.line([(c + xa * r, c + ya * r), (c + xb * r, c + yb * r)], fill=(*col, a), width=max(1, int(w)))


def _draw_particles(d: ImageDraw.ImageDraw, t: float, col, speed: float, front: bool) -> None:
    """Three small lights orbiting at different heights, sized and dimmed by depth."""
    s, r = SIZE * SS, ORB_R * SS
    c = s / 2
    for k, (rad, incl, sp) in enumerate(((1.6, 0.35, 0.9), (1.3, -0.55, -1.2), (1.5, 0.9, 0.7))):
        a = t * sp * speed + k * 2.1
        x, z = math.cos(a) * rad, math.sin(a) * rad
        y = z * math.sin(incl) * 0.6 + math.sin(a * 0.5 + k) * 0.15
        if (z >= 0) != front or (not front and x * x + y * y < 1.0):
            continue
        size = (1.2 + 0.9 * (z / rad + 1) / 2) * SS
        alpha = int(120 + 120 * (z / rad + 1) / 2)
        px, py = c + x * r, c + y * r
        d.ellipse((px - size, py - size, px + size, py + size), fill=(*[min(255, v + 70) for v in col], alpha))


def ring_speed(state: str) -> float:
    return {"thinking": 3.2, "executing": 3.0, "transcribing": 2.6, "speaking": 2.0, "recording": 1.8, "listening": 1.8,
            "following": 1.3, "loading": 2.0, "offline": 0.0, "error": 0.6}.get(state, 0.7)


def render(o: OrbState) -> Image.Image:
    """One frame (RGBA, SIZE x SIZE) for the given state and time."""
    if len(_GLOW) > 300:  # ~25 MB at most; cleared layers are rebuilt on demand in a few frames
        _GLOW.clear()
    s, c, r, t = SIZE * SS, SIZE * SS / 2, ORB_R * SS, o.t
    col = tuple(int(v) for v in o.color)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    active = o.state not in ("offline",)
    speed = ring_speed(o.state)
    # glow: breathing when idle, stronger and quicker when busy
    if active:
        speed, lo, hi = {"recording": (2.2, 0.55, 1.0), "listening": (2.2, 0.55, 1.0), "speaking": (3.0, 0.5, 0.95),
                         "thinking": (1.2, 0.45, 0.8), "executing": (1.4, 0.5, 0.85), "transcribing": (1.6, 0.5, 0.85),
                         "error": (1.0, 0.4, 0.9), "following": (1.0, 0.3, 0.6)}.get(o.state, (0.55, 0.18, 0.4))
        k = lo + (hi - lo) * (0.5 + 0.5 * math.sin(t * speed * math.pi))
        img.alpha_composite(_glow_at(col, k))
    d = ImageDraw.Draw(img)
    # ripples while listening; soft waves while speaking
    if o.state in ("recording", "listening", "speaking", "following"):
        period = {"speaking": 0.9, "following": 2.2}.get(o.state, 1.3)  # following: slow, calm ripples
        for i in range(2):
            ph = ((t / period) + i / 2) % 1.0
            rr = r + (4 + 14 * ph) * SS
            a = int(150 * (1 - ph) ** 1.6)
            d.ellipse((c - rr, c - rr, c + rr, c + rr), outline=(*col, a), width=int(1.6 * SS))
    # 3D: the back halves of the rings and particles, then the lit sphere, then the front halves
    _draw_rings(d, t, col, speed, front=False)
    _draw_particles(d, t, col, speed, front=False)
    img.alpha_composite(_sphere(col))
    d = ImageDraw.Draw(img)
    # a thin rim in the state colour keeps the state readable at a glance
    d.ellipse((c - r, c - r, c + r, c + r), outline=(*col, 150 if active else 90), width=int(1.2 * SS))
    _draw_rings(d, t, col, speed, front=True)
    _draw_particles(d, t, col, speed, front=True)
    # thinking / working: a comet arc running round the rim
    if o.state in ("thinking", "executing", "transcribing", "loading"):
        img.alpha_composite(_rotated(_comet(col, inset=4, span=7, n=18, width=2.6), -(t * 300)))
        d = ImageDraw.Draw(img)
    # core: a glowing dot, or voice bars while speaking
    if o.state == "speaking":
        for i, phase in enumerate((0.0, 1.7, 3.1)):
            h = (4 + 7 * (0.5 + 0.5 * math.sin(t * 9 + phase))) * SS
            x = c + (i - 1) * 5 * SS
            d.rounded_rectangle((x - 1.5 * SS, c - h, x + 1.5 * SS, c + h), radius=1.5 * SS, fill=(*col, 255))
    else:
        pulse = 1 + (0.25 * math.sin(t * 6) if o.state in ("recording", "listening") else 0.06 * math.sin(t * 2))
        cr = 5.5 * SS * pulse
        img.alpha_composite(_core_glow(col, cr, active))
        d = ImageDraw.Draw(img)
        d.ellipse((c - cr, c - cr, c + cr, c + cr), fill=(*[min(255, v + 60) for v in col], 255 if active else 150))
    # a running timer: a thin bright ring just outside the rim, shrinking clockwise from the top as time runs out
    if o.timer is not None:
        rr = r + 3.2 * SS
        d.arc((c - rr, c - rr, c + rr, c + rr), -90, -90 + 360 * max(0.004, o.timer), fill=(226, 232, 240, 230), width=int(1.6 * SS))
    # an action waiting for approval: an amber arc sweeping round the outside, and a badge
    if o.approval:
        img.alpha_composite(_rotated(_comet(AMBER, inset=-4, span=6, n=14, width=2.4), t * 220))
        d = ImageDraw.Draw(img)
        bx, by, br = c + r * 0.72, c - r * 0.72, 5 * SS
        d.ellipse((bx - br, by - br, bx + br, by + br), fill=(*AMBER, 255), outline=(10, 14, 26, 255), width=SS)
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
