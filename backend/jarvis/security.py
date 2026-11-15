"""Security primitives: execution levels, path guard, secret redaction, audit log.

LEVEL 1  READ     - inspects, never modifies. Runs automatically.
LEVEL 2  CONFIRM  - modifies something. Prepared, then queued for explicit user approval.
LEVEL 3  TRUSTED  - a CONFIRM tool the user allowlisted AND that the code marks as low-risk
                    (`trust_eligible`). Destructive tools can never be allowlisted.
"""
from __future__ import annotations

import fnmatch
import json
import re
import time
from enum import Enum
from pathlib import Path
from typing import Any

from .db import Database


class Level(str, Enum):
    READ = "read"
    CONFIRM = "confirm"


class SecurityError(Exception):
    pass


SENSITIVE_PARTS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", "keyrings", ".password-store", ".ccr"}
SENSITIVE_NAMES = [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
                   ".netrc", ".npmrc", ".pypirc", "credentials", "credentials.*", "*.kdbx", "api_token",
                   "shadow", "sudoers", ".git-credentials", "secrets.*"]

_REDACTIONS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{20,}"),
]
_KV_SECRET = re.compile(r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|secret|token|passwd|password)[A-Z0-9_]*)(\s*[:=]\s*)(['\"]?)([^\s'\"]{6,})")


def redact(text: str) -> str:
    """Mask anything that looks like a credential before it reaches the model, UI or logs."""
    for pattern in _REDACTIONS:
        text = pattern.sub("[REDACTED]", text)
    return _KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED]", text)


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


class PathGuard:
    """Every filesystem path a tool touches goes through here."""

    def __init__(self, allowed_roots: list[Path], data_dir: Path):
        self.roots = [r.resolve() for r in allowed_roots]
        self.data_dir = data_dir.resolve()

    def is_sensitive(self, path: Path) -> bool:
        if SENSITIVE_PARTS & set(path.parts):
            return True
        return any(fnmatch.fnmatch(path.name.lower(), pat) for pat in SENSITIVE_NAMES)

    def resolve(self, raw: str | Path, *, write: bool = False) -> Path:
        # resolve() follows symlinks, so a link pointing outside the roots is rejected too.
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.roots[0] / path if self.roots else path
        path = path.resolve()
        if not any(path == r or r in path.parents for r in self.roots):
            raise SecurityError(f"Path is outside the allowed roots: {path}")
        if self.is_sensitive(path):
            raise SecurityError(f"Path is protected (credentials/secrets): {path}")
        if write:
            if path == self.data_dir or (self.data_dir in path.parents and (self.data_dir / "reports") not in [path, *path.parents]):
                raise SecurityError("JARVIS will not modify its own data directory")
            if ".git" in path.parts:
                raise SecurityError("Direct writes inside .git are not allowed")
            if path in self.roots:
                raise SecurityError("Refusing to modify an allowed root itself")
        return path


class AuditLog:
    def __init__(self, db: Database):
        self.db = db

    def record(self, *, actor: str, tool: str, level: str, decision: str, args: dict, outcome: str, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO audit_log(ts, actor, tool, level, decision, args, outcome, detail) VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), actor, tool, level, decision, redact(json.dumps(args, default=str))[:4000], outcome, redact(detail)[:2000]),
        )

    def recent(self, limit: int = 100, tool: str | None = None) -> list[dict]:
        if tool:
            return self.db.query("SELECT * FROM audit_log WHERE tool=? ORDER BY id DESC LIMIT ?", (tool, limit))
        return self.db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
