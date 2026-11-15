import time

import pytest

from conftest import run
from jarvis.automation import condition_holds, next_run, render
from jarvis.tools.registry import Tool


def test_templating_and_conditions():
    ctx = {"s1": {"cpu": {"percent": 91.5}, "items": [{"n": 1}]}}
    assert render("{{s1.cpu.percent}}", ctx) == 91.5 and render("cpu {{s1.cpu.percent}}%", ctx) == "cpu 91.5%"
    assert render({"a": ["{{s1.items.0.n}}"]}, ctx) == {"a": [1]}
    assert condition_holds({"ref": "s1.cpu.percent", "op": ">", "value": 80}, ctx)
    assert not condition_holds({"ref": "s1.cpu.percent", "op": "<", "value": 80}, ctx)
    assert not condition_holds({"ref": "s1.missing", "op": ">", "value": 1}, ctx)
    assert 59 <= next_run({"type": "interval", "seconds": 60}, 1000.0) - 1000.0 <= 60
    assert next_run({"type": "cron", "expr": "0 9 * * *"}) > time.time()


def test_validation_rejects_bad_workflows(jarvis):
    a = jarvis.automation
    for schedule, steps in [({"type": "interval", "seconds": 1}, [{"tool": "system_stats"}]), ({"type": "cron", "expr": "nope"}, [{"tool": "system_stats"}]),
                            ({"type": "manual"}, []), ({"type": "manual"}, [{"tool": "does_not_exist"}]), ({"type": "manual"}, [{"tool": "delegate", "args": {}}])]:
        with pytest.raises(ValueError):
            a.create("bad", schedule, steps)


def test_multi_step_conditional_workflow_with_retries_and_logs(jarvis):
    attempts = {"n": 0}

    def flaky(ctx):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient failure")
        return {"value": 42}

    jarvis.tools.register(Tool("flaky_probe", "test", {}, flaky))
    wf = jarvis.automation.create("probe", {"type": "manual"}, [
        {"id": "stats", "tool": "system_stats"},
        {"id": "probe", "tool": "flaky_probe", "retries": 3, "retry_delay": 0.01},
        {"id": "big", "notify": "value is {{probe.value}}", "when": {"ref": "probe.value", "op": ">", "value": 10}},
        {"id": "small", "notify": "never", "when": {"ref": "probe.value", "op": "<", "value": 10}},
    ])
    result = run(jarvis.automation.run(wf["id"]))
    log = {e["step"]: e for e in result["log"]}
    assert result["status"] == "succeeded"
    assert log["probe"]["attempts"] == 3 and log["probe"]["status"] == "ok"
    assert log["big"]["status"] == "ok" and log["small"]["status"] == "skipped"
    assert jarvis.db.one("SELECT body FROM notifications WHERE title='probe'")["body"] == "value is 42"


def test_failed_step_stops_run_and_notifies(jarvis):
    jarvis.tools.register(Tool("always_fails", "test", {}, lambda ctx: 1 / 0))
    wf = jarvis.automation.create("broken", {"type": "manual"}, [{"tool": "always_fails", "retries": 1, "retry_delay": 0.01}, {"notify": "unreachable"}])
    result = run(jarvis.automation.run(wf["id"]))
    assert result["status"] == "failed" and len(result["log"]) == 1 and result["log"][0]["attempts"] == 2
    assert "ZeroDivisionError" in result["log"][0]["detail"]
    assert jarvis.db.one("SELECT COUNT(*) AS n FROM notifications WHERE level='error'")["n"] == 1


def test_scheduler_fires_due_workflow(jarvis):
    import asyncio

    async def scenario():
        wf = jarvis.automation.create("tick", {"type": "interval", "seconds": 5}, [{"tool": "system_stats"}])
        assert jarvis.automation.tick() == []  # not due yet
        jarvis.db.update("workflows", wf["id"], next_run=time.time() - 1)
        assert jarvis.automation.tick() == [wf["id"]]
        await asyncio.gather(*jarvis.automation._tasks)
        return wf

    wf = run(scenario())
    runs = jarvis.automation.runs(wf["id"])
    assert len(runs) == 1 and runs[0]["status"] == "succeeded" and runs[0]["trigger"] == "schedule"
    assert jarvis.db.one("SELECT next_run FROM workflows WHERE id=?", (wf["id"],))["next_run"] > time.time()


def test_workflow_gets_no_extra_privileges_and_resumes_after_approval(jarvis, home):
    wf = jarvis.automation.create("tidy", {"type": "manual"}, [
        {"id": "plan", "tool": "organize_folder_plan", "args": {"path": str(home / "Downloads")}},
        {"id": "apply", "tool": "organize_folder", "args": {"path": "{{plan.path}}"}, "when": {"ref": "plan.files", "op": ">", "value": 0}},
        {"id": "done", "notify": "moved {{apply.moved}} files"}])
    result = run(jarvis.automation.run(wf["id"]))
    assert result["status"] == "waiting_approval" and (home / "Downloads" / "invoice.pdf").exists()
    ap = jarvis.db.one("SELECT * FROM approvals WHERE status='pending'")
    run(jarvis.executor.decide(ap["id"], True))
    final = jarvis.automation.runs(wf["id"])[0]
    assert final["status"] == "succeeded" and (home / "Downloads" / "Documents" / "invoice.pdf").exists()
    assert jarvis.db.one("SELECT body FROM notifications WHERE title='tidy'")["body"] == "moved 5 files"


def test_natural_language_schedule_creates_conditional_workflow(jarvis):
    r = run(jarvis.chat("every 10 minutes check my computer's performance and alert me if cpu is above 80", "c1"))
    assert r["pending_approvals"]
    run(jarvis.chat("approve", "c1"))
    wf = jarvis.automation.list()[0]
    assert wf["schedule"] == {"type": "interval", "seconds": 600} and wf["steps"][0]["tool"] == "system_stats"
    assert wf["steps"][1]["when"] == {"ref": "s1.cpu.percent", "op": ">", "value": 80.0}
    assert run(jarvis.automation.run(wf["id"]))["status"] == "succeeded"
