import os
import subprocess
import sys

import pytest

from conftest import ScriptedLLM, make, run
from jarvis.security import SecurityError, redact
from jarvis.tools.registry import ToolContext


def test_path_guard_blocks_outside_roots_secrets_and_symlink_escape(jarvis, home, tmp_path):
    g = jarvis.guard
    assert g.resolve(str(home / "Downloads" / "notes.txt")).name == "notes.txt"
    for bad in ("/etc/passwd", str(tmp_path / "elsewhere.txt"), str(home / ".ssh" / "id_rsa"), str(home / "app" / ".env"), str(home / "server.pem")):
        with pytest.raises(SecurityError):
            g.resolve(bad)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    try:
        os.symlink(outside, home / "link.txt")
    except OSError:  # Windows: symlinks need admin rights or Developer Mode
        if sys.platform != "win32":
            raise
    else:
        with pytest.raises(SecurityError):
            g.resolve(str(home / "link.txt"))
    if sys.platform == "win32":  # a directory junction needs no privileges, so it is the real escape route on Windows
        (tmp_path / "outdir").mkdir()
        (tmp_path / "outdir" / "secret.txt").write_text("secret")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(home / "jlink"), str(tmp_path / "outdir")], check=True, capture_output=True)
        for p in (home / "jlink", home / "jlink" / "secret.txt"):
            with pytest.raises(SecurityError):
                g.resolve(str(p))
    with pytest.raises(SecurityError):  # JARVIS never edits its own database / token
        g.resolve(str(home / ".jarvis" / "jarvis.db"), write=True)


def test_blocked_folder_cannot_be_read_listed_searched_or_sized(jarvis, ctx, home):
    """JARVIS_BLOCKED_PATHS: a folder inside an allowed root that JARVIS must not touch at all."""
    from jarvis.security import PathGuard

    private = home / "private"
    (private / "deep").mkdir(parents=True)
    (private / "accounts.txt").write_text("invoice login details")
    (private / "deep" / "invoice-copy.pdf").write_text("x")
    jarvis.guard = PathGuard([home], jarvis.settings.data_dir, [private])
    for p in (private, private / "accounts.txt", private / "deep" / "invoice-copy.pdf"):
        with pytest.raises(SecurityError, match="blocked"):
            jarvis.guard.resolve(str(p))

    def call(name, **args):
        return run(jarvis.executor.call(name, args, ctx))

    assert not call("read_file", path=str(private / "accounts.txt")).ok
    assert not call("list_dir", path=str(private)).ok
    assert "private" not in [e["name"] for e in call("list_dir", path=str(home)).data["entries"]]
    found = call("search_files", extensions=["pdf"]).data["results"]
    assert found and all("private" not in r["path"] for r in found)  # the Downloads invoice, not the blocked copy
    assert not call("search_files", contains_text="login details").data["results"]
    assert "private" not in [e["name"] for e in call("disk_usage", path=str(home)).data["largest_in"]["entries"]]
    assert jarvis.guard.resolve(str(home / "Downloads" / "notes.txt"))  # everything else is unaffected


def test_redaction():
    out = redact("key sk-abcdefghijklmnopqrstuvwxyz123 and ghp_abcdefghijklmnopqrstuvwxyz1234 OPENAI_API_KEY=supersecretvalue password: hunter2hunter")
    assert "sk-abc" not in out and "ghp_abc" not in out and "supersecretvalue" not in out and "hunter2hunter" not in out


def test_secret_in_file_is_redacted_before_reaching_the_model(jarvis, ctx, home):
    (home / "config.txt").write_text("db_password = correcthorsebattery\nname = demo\n")
    res = run(jarvis.executor.call("read_file", {"path": str(home / "config.txt")}, ctx))
    assert res.ok and "correcthorsebattery" not in res.to_model() and "demo" in res.to_model()


