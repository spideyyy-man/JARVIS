"""Desktop launcher: runs JARVIS as an app instead of a browser tab.

    python -m jarvis.desktop_app                    start server + app window + tray icon + global hotkey
    python -m jarvis.desktop_app --install-autostart   start with Windows (remove with --remove-autostart)

JARVIS runs in the background. It listens with the offline listener (listen.py: "Hey Jarvis", speech recognised on
this computer) and shows a small floating icon on the screen edge (overlay.py; hover for what it is doing). One
compact window (Edge/Chrome "app" mode) is opened minimised: it shows the conversation and speaks the replies.
Ctrl+Alt+J or the tray's "Talk to JARVIS" listens for one request without the wake word; --show keeps the window
open at start. Without the offline listener's packages the window's browser recogniser is used, as before.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

from .api import create_app
from .app import Jarvis

HOTKEY_LABEL = "Ctrl+Alt+J"


def find_browser() -> str | None:
    if platform.system() == "Windows":
        roots = [os.environ.get(v, "") for v in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData")]
        for root in filter(None, roots):
            for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                if (Path(root) / rel).is_file():
                    return str(Path(root) / rel)
    for name in ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return mac if Path(mac).is_file() else None


WINDOW_SIZE = (460, 760)  # a compact assistant panel; "Show all pages" in Settings makes it worth enlarging
MARGIN = 12
# Keep the page fully alive while minimised or covered: the wake word is heard by the page itself, and replies are
# spoken by it, possibly before the user has clicked anything in that window.
BACKGROUND_FLAGS = ["--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
                    "--autoplay-policy=no-user-gesture-required", "--disable-features=CalculateNativeWinOcclusion"]


def work_area() -> tuple[int, int, int, int] | None:
    """Usable screen rectangle (x, y, width, height) on Windows - the screen minus the taskbar."""
    if platform.system() != "Windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        pass
    return None


def window_command(browser: str, url: str, profile: Path, area: tuple[int, int, int, int] | None = None) -> list[str]:
    """The app window: a panel docked to the bottom-right of `area` (the screen minus the taskbar), never larger
    than it. (A fixed 1360x900 once ran 23 px off the bottom of a 1366x768 laptop screen.)"""
    w, h = WINDOW_SIZE
    geometry = [f"--window-size={w},{h}"]
    if area:
        x, y, aw, ah = area
        w, h = min(w, aw - 2 * MARGIN), min(h, ah - 2 * MARGIN)
        geometry = [f"--window-size={w},{h}", f"--window-position={x + aw - w - MARGIN},{y + ah - h - MARGIN}"]
    return [browser, f"--app={url}", f"--user-data-dir={profile}", *geometry, *BACKGROUND_FLAGS, "--no-first-run", "--no-default-browser-check"]


def app_url(jarvis: Jarvis, screen: str = "assistant") -> str:
    s = jarvis.settings
    return f"http://127.0.0.1:{s.port}/api/auth?token={s.api_token()}&next=/%23/{screen}"


def open_window(jarvis: Jarvis, screen: str = "assistant") -> bool:
    browser = find_browser()
    if not browser:
        import webbrowser

        return webbrowser.open(app_url(jarvis, screen))
    subprocess.Popen(window_command(browser, app_url(jarvis, screen), jarvis.settings.data_dir / "ui-profile", work_area()),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


# -- the one JARVIS window (Windows) ------------------------------------------------------------------------------
# Every window runs its own speech recogniser, so two windows would both answer "Jarvis". The tray and hotkey
# therefore bring the existing window back instead of opening another.
SW_RESTORE, SW_SHOWMINNOACTIVE, WM_CLOSE = 9, 7, 0x0010


def find_window(profile: Path) -> int | None:
    """Handle of the JARVIS app window: a visible top-level browser window whose process uses JARVIS's profile."""
    if platform.system() != "Windows":
        return None
    import ctypes
    from ctypes import wintypes

    import psutil

    user32, found = ctypes.windll.user32, []
    marker = str(profile).lower()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != "Chrome_WidgetWin_1":
            return True
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if "JARVIS" not in title.value:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            if marker in " ".join(psutil.Process(pid.value).cmdline()).lower():
                found.append(hwnd)
                return False
        except psutil.Error:
            pass
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


