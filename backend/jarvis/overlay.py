"""The floating JARVIS icon: a small always-on-top circle on the screen edge.

    python -m jarvis.overlay [--parent PID]      (the desktop app starts it; it exits when that process ends)

The circle's colour shows the state (cyan ready, green listening, amber thinking, orange working, blue speaking, red
error, grey when JARVIS is not reachable).
- Hover (or click to keep it open): a compact panel that fades in - one status line, the last request and reply,
  a "Last 5" drop-down, a command box, and Talk / Window.
- An action waiting for approval shows by itself, without hovering: a small bar under the circle with what it is,
  a moving progress line, and Allow / Deny.
Drag the circle to move it; the position is remembered.

A separate process with tkinter (part of Python): it only talks to the local API, polling /api/overlay twice a second.
Widgets are built once and only their text changes, so nothing jumps or flickers while it is open.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request

from .config import load_settings
from .orb import COLORS as ORB_COLORS
from .orb import SIZE, LayeredWindow, OrbState, ease_color, render

ORB = SIZE  # the icon window (the orb itself is smaller; the rest is room for its glow)
PANEL_W = 300
BAR_W = 250
KEY = "#010203"  # transparent colour around the circle
BG, CARD, FG, MUTED, LINE, ACCENT, AMBER = "#0b1220", "#131c30", "#e2e8f0", "#8b98ad", "#1f2a44", "#22d3ee", "#f59e0b"
COLORS = {"idle": "#22d3ee", "waiting": "#22d3ee", "listening": "#22c55e", "recording": "#22c55e", "transcribing": "#a3e635",
          "thinking": "#f59e0b", "executing": "#fb923c", "speaking": "#60a5fa", "error": "#ef4444", "offline": "#64748b", "loading": "#94a3b8", "following": "#4ade80"}
LABELS = {"idle": "Ready", "waiting": "Say “Jarvis”", "recording": "Listening…", "transcribing": "Understanding…",
          "thinking": "Thinking…", "executing": "Working…", "speaking": "Speaking", "error": "Something went wrong", "offline": "Not running",
          "loading": "Loading speech model…", "listening": "Listening…", "following": "Listening — just keep talking"}
FONT = ("Segoe UI", 9)
SMALL = ("Segoe UI", 8)
BOLD = ("Segoe UI Semibold", 9)
FADE_STEP, FADE_MS, PANEL_ALPHA = 0.09, 12, 0.97  # ~0.13 s fade


def elide(text: str, n: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


class Api:
    def __init__(self, port: int, token: str):
        self.base, self.token = f"http://127.0.0.1:{port}", token

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 5):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")


def work_area() -> tuple[int, int, int, int] | None:
    try:
        from .desktop_app import work_area as wa

        return wa()
    except Exception:  # noqa: BLE001
        return None


class Fader:
    """Fades a Toplevel in and out with its window alpha (Windows supports -alpha on toplevels)."""

    def __init__(self, win: tk.Toplevel, top: float = PANEL_ALPHA, on_step=None):
        self.win, self.top, self.alpha, self.target, self._job = win, top, 0.0, 0.0, None
        self.on_step = on_step  # called with 0..1 as it fades, to slide the window along with the fade
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

    @property
    def shown(self) -> bool:
        return self.target > 0

    def show(self) -> None:
        if self.target == self.top:
            return
        self.target = self.top
        self.win.deiconify()
        self._step()

    def hide(self) -> None:
        if self.target == 0:
            return
        self.target = 0.0
        self._step()

    def _step(self) -> None:
        if self._job:
            self.win.after_cancel(self._job)
            self._job = None
        diff = self.target - self.alpha
        if abs(diff) <= FADE_STEP:
            self.alpha = self.target
        else:
            self.alpha += FADE_STEP if diff > 0 else -FADE_STEP
        try:
            self.win.attributes("-alpha", self.alpha)
        except tk.TclError:
            return
        if self.on_step:
            self.on_step(self.alpha / self.top)
        if self.alpha != self.target:
            self._job = self.win.after(FADE_MS, self._step)
        elif self.alpha == 0:
            self.win.withdraw()


def rounded_image(w: int, h: int, r: int, fill: str, bg: str):
    """An anti-aliased rounded rectangle (Tk's own canvas shapes have jagged corners), drawn at 3x and scaled down."""
    from PIL import Image, ImageDraw, ImageTk

    s = 3
    img = Image.new("RGB", (max(1, w) * s, max(1, h) * s), bg)
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w * s - 1, h * s - 1), radius=r * s, fill=fill)
    return ImageTk.PhotoImage(img.resize((max(1, w), max(1, h)), Image.LANCZOS))


