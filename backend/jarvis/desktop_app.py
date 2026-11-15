"""Desktop launcher: runs JARVIS as an app instead of a browser tab.

    python -m jarvis.desktop_app                    start server + app window + tray icon + global hotkey
    python -m jarvis.desktop_app --install-autostart   start with Windows (remove with --remove-autostart)

The window is Edge/Chrome in "app" mode with its own profile, so microphone permission is remembered
and the wake word keeps listening while the window is minimised. Ctrl+Alt+J (Windows) brings up Voice.
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


def window_command(browser: str, url: str, profile: Path) -> list[str]:
    return [browser, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1360,900", "--no-first-run", "--no-default-browser-check"]


def app_url(jarvis: Jarvis, screen: str = "home") -> str:
    s = jarvis.settings
    return f"http://127.0.0.1:{s.port}/api/auth?token={s.api_token()}&next=/%23/{screen}"


def open_window(jarvis: Jarvis, screen: str = "home") -> bool:
    browser = find_browser()
    if not browser:
        import webbrowser

        return webbrowser.open(app_url(jarvis, screen))
    subprocess.Popen(window_command(browser, app_url(jarvis, screen), jarvis.settings.data_dir / "ui-profile"),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


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
    menu = pystray.Menu(pystray.MenuItem("Open JARVIS", lambda: open_window(jarvis, "home"), default=True),
                        pystray.MenuItem(f"Voice ({HOTKEY_LABEL})", lambda: open_window(jarvis, "voice")),
                        pystray.MenuItem("Quit", lambda icon: (icon.stop(), on_quit())))
    return pystray.Icon("jarvis", img, "JARVIS", menu)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarvis.desktop_app")
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--no-tray", action="store_true")
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
    server, thread = start_server(jarvis)
    print(f"\n  JARVIS desktop is running. Tray icon: open / voice / quit. Hotkey: {HOTKEY_LABEL} (Windows).\n  Model chain: {' -> '.join(p['name'] for p in jarvis.provider_info()['chain'])}\n")
    if not args.no_window:
        open_window(jarvis, "home")
    if platform.system() == "Windows":
        threading.Thread(target=hotkey_loop, args=(lambda: open_window(jarvis, "voice"),), daemon=True, name="jarvis-hotkey").start()

    def quit_() -> None:
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