def test_confirm_level_action_is_queued_not_executed_then_runs_on_approval(jarvis, ctx, home):
    target = home / "new.txt"
    res = run(jarvis.executor.call("write_file", {"path": str(target), "content": "hello"}, ctx))
    assert res.pending and not target.exists()
    ap = jarvis.db.one("SELECT * FROM approvals WHERE id=?", (res.approval_id,))
    assert ap["status"] == "pending" and str(target) in ap["preview"]
    decided = run(jarvis.executor.decide(res.approval_id, True))
    assert decided["status"] == "executed" and target.read_text() == "hello"
    with pytest.raises(SecurityError):  # cannot be replayed
        run(jarvis.executor.decide(res.approval_id, True))
    decisions = [r["decision"] for r in jarvis.audit.recent(tool="write_file")]
    assert decisions == ["approved", "queued-for-approval"]


def test_denied_action_never_runs(jarvis, ctx, home):
    victim = home / "Downloads" / "notes.txt"
    res = run(jarvis.executor.call("delete_path", {"path": str(victim)}, ctx))
    assert run(jarvis.executor.decide(res.approval_id, False))["status"] == "denied"
    assert victim.exists()


def test_delete_is_recoverable(jarvis, ctx, home):
    victim = home / "Downloads" / "notes.txt"
    res = run(jarvis.executor.call("delete_path", {"path": str(victim)}, ctx))
    run(jarvis.executor.decide(res.approval_id, True))
    assert not victim.exists() and any(p.name.endswith("notes.txt") for p in jarvis.settings.trash_dir.iterdir())


def test_trusted_allowlist_only_accepts_low_risk_tools(jarvis, ctx, home):
    for dangerous in ("delete_path", "run_command", "write_file", "move_path", "git_commit", "system_stats", "nope"):
        with pytest.raises(SecurityError):
            jarvis.executor.set_trusted_tools([dangerous])
    jarvis.executor.set_trusted_tools(["create_directory"])
    res = run(jarvis.executor.call("create_directory", {"path": str(home / "auto")}, ctx))
    assert res.ok and not res.pending and (home / "auto").is_dir()
    assert jarvis.audit.recent(tool="create_directory")[0]["decision"] == "trusted-allowlist"


def test_admin_commands_and_invalid_args_are_rejected_before_queueing(jarvis, ctx, home):
    res = run(jarvis.executor.call("run_command", {"command": "sudo rm -rf /", "path": str(home)}, ctx))
    assert not res.ok and not res.pending and "administrator" in res.error
    res = run(jarvis.executor.call("write_file", {"path": "/etc/cron.d/x", "content": "x"}, ctx))
    assert not res.ok and not res.pending
    res = run(jarvis.executor.call("read_file", {"path": 5}, ctx))
    assert not res.ok and "Invalid arguments" in res.error
    assert jarvis.db.one("SELECT COUNT(*) AS n FROM approvals")["n"] == 0


def test_agent_cannot_use_tools_outside_its_scope(home, tmp_path):
    # A (possibly prompt-injected) research agent tries to write a file: refused without even reaching approval.
    llm = ScriptedLLM([("", [("write_file", {"path": str(home / "pwned.txt"), "content": "x"})]), ("I could not do that.", [])])
    j = make(home, tmp_path, providers=[llm])
    res = run(j.agents.run("research", "summarise https://example.com", conversation_id=None))
    assert any("not permitted" in e for e in res.errors)
    assert j.db.one("SELECT COUNT(*) AS n FROM approvals")["n"] == 0 and not (home / "pwned.txt").exists()
    assert "write_file" not in llm.seen[0]["tools"]


def test_no_tool_lets_a_model_approve_its_own_action(jarvis):
    names = {t.name for t in jarvis.tools.all()}
    assert not any("approv" in n or "decide" in n or "trust" in n for n in names)


def test_spoken_approval_executes_the_single_pending_action(jarvis, home):
    r = run(jarvis.chat("organize my downloads folder", "c1"))
    assert len(r["pending_approvals"]) == 1 and (home / "Downloads" / "invoice.pdf").exists()
    r2 = run(jarvis.chat("approve", "c1"))
    assert "done" in r2["text"].lower() and (home / "Downloads" / "Documents" / "invoice.pdf").exists()


def test_non_user_actor_cannot_approve_by_text(jarvis, home):
    run(jarvis.chat("organize my downloads folder", "c1"))
    run(jarvis.chat("approve", "c1", actor="workflow:wf_x"))  # e.g. an agent_prompt step inside a workflow
    assert jarvis.db.one("SELECT status FROM approvals")["status"] == "pending"
    assert (home / "Downloads" / "invoice.pdf").exists()
