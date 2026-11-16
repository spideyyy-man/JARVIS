"""SQLite persistence. One connection, WAL mode, guarded by a lock (calls are short)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, title TEXT, summary TEXT DEFAULT '', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT, content TEXT, meta TEXT DEFAULT '{}', created REAL);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, message_id UNINDEXED, conversation_id UNINDEXED);
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, conversation_id TEXT, parent_id TEXT, agent TEXT, title TEXT, status TEXT, plan TEXT DEFAULT '[]', result TEXT DEFAULT '', evaluation TEXT DEFAULT '{}', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS agent_messages (id TEXT PRIMARY KEY, task_id TEXT, sender TEXT, recipient TEXT, type TEXT, content TEXT, data TEXT DEFAULT '{}', created REAL);
CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, tool TEXT, args TEXT, preview TEXT, agent TEXT, task_id TEXT, conversation_id TEXT, origin TEXT DEFAULT '{}', status TEXT, result TEXT DEFAULT '', created REAL, decided REAL);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, tool TEXT, level TEXT, decision TEXT, args TEXT, outcome TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, kind TEXT, content TEXT, vector BLOB, created REAL);
CREATE TABLE IF NOT EXISTS kb_documents (id TEXT PRIMARY KEY, space TEXT, title TEXT, source TEXT, kind TEXT, sha TEXT, chunks INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS kb_chunks (id TEXT PRIMARY KEY, document_id TEXT, space TEXT, ord INTEGER, locator TEXT, text TEXT, vector BLOB);
CREATE INDEX IF NOT EXISTS idx_chunks_space ON kb_chunks(space);
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(text, chunk_id UNINDEXED, space UNINDEXED);
CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, task_id TEXT, url TEXT, title TEXT, kind TEXT, snippet TEXT, fetched REAL);
CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, name TEXT, description TEXT DEFAULT '', schedule TEXT, steps TEXT, enabled INTEGER DEFAULT 1, next_run REAL, last_run REAL, created REAL);
CREATE TABLE IF NOT EXISTS workflow_runs (id TEXT PRIMARY KEY, workflow_id TEXT, status TEXT, trigger TEXT, cursor INTEGER DEFAULT 0, context TEXT DEFAULT '{}', log TEXT DEFAULT '[]', started REAL, finished REAL);
CREATE TABLE IF NOT EXISTS dev_tasks (id TEXT PRIMARY KEY, project TEXT, title TEXT, status TEXT, notes TEXT DEFAULT '', due REAL, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, level TEXT, title TEXT, body TEXT, read INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

JSON_COLUMNS = {"trace", "meta", "plan", "evaluation", "data", "args", "origin", "schedule", "steps", "context", "log"}


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Database:
    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        for table, column, ddl in [("dev_tasks", "reminded", "INTEGER DEFAULT 0"), ("tasks", "trace", "TEXT DEFAULT '[]'")]:  # additive migrations
            if column not in {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._decode(dict(r)) for r in rows]

    def one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        enc = {k: (json.dumps(v, default=str) if k in JSON_COLUMNS and not isinstance(v, str) else v) for k, v in row.items()}
        cols = ", ".join(enc)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(enc))})", list(enc.values()))
        return row

    def update(self, table: str, id_: str, **fields: Any) -> None:
        enc = {k: (json.dumps(v, default=str) if k in JSON_COLUMNS and not isinstance(v, str) else v) for k, v in fields.items()}
        sets = ", ".join(f"{k}=?" for k in enc)
        self.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*enc.values(), id_])

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for k in JSON_COLUMNS & row.keys():
            if isinstance(row[k], str):
                try:
                    row[k] = json.loads(row[k])
                except ValueError:
                    pass
        row.pop("vector", None)
        return row

    # -- small helpers used across modules ---------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def notify(self, level: str, title: str, body: str = "") -> dict[str, Any]:
        return self.insert("notifications", {"id": new_id("n_"), "level": level, "title": title, "body": body, "read": 0, "created": time.time()})

    def close(self) -> None:
        with self._lock:
            self._conn.close()
