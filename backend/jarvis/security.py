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


def looks_secret(text: str) -> bool:
    """True when `text` contains something shaped like a key, token, private key or password assignment."""
    return any(p.search(text) for p in _REDACTIONS) or bool(_KV_SECRET.search(text))


# Saved details (the `profile` setting) that must not leave the computer inside a web search, a URL or a repo
# lookup without the user seeing it first. A name is ordinary in searches, so it only counts once the conversation
# has read third-party content (a page could have told the model to search for "<name> <phone>").
PRIVATE_DETAILS = {"email": "email address", "phone": "phone number", "date_of_birth": "date of birth", "address": "address",
                   "pincode": "PIN code", "roll_number": "roll number", "father_name": "father's name", "mother_name": "mother's name"}
NAME_DETAILS = {"full_name": "name", "first_name": "name", "last_name": "name"}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def outbound_leak(args: Any, profile: dict, after_untrusted: bool) -> str | None:
    """Why sending `args` off this computer needs the user's OK, or None. Checks for credential-shaped text and for
    the user's own saved details (phone numbers by their digits, other details as text)."""
    text = " ".join(str(v) for v in (args.values() if isinstance(args, dict) else [args]))
    if looks_secret(text):
        return "it contains something that looks like a password or key"
    low, digits = " ".join(text.lower().split()), _digits(text)
    found = []
    watched = {**PRIVATE_DETAILS, **(NAME_DETAILS if after_untrusted else {})}
    for key, label in watched.items():
        value = " ".join(str(profile.get(key) or "").lower().split())
        if len(value) < 3:
            continue
        if key == "phone":
            hit = len(_digits(value)) >= 7 and _digits(value)[-7:] in digits
        elif key in ("date_of_birth", "pincode", "roll_number") and len(_digits(value)) >= 4 and value.replace(" ", "").isascii():
            hit = value in low or (len(_digits(value)) >= 6 and _digits(value) in digits)
        elif key == "address":
            hit = (value[:20] if len(value) > 20 else value) in low
        else:
            hit = value in low
        if hit and label not in found:
            found.append(label)
    if found:
        return f"it would send your {', '.join(found)} off this computer"
    return None


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

    def __init__(self, allowed_roots: list[Path], data_dir: Path, blocked: list[Path] | None = None):
        self.roots = [r.resolve() for r in allowed_roots]
        self.data_dir = data_dir.resolve()
        # Folders the user excluded (JARVIS_BLOCKED_PATHS), even when they sit inside an allowed root.
        self.blocked = [b.resolve() for b in blocked or []]

    def is_blocked(self, path: Path) -> bool:
        return any(path == b or b in path.parents for b in self.blocked)

    def is_sensitive(self, path: Path) -> bool:
        """True for anything a tool must not read or list: credentials, and the user's blocked folders."""
        if SENSITIVE_PARTS & set(path.parts):
            return True
        if self.blocked and self.is_blocked(path.absolute()):
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
        if self.is_blocked(path):
            raise SecurityError(f"Path is in a folder the user has blocked (JARVIS_BLOCKED_PATHS): {path}")
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