class Pill(tk.Canvas):
    """A rounded button: text on an anti-aliased pill, lighter while hovered."""

    def __init__(self, parent, text: str, cmd, bg: str = "#1e293b", fg: str = FG, font=SMALL, padx: int = 10, pady: int = 4):
        import tkinter.font as tkfont

        f = tkfont.Font(font=font)
        w, h = f.measure(text) + 2 * padx, f.metrics("linespace") + 2 * pady
        parent_bg = parent.cget("bg")
        super().__init__(parent, width=w, height=h, bg=parent_bg, highlightthickness=0, bd=0, cursor="hand2")
        self._imgs = (rounded_image(w, h, h // 2, bg, parent_bg), rounded_image(w, h, h // 2, _lighter(bg), parent_bg))
        self._bgitem = self.create_image(0, 0, image=self._imgs[0], anchor="nw")
        self.create_text(w // 2, h // 2, text=text, fill=fg, font=font)
        self.bind("<Button-1>", lambda _e: cmd())
        self.bind("<Enter>", lambda _e: self.itemconfigure(self._bgitem, image=self._imgs[1]))
        self.bind("<Leave>", lambda _e: self.itemconfigure(self._bgitem, image=self._imgs[0]))


class RoundBox(tk.Canvas):
    """A rounded card holding other widgets (in `.inner`); it follows the height of what it holds."""

    def __init__(self, parent, width: int, fill: str = CARD, radius: int = 10, pad: int = 8):
        parent_bg = parent.cget("bg")
        super().__init__(parent, width=width, height=2 * pad, bg=parent_bg, highlightthickness=0, bd=0)
        self.w, self.fill, self.radius, self.pad, self.parent_bg, self._size = width, fill, radius, pad, parent_bg, None
        self._bg = self.create_image(0, 0, anchor="nw")
        self.inner = tk.Frame(self, bg=fill)
        self.create_window(pad, pad, window=self.inner, anchor="nw", width=width - 2 * pad)
        self.inner.bind("<Configure>", lambda _e: self._fit())

    def _fit(self):
        h = self.inner.winfo_reqheight() + 2 * self.pad
        if self._size != h:
            self._size = h
            self._img = rounded_image(self.w, h, self.radius, self.fill, self.parent_bg)
            self.itemconfigure(self._bg, image=self._img)
            self.configure(height=h)


def round_window(win: tk.Toplevel, border: str) -> None:
    """Windows 11 rounds the window's corners and draws a thin coloured border (no-op elsewhere)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        dwm = ctypes.windll.dwmapi
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33, ctypes.byref(pref), 4)  # DWMWA_WINDOW_CORNER_PREFERENCE
        r, g, b = (int(border[i:i + 2], 16) for i in (1, 3, 5))
        color = ctypes.c_uint(r | (g << 8) | (b << 16))  # COLORREF
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 34, ctypes.byref(color), 4)  # DWMWA_BORDER_COLOR
    except Exception:  # noqa: BLE001 - square corners are fine
        pass


class Overlay:
    def __init__(self, api: Api, settings, parent_pid: int | None = None):
        self.api, self.settings, self.parent_pid = api, settings, parent_pid
        self.data: dict | None = None
        self.updates: queue.Queue = queue.Queue()
        self.pinned = False
        self.history_open = False
        self.outside_since: float | None = None
        self.hover_since: float | None = None
        self.conversation: str | None = None
        self.phase = 0.0
        self.pos_file = settings.data_dir / "overlay.json"
        self._texts: dict[str, str] = {}

        self.root = tk.Tk()
        self.root.title("JARVIS icon")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY)
        self.canvas = tk.Canvas(self.root, width=ORB, height=ORB, bg=KEY, highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        x, y = self._start_position()
        self.root.geometry(f"{ORB}x{ORB}+{x}+{y}")
        self.root.update_idletasks()
        # The smooth orb (orb.py) on a per-pixel-alpha window; the old flat canvas drawing is the fallback.
        self.layered = None
        self.orb = OrbState()
        try:
            if os.name == "nt":
                self.layered = LayeredWindow(int(self.root.wm_frame(), 16))
        except Exception:  # noqa: BLE001
            self.layered = None
        if not self.layered:
            try:
                self.root.attributes("-transparentcolor", KEY)
            except tk.TclError:
                pass
        self._last = time.time()
        self._frame = 0
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", lambda _e: self._talk())
        self._build_panel()
        self._build_approval_bar()
        threading.Thread(target=self._poll, daemon=True, name="overlay-poll").start()
        self.root.after(100, self._tick)

    # -- position -----------------------------------------------------------------------------------------------------
    def _start_position(self) -> tuple[int, int]:
        try:
            p = json.loads(self.pos_file.read_text())
            return int(p["x"]), int(p["y"])
        except (OSError, ValueError, KeyError):
            pass
        x, y, w, h = work_area() or (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 40)
        return x + w - ORB - 6, y + h - ORB - 110

    def _press(self, e):
        self._drag_start = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())
        self._moved = False

    def _drag(self, e):
        sx, sy, wx, wy = self._drag_start
        dx, dy = e.x_root - sx, e.y_root - sy
        if abs(dx) + abs(dy) > 3:
            self._moved = True
            self.root.geometry(f"+{wx + dx}+{wy + dy}")
            self._place_panel()
            self._place_bar()

    def _release(self, _e):
        if self._moved:
            try:
                self.pos_file.write_text(json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y()}))
            except OSError:
                pass
        elif self.panel_fade.shown:  # a click just opens/closes it; only the pin keeps it open (clicks used to pin it,
            self.pinned = False      # so it stayed after the pointer left - the owner's "it stays for a long time")
            self._pin_color()
            self._hide_panel()
        else:
            self._show_panel()

    def _area(self) -> tuple[int, int, int, int]:
        return work_area() or (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())

    def _geometry(self, win: tk.Toplevel, geo: str) -> None:
        """Move/resize only when something changed (setting it every poll makes Windows redraw the frame)."""
        if self._texts.get(f"geo{id(win)}") != geo:
            self._texts[f"geo{id(win)}"] = geo
            win.geometry(geo)

    # -- widgets helpers --------------------------------------------------------------------------------------------------
    def _set_text(self, key: str, widget: tk.Widget, text: str, **extra) -> None:
        """Change a label only when its text changed: no flicker, no relayout every poll."""
        if self._texts.get(key) != text or extra:
            self._texts[key] = text
            widget.configure(text=text, **extra)

    def _button(self, parent, text: str, cmd, bg: str = "#1e293b", fg: str = FG, font=SMALL) -> Pill:
        return Pill(parent, text, cmd, bg=bg, fg=fg, font=font)

    # -- the hover panel ----------------------------------------------------------------------------------------------------
    def _build_panel(self):
        p = self.panel = tk.Toplevel(self.root)
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        p.configure(bg=BG, highlightthickness=0)
        p.withdraw()
        round_window(p, LINE)
        self.panel_fade = Fader(p, on_step=lambda f: self._place_panel(slide=(1 - f) ** 2 * 18))  # eased slide

        head = tk.Frame(p, bg=BG)
        head.pack(fill="x", padx=10, pady=(8, 0))
        self.dot = tk.Canvas(head, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot.pack(side="left", pady=(2, 0))
        self.status = tk.Label(head, text="", font=BOLD, fg=FG, bg=BG, anchor="w")
        self.status.pack(side="left", padx=(6, 0))
        self.pin = tk.Label(head, text="📌", font=SMALL, fg=MUTED, bg=BG, cursor="hand2")
        self.pin.pack(side="right")
        self.pin.bind("<Button-1>", lambda _e: self._toggle_pin())
        self.meta = tk.Label(p, text="", font=SMALL, fg=MUTED, bg=BG, anchor="w")
        self.meta.pack(fill="x", padx=10)
        self.timer_row = tk.Label(p, text="", font=BOLD, fg=ACCENT, bg=BG, anchor="w")  # live countdowns; hidden when none

        card = RoundBox(p, PANEL_W - 20, fill=CARD, radius=10, pad=9)
        card.pack(padx=10, pady=(6, 0))
        self.you = tk.Label(card.inner, text="", font=SMALL, fg=MUTED, bg=CARD, anchor="w", justify="left")
        self.you.pack(fill="x")
        self.reply = tk.Label(card.inner, text="", font=FONT, fg=FG, bg=CARD, anchor="w", justify="left", wraplength=PANEL_W - 44)
        self.reply.pack(fill="x", pady=(2, 0))

        self.hist_toggle = tk.Label(p, text="▸ Last 5", font=SMALL, fg=ACCENT, bg=BG, anchor="w", cursor="hand2")
        self.hist_toggle.pack(fill="x", padx=10, pady=(4, 0))
        self.hist_toggle.bind("<Button-1>", lambda _e: self._toggle_history())
        self.hist = tk.Frame(p, bg=BG)
        self.hist_rows = []
        for _ in range(5):
            row = tk.Label(self.hist, text="", font=SMALL, fg=MUTED, bg=BG, anchor="w", justify="left", wraplength=PANEL_W - 24)
            row.pack(fill="x", pady=(0, 3))
            self.hist_rows.append(row)

        self.box = tk.Frame(p, bg=BG)
        self.box.pack(fill="x", padx=10, pady=(6, 0))
        field = RoundBox(self.box, PANEL_W - 20 - 40, fill=CARD, radius=14, pad=6)
        field.pack(side="left")
        self.entry = tk.Entry(field.inner, font=FONT, bg=CARD, fg=FG, insertbackground=FG, relief="flat", highlightthickness=0, bd=0)
        self.entry.pack(fill="x", ipady=2, padx=4)
        self.entry.bind("<Return>", lambda _e: self._send())
        self._button(self.box, "➤", self._send, bg="#0e7490").pack(side="right")

        row = tk.Frame(p, bg=BG)
        row.pack(fill="x", padx=10, pady=(7, 10))
        self._button(row, "🎤 Talk", self._talk).pack(side="left")
        self._button(row, "Window", self._open_window).pack(side="left", padx=5)
        self.note = tk.Label(row, text="", font=SMALL, fg=MUTED, bg=BG, anchor="e")
        self.note.pack(side="right")
        p.bind("<Enter>", lambda _e: setattr(self, "outside_since", None))

    def _toggle_pin(self):
        self.pinned = not self.pinned
        self._pin_color()

    def _pin_color(self):
        self.pin.configure(fg=ACCENT if self.pinned else MUTED)

    def _on_orb(self) -> bool:
        """The pointer is over the visible orb (a circle), not just the square window around its glow."""
        try:
            px, py = self.root.winfo_pointerxy()
            cx, cy = self.root.winfo_rootx() + ORB / 2, self.root.winfo_rooty() + ORB / 2
        except tk.TclError:
            return False
        return (px - cx) ** 2 + (py - cy) ** 2 <= 27 ** 2

    def _toggle_history(self):
        self.history_open = not self.history_open
        self.hist_toggle.configure(text=("▾ Last 5" if self.history_open else "▸ Last 5"))
        if self.history_open:
            self.hist.pack(fill="x", padx=10, pady=(2, 0), before=self.box)
        else:
            self.hist.pack_forget()
        self._place_panel()

    def _show_panel(self):
        self.outside_since = None
        self._render()
        self._place_panel()
        self.panel_fade.show()
        self.pin.configure(fg=ACCENT if self.pinned else MUTED)

    def _hide_panel(self):
        self.panel_fade.hide()

    def _place_panel(self, slide: float = 0.0):
        """Beside the orb (left of it, or right near the screen's left edge); `slide` shifts it while it fades in."""
        if not self.panel_fade.shown:
            return
        self.panel.update_idletasks()
        h = self.panel.winfo_reqheight()
        ox, oy = self.root.winfo_x(), self.root.winfo_y()
        ax, ay, aw, ah = self._area()
        pad = ORB // 2 - 24  # the transparent margin around the visible orb
        left = ox + pad - PANEL_W - 6
        x = int(left + slide) if left >= ax else int(ox + ORB - pad + 6 - slide)
        y = max(ay + 4, min(oy + ORB - pad - h, ay + ah - h - 4))
        self._geometry(self.panel, f"{PANEL_W}x{h}+{x}+{y}")

    def _pointer_in(self, w, margin: int = 10) -> bool:
        try:
            if not w.winfo_viewable():
                return False
            px, py = w.winfo_pointerxy()
            x, y, ww, hh = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
        except tk.TclError:
            return False
        return x - margin <= px <= x + ww + margin and y - margin <= py <= y + hh + margin

    # -- the approval bar (no hover needed) ----------------------------------------------------------------------------------
    def _build_approval_bar(self):
        b = self.bar = tk.Toplevel(self.root)
        b.overrideredirect(True)
        b.attributes("-topmost", True)
        b.configure(bg=BG, highlightthickness=0)
        b.withdraw()
        round_window(b, AMBER)
        self.bar_fade = Fader(b, top=0.98, on_step=lambda f: self._place_bar(slide=(1 - f) ** 2 * -10))
        self.bar_title = tk.Label(b, text="", font=BOLD, fg=AMBER, bg=BG, anchor="w")
        self.bar_title.pack(fill="x", padx=8, pady=(6, 0))
        self.bar_text = tk.Label(b, text="", font=SMALL, fg=FG, bg=BG, anchor="w", justify="left", wraplength=BAR_W - 16)
        self.bar_text.pack(fill="x", padx=8)
        self.bar_line = tk.Canvas(b, width=BAR_W - 16, height=3, bg=LINE, highlightthickness=0)
        self.bar_line.pack(padx=8, pady=(5, 5))
        row = tk.Frame(b, bg=BG)
        row.pack(fill="x", padx=8, pady=(0, 7))
        self._button(row, "✓ Allow", lambda: self._decide_current(True), bg="#166534").pack(side="left")
        self._button(row, "✕ Deny", lambda: self._decide_current(False), bg="#7f1d1d").pack(side="left", padx=6)
        self.bar_count = tk.Label(row, text="", font=SMALL, fg=MUTED, bg=BG)
        self.bar_count.pack(side="right")
        self.current_approval: str | None = None
        self.deciding: set[str] = set()

    def _place_bar(self, slide: float = 0.0):
        if not self.bar_fade.shown:
            return
        self.bar.update_idletasks()
        h = self.bar.winfo_reqheight()
        ox, oy = self.root.winfo_x(), self.root.winfo_y()
        ax, ay, aw, ah = self._area()
        pad = ORB // 2 - 24
        x = max(ax + 4, min(ox + ORB // 2 - BAR_W // 2, ax + aw - BAR_W - 4))
        below = oy + ORB - pad + 4
        y = int(below + slide) if below + h <= ay + ah else int(oy + pad - h - 4 - slide)  # under the orb, or above it near the bottom
        self._geometry(self.bar, f"{BAR_W}x{h}+{x}+{y}")

    def _render_bar(self, approvals: list[dict]):
        waiting = [a for a in approvals if a["id"] not in self.deciding]
        if not waiting:
            self.current_approval = None
            self.bar_fade.hide()
            return
        ap = waiting[0]
        self.current_approval = ap["id"]
        self._set_text("bar_title", self.bar_title, "Allow " + ap["tool"].replace("_", " ") + "?")
        self._set_text("bar_text", self.bar_text, elide(ap["preview"].splitlines()[0] if ap["preview"] else "", 110))
        self._set_text("bar_count", self.bar_count, f"1 of {len(waiting)}" if len(waiting) > 1 else "")
        if not self.bar_fade.shown:
            self.bar_fade.show()
        self._place_bar()

    def _draw_bar_line(self):
        """An indeterminate progress line: a soft segment sliding back and forth while the action waits."""
        if not self.bar_fade.shown:
            return
        c, w = self.bar_line, BAR_W - 16
        c.delete("all")
        seg = w * 0.32
        x = (math.sin(self.phase * 0.9) * 0.5 + 0.5) * (w - seg)
        c.create_rectangle(x, 0, x + seg, 3, fill=AMBER, outline="")

    # -- data -----------------------------------------------------------------------------------------------------------
    def _poll(self):
        misses = 0
        while True:
            try:
                self.updates.put(self.api.call("GET", "/api/overlay", timeout=4))
                misses = 0
            except (urllib.error.URLError, OSError, ValueError):
                misses += 1
                self.updates.put(None)
            if self.parent_pid and misses and not _alive(self.parent_pid):
                self.updates.put("quit")
                return
            time.sleep(0.5)

    def _state(self) -> str:
        d = self.data
        if not d:
            return "offline"
        v = d.get("voice") or {}
        if v.get("state") in ("recording", "transcribing", "following"):
            return v["state"]
        if d.get("state") not in (None, "idle"):
            return d["state"]
        if v.get("state") in ("loading", "error"):
            return v["state"]
        return "waiting" if v.get("running") else "idle"

    def _tick(self):
        try:
            while True:
                item = self.updates.get_nowait()
                if item == "quit":
                    self.root.destroy()
                    return
                self.data = item
                if self.panel_fade.shown:
                    self._render()
                self._render_bar((item or {}).get("approvals") or [])
        except queue.Empty:
            pass
        self.phase += 0.12
        self._draw()
        self._draw_bar_line()
        self._hover()
        self.root.after(16 if self.layered else 40, self._tick)

    def _hover(self):
        """Open after a short, deliberate hover; close shortly after the pointer leaves (not while typing)."""
        on_orb = self._on_orb()
        if not self.panel_fade.shown:
            if on_orb:
                self.hover_since = self.hover_since or time.time()
                if time.time() - self.hover_since > 0.15:
                    self._show_panel()
            else:
                self.hover_since = None
            return
        if self.pinned:
            return
        inside = on_orb or self._pointer_in(self.panel, margin=4) or self._pointer_in(self.bar, margin=4)
        # half-typed text keeps it open only while the pointer is still near it
        typing = self.root.focus_get() is self.entry and self.entry.get().strip() and self._pointer_in(self.panel, margin=80)
        if inside or typing:
            self.outside_since = None
        elif self.outside_since is None:
            self.outside_since = time.time()
        elif time.time() - self.outside_since > 0.25:
            self.hover_since = None
            self._hide_panel()

    def _draw(self):
        if self.layered:
            return self._draw_orb()
        state = self._state()
        color = COLORS.get(state, COLORS["idle"])
        c = self.canvas
        c.delete("all")
        cx = cy = ORB / 2
        r = ORB / 2 - 5
        waiting = bool(self.data and self.data.get("approvals"))
        if state in ("recording", "listening", "speaking"):
            grow = 2 + 2 * math.sin(self.phase * 1.6)
            c.create_oval(cx - r - grow, cy - r - grow, cx + r + grow, cy + r + grow, outline=color, width=1)
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#0b1220", outline=color, width=3)
        if state in ("thinking", "executing", "transcribing", "loading"):
            c.create_arc(cx - r + 5, cy - r + 5, cx + r - 5, cy + r - 5, start=(self.phase * 120) % 360, extent=100, style="arc", outline=color, width=3)
        if waiting:  # an amber arc sweeping round the rim: something needs you
            c.create_arc(cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3, start=(-self.phase * 90) % 360, extent=70, style="arc", outline=AMBER, width=3)
        inner = 6 + (2 * abs(math.sin(self.phase * 2.2)) if state in ("recording", "speaking") else 0)
        c.create_oval(cx - inner, cy - inner, cx + inner, cy + inner, fill=color, outline="")

    def _timer_text(self) -> str:
        now, parts = time.time(), []
        for t in (self.data or {}).get("timers") or []:
            if t["kind"] == "timer":
                left = max(0, int(t["due"] - now))
                h, rem = divmod(left, 3600)
                clock = f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"
                parts.append(f"⏱ {t['label'] + ' ' if t['label'] else ''}{clock}")
            else:
                parts.append("⏰ " + time.strftime("%I:%M %p", time.localtime(t["due"])).lstrip("0") + (f" {t['label']}" if t["label"] else ""))
        return "   ·   ".join(parts[:3])

    def _render_timers(self):
        text = self._timer_text()
        if text and not self.timer_row.winfo_manager():
            self.timer_row.pack(fill="x", padx=10, pady=(2, 0), after=self.meta)
        elif not text and self.timer_row.winfo_manager():
            self.timer_row.pack_forget()
        self._set_text("timers", self.timer_row, text)

    def _nearest_timer(self) -> float | None:
        """Fraction of the soonest countdown still to go (for the ring round the orb), or None."""
        running = [t for t in (self.data or {}).get("timers") or [] if t["kind"] == "timer" and t.get("seconds")]
        if not running:
            return None
        soonest = min(running, key=lambda t: t["due"])
        return max(0.0, min(1.0, (soonest["due"] - time.time()) / soonest["seconds"]))

    def _draw_orb(self):
        """Smooth orb frames: ~30 fps while something is happening, ~15 fps at rest; time-based, so a late frame
        never makes the motion stutter."""
        now = time.time()
        state = self._state()
        approval = bool(self.data and self.data.get("approvals"))
        resting = state in ("waiting", "idle", "offline") and not approval and not (self.data or {}).get("timers")
        if now - self._last < (1 / 15 if resting else 1 / 30):
            return
        self.orb.t += min(0.2, now - self._last)
        self._last = now
        self.orb.state, self.orb.approval = state, approval
        self.orb.timer = self._nearest_timer()
        if self.panel_fade.shown and self.orb.timer is not None:
            self._render_timers()  # the countdown ticks every second, not only when new data arrives
        self.orb.color = ease_color(self.orb.color, ORB_COLORS.get(state, ORB_COLORS["idle"]), 0.16)
        try:
            self.layered.show(render(self.orb))
        except Exception:  # noqa: BLE001 - fall back to the flat drawing rather than lose the icon
            self.layered = None
            try:
                self.root.attributes("-transparentcolor", KEY)
            except tk.TclError:
                pass

    def _render(self):
        d = self.data
        state = self._state()
        color = COLORS.get(state, FG)
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 9, 9, fill=color, outline="")
        self._set_text("status", self.status, LABELS.get(state, state))
        if not d:
            self._set_text("meta", self.meta, "Start JARVIS from the desktop shortcut - this reconnects by itself.")
            return
        v = d.get("voice") or {}
        last = v.get("last") or {}
        bits = [] if v.get("running") else ["voice off" if not v.get("enabled") else (v.get("unavailable") or v.get("error") or "voice starting")]
        if d.get("detail"):
            bits.append(elide(d["detail"], 40))
        if last.get("seconds") is not None and last.get("heard"):
            bits.append(f"heard in {last['seconds']} s")
        elif last.get("note"):
            bits.append(elide(last["note"], 40))
        self._set_text("meta", self.meta, " · ".join(bits))
        self._render_timers()
        ex = d.get("last") or {}
        self._set_text("you", self.you, "You: " + elide(ex.get("request") or "-", 48))
        self._set_text("reply", self.reply, elide(ex.get("reply") or "-", 170))
        history = d.get("history") or []
        for i, row in enumerate(self.hist_rows):
            h = history[i] if i < len(history) else None
            txt = (time.strftime("%H:%M ", time.localtime(h["at"])) + elide(h["request"], 40) + "\n  → " + elide(h["reply"] or "…", 70)) if h else ""
            self._set_text(f"hist{i}", row, txt)
        if not self._texts.get("note_override"):
            self._set_text("note", self.note, "")
        self._place_panel()

    # -- actions --------------------------------------------------------------------------------------------------------
    def _flash(self, text: str):
        self._texts["note_override"] = "1"
        self.note.configure(text=text)
        self.root.after(2500, lambda: self._texts.pop("note_override", None))

    def _bg(self, fn, done_note: str = ""):
        def run():
            try:
                fn()
                if done_note:
                    self.root.after(0, lambda: self._flash(done_note))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        msg = json.loads(e.read()).get("detail", msg)
                    except ValueError:
                        pass
                self.root.after(0, lambda: self._flash(elide(msg, 40)))
        threading.Thread(target=run, daemon=True).start()

    def _send(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")

        def go():
            r = self.api.call("POST", "/api/chat", {"text": text, "conversation_id": self.conversation, "source": "text"})
            self.conversation = r.get("conversation_id", self.conversation)
        self._bg(go, "sent")

    def _talk(self):
        self._bg(lambda: self.api.call("POST", "/api/ui/listen"), "listening…")

    def _decide_current(self, approve: bool):
        aid = self.current_approval
        if not aid:
            return
        self.deciding.add(aid)  # hide it at once; the next poll shows the next one, if any
        self._render_bar((self.data or {}).get("approvals") or [])

        def go():
            try:
                self.api.call("POST", f"/api/approvals/{aid}/decide", {"approve": approve, "scope": "once"}, timeout=120)
            finally:
                self.root.after(3000, lambda: self.deciding.discard(aid))
        self._bg(go, "allowed" if approve else "denied")

    def _open_window(self):
        def go():
            from types import SimpleNamespace

            from . import desktop_app

            desktop_app.bring_up(SimpleNamespace(settings=self.settings))
        self._bg(go)

    def run(self):
        self.root.mainloop()


def _lighter(color: str) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(*(min(255, int(v + (255 - v) * 0.18)) for v in (r, g, b)))


def _alive(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001
        return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarvis.overlay")
    ap.add_argument("--parent", type=int, default=None, help="exit when this process (the JARVIS desktop app) ends")
    args = ap.parse_args(argv)
    s = load_settings()
    Overlay(Api(s.port, s.api_token()), s, args.parent).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
