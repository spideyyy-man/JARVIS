"""Run with:  python -m jarvis"""
from __future__ import annotations

import uvicorn

from .api import create_app
from .app import Jarvis


def main() -> None:
    jarvis = Jarvis()
    s = jarvis.settings
    info = jarvis.provider_info()
    print(f"\n  JARVIS online  ->  http://{s.host}:{s.port}/api/auth?token={s.api_token()}\n")
    print(f"  Language model : {' -> '.join(p['name'] for p in info['chain'])}" + ("   (offline engine only: set ANTHROPIC_API_KEY for full reasoning)" if info["degraded"] else ""))
    print(f"  Data directory : {s.data_dir}")
    print(f"  File access    : {', '.join(str(r) for r in s.allowed_roots)}\n")
    if s.host not in ("127.0.0.1", "localhost", "::1"):
        print("  WARNING: JARVIS is bound to a non-loopback address. Anyone who obtains the token can control this machine.\n")
    uvicorn.run(create_app(jarvis), host=s.host, port=s.port, log_level="warning")


if __name__ == "__main__":
    main()
