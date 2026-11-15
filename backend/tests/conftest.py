import asyncio
import subprocess
from pathlib import Path

import pytest

from jarvis.app import Jarvis
from jarvis.config import Settings
from jarvis.llm.base import LLMResponse, ToolCall
from jarvis.tools.registry import ToolContext


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / "Downloads").mkdir(parents=True)
    for name in ("invoice.pdf", "holiday.png", "notes.txt", "q3_budget_report.xlsx", "setup.exe"):
        (h / "Downloads" / name).write_text("x")
    return h


def make(home: Path, tmp_path: Path, providers=None) -> Jarvis:
    settings = Settings(data_dir=home / ".jarvis", allowed_roots=[home])
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.trash_dir.mkdir(exist_ok=True)
    settings.reports_dir.mkdir(exist_ok=True)
    settings.images_dir.mkdir(exist_ok=True)
    return Jarvis(settings, providers=providers)


@pytest.fixture
def jarvis(home, tmp_path):
    j = make(home, tmp_path)
    yield j
    j.db.close()


@pytest.fixture
def ctx(jarvis):
    return ToolContext(jarvis, agent="test", actor="test", task_id="t_test", conversation_id=None)


@pytest.fixture
def repo(home: Path) -> Path:
    r = home / "proj"
    (r / "tests").mkdir(parents=True)
    (r / "calc.py").write_text("def add(a, b):\n    return a + b  # TODO: validate\n")
    (r / "tests" / "test_calc.py").write_text("import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1]))\nfrom calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    (r / "README.md").write_text("# Calc\nA tiny calculator.\n")
    git = ["git", "-C", str(r), "-c", "user.email=t@example.com", "-c", "user.name=T", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "initial commit"], check=True)
    return r


class ScriptedLLM:
    """Stands in for a hosted model: replays a fixed list of responses (text and/or tool calls)."""
    name, model = "scripted", "scripted-1"

    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def complete(self, system, messages, tools, on_text=None, meta=None):
        self.seen.append({"system": system, "messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools], "meta": meta})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        text, calls = step
        if text and on_text:
            on_text(text)
        return LLMResponse(text=text, tool_calls=[ToolCall(f"call_{len(self.seen)}_{i}", n, a) for i, (n, a) in enumerate(calls)], provider=self.name, model=self.model)