def wait_for_window(profile: Path, timeout: float = 20) -> int | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = find_window(profile)
        if hwnd:
            return hwnd
        time.sleep(0.3)
    return None


def show_window(hwnd: int) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def minimize_window(hwnd: int) -> None:
    import ctypes

    ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)  # to the taskbar, without stealing focus


def close_window(hwnd: int) -> None:
    import ctypes

    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def ask_window_to_listen(jarvis: Jarvis) -> bool:
    """Tell the open window to start listening now (no wake word). Goes through the server, which owns the event loop."""
    import urllib.request

    s = jarvis.settings
    req = urllib.request.Request(f"http://127.0.0.1:{s.port}/api/ui/listen", method="POST", data=b"", headers={"Authorization": f"Bearer {s.api_token()}"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def bring_up(jarvis: Jarvis, listen: bool = False) -> None:
    """Show the JARVIS window (opening it if it was closed); with `listen`, start listening straight away."""
    profile = jarvis.settings.data_dir / "ui-profile"
    hwnd = find_window(profile)
    if hwnd:
        show_window(hwnd)
        if listen:
            ask_window_to_listen(jarvis)
        return
    open_window(jarvis, "assistant/listen" if listen else "assistant")  # a new window starts listening once it has loaded


def window_watchdog(jarvis: Jarvis, profile: Path, every: float = 30, misses_allowed: int = 2) -> None:
    """Reopen the JARVIS window when it is open but its page has stopped talking to the server. Seen on the owner's
    laptop (2026-11-20): the minimised window stayed on the taskbar while its page was disconnected, so it showed and
    said nothing. A window the user closed is left closed (the server speaks replies itself)."""
    import ctypes

    misses = 0
    while True:
        time.sleep(every)
        hwnd = find_window(profile)
        if not hwnd or jarvis.bus._subscribers:
            misses = 0
            continue
        misses += 1
        if misses < misses_allowed:
            continue
        misses = 0
        minimised = bool(ctypes.windll.user32.IsIconic(hwnd))
        close_window(hwnd)
        time.sleep(3)
        open_window(jarvis, "assistant")
        if minimised:
            new = wait_for_window(profile)
            if new:
                time.sleep(2)
                minimize_window(new)
        jarvis.bus.activity.append({"ts": time.time(), "text": "Window reopened (its page had lost the connection)"})


def already_running(jarvis: Jarvis) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{jarvis.settings.port}/api/health", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def start_server(jarvis: Jarvis) -> tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(uvicorn.Config(create_app(jarvis), host=jarvis.settings.host, port=jarvis.settings.port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True, name="jarvis-server")
    thread.start()
    deadline = time.time() + 30
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError(f"JARVIS server did not start on port {jarvis.settings.port} (is it already running?)")
    return server, thread


def pythonw(python: str) -> str:
    """The console-less interpreter next to `python` on Windows (no black window flashing up), else `python`."""
    if python.lower().endswith("python.exe"):
        candidate = python[: -len("python.exe")] + "pythonw.exe"
        if Path(candidate).is_file():
            return candidate
    return python


def start_overlay() -> subprocess.Popen | None:
    """The floating icon, as its own process (tkinter wants its own main thread); it exits when this process ends."""
    try:
        return subprocess.Popen([pythonw(sys.executable), "-m", "jarvis.overlay", "--parent", str(os.getpid())],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        print(f"  Floating icon unavailable ({e}).")
        return None


def talk(jarvis: Jarvis) -> None:
    """Hotkey / tray: listen now. The offline listener needs no window; the browser recogniser lives in the window."""
    if not jarvis.voice.listen_now():
        bring_up(jarvis, listen=True)


def autostart_path() -> Path:
    if platform.system() != "Windows":
        raise RuntimeError("Autostart is only implemented for Windows")
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "JARVIS.vbs"


def autostart_script(python: str, workdir: str) -> str:
    """A .vbs launcher so no console window flashes at login."""
    pythonw = python[: -len("python.exe")] + "pythonw.exe" if python.lower().endswith("python.exe") else python  # no console window
    return ('Set sh = CreateObject("WScript.Shell")\r\n'
            f'sh.CurrentDirectory = "{workdir}"\r\n'
            f'sh.Run """{pythonw}"" -m jarvis.desktop_app", 0, False\r\n')


def hotkey_loop(callback) -> None:
    """Windows global hotkey (Ctrl+Alt+J) via RegisterHotKey; blocks in a message loop."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    MOD_ALT, MOD_CONTROL, WM_HOTKEY = 0x0001, 0x0002, 0x0312
    if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT, 0x4A):
        print(f"  Could not register {HOTKEY_LABEL} (another program is using it).")
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == WM_HOTKEY:
            callback()


def tray_icon(jarvis: Jarvis, on_quit):
    import pystray
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (5, 10, 20, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), outline=(34, 211, 238, 255), width=4)
    d.ellipse((22, 22, 42, 42), fill=(34, 211, 238, 255))
    menu = pystray.Menu(pystray.MenuItem("Open JARVIS", lambda: bring_up(jarvis), default=True),
                        pystray.MenuItem(f"Talk to JARVIS ({HOTKEY_LABEL})", lambda: talk(jarvis)),
                        pystray.MenuItem("Quit", lambda icon: (icon.stop(), on_quit())))
    return pystray.Icon("jarvis", img, "JARVIS", menu)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarvis.desktop_app")
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--show", action="store_true", help="keep the window open at start instead of minimising it to the taskbar")
    ap.add_argument("--no-tray", action="store_true")
    ap.add_argument("--no-overlay", action="store_true", help="no floating icon")
    ap.add_argument("--install-autostart", action="store_true")
    ap.add_argument("--remove-autostart", action="store_true")
    args = ap.parse_args(argv)
    if args.install_autostart or args.remove_autostart:
        path = autostart_path()
        if args.remove_autostart:
            path.unlink(missing_ok=True)
            print(f"Removed {path}")
        else:
            path.write_text(autostart_script(sys.executable, os.getcwd()))
            print(f"JARVIS will start when you sign in ({path})")
        return 0

    jarvis = Jarvis()
    if already_running(jarvis):
        # Started again (desktop shortcut) while JARVIS is running: show the running one instead of failing on the port.
        if not args.no_window:
            bring_up(jarvis)
        print("  JARVIS is already running - brought its window to the front.")
        return 0
    if platform.system() == "Windows":
        try:  # a voice request must not wait behind background apps (seen: one took 912 s while the CPU was full)
            import psutil

            psutil.Process().nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
        except Exception:  # noqa: BLE001
            pass
    jarvis.settings.listen_on_start = True  # the offline listener, if its packages are installed and the user has not turned it off
    server, thread = start_server(jarvis)
    wake = "Hey Jarvis" if jarvis.voice.status()["running"] else jarvis.settings.wake_word
    print(f"\n  JARVIS is running in the background. Say \"{wake}\", press {HOTKEY_LABEL}, or use the floating icon or the tray.\n"
          f"  Model chain: {' -> '.join(p['name'] for p in jarvis.provider_info()['chain'])}\n")
    profile = jarvis.settings.data_dir / "ui-profile"
    if not args.no_window:
        open_window(jarvis, "assistant")
        if not args.show and platform.system() == "Windows":
            def to_background() -> None:  # minimise once it has appeared; the page keeps listening for the wake word
                hwnd = wait_for_window(profile)
                if hwnd:
                    time.sleep(2)  # let the page load and start its recogniser first
                    minimize_window(hwnd)
            threading.Thread(target=to_background, daemon=True, name="jarvis-minimise").start()
        if platform.system() == "Windows":
            threading.Thread(target=window_watchdog, args=(jarvis, profile), daemon=True, name="jarvis-window-watchdog").start()
    if platform.system() == "Windows":
        threading.Thread(target=hotkey_loop, args=(lambda: talk(jarvis),), daemon=True, name="jarvis-hotkey").start()
    overlay = None if args.no_overlay else start_overlay()

    def quit_() -> None:
        if overlay and overlay.poll() is None:
            overlay.terminate()
        hwnd = find_window(profile)
        if hwnd:
            close_window(hwnd)  # its server is going away; leave no orphaned window listening
        server.should_exit = True

    try:
        if args.no_tray:
            while thread.is_alive():
                time.sleep(0.5)
        else:
            try:
                tray_icon(jarvis, quit_).run()  # blocks until Quit
            except Exception as e:  # no system tray available: keep serving
                print(f"  Tray icon unavailable ({e}); press Ctrl+C to stop.")
                while thread.is_alive():
                    time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    quit_()
    thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
