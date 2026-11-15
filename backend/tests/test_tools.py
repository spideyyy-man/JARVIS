from conftest import run


def call(jarvis, ctx, name, **args):
    res = run(jarvis.executor.call(name, args, ctx))
    assert res.ok, res.error
    return res


def test_system_stats_reports_real_numbers(jarvis, ctx):
    d = call(jarvis, ctx, "system_stats").data
    assert 0 <= d["cpu"]["percent"] <= 100 and d["memory"]["total_gb"] > 0 and d["cpu"]["cores"] >= 1
    assert d["top_memory"] and d["assessment"]
    assert call(jarvis, ctx, "list_processes", sort_by="memory", limit=5).data["processes"]
    assert call(jarvis, ctx, "disk_usage", path=str(jarvis.settings.allowed_roots[0])).data["largest_in"]["entries"]


def test_search_files_by_keyword_extension_and_content(jarvis, ctx, home):
    (home / "docs").mkdir()
    (home / "docs" / "meeting.md").write_text("Agenda\nDiscuss the quarterly roadmap\n")
    d = call(jarvis, ctx, "search_files", keywords=["budget"]).data
    assert d["results"][0]["path"].endswith("q3_budget_report.xlsx")
    d = call(jarvis, ctx, "search_files", extensions=["pdf"]).data
    assert [r["path"].rsplit("/", 1)[1] for r in d["results"]] == ["invoice.pdf"]
    d = call(jarvis, ctx, "search_files", contains_text="quarterly roadmap").data
    assert d["results"][0]["match"]["line"] == 2


def test_organize_plan_is_a_dry_run_and_apply_moves_files(jarvis, ctx, home):
    plan = call(jarvis, ctx, "organize_folder_plan", path=str(home / "Downloads")).data
    assert plan["files"] == 5 and plan["by_category"]["Documents"] == 2 and (home / "Downloads" / "invoice.pdf").exists()
    res = call(jarvis, ctx, "organize_folder", path=str(home / "Downloads"))
    assert res.pending
    run(jarvis.executor.decide(res.approval_id, True))
    assert (home / "Downloads" / "Images" / "holiday.png").exists() and (home / "Downloads" / "Installers" / "setup.exe").exists()


def test_repo_overview_git_and_code_search(jarvis, ctx, repo):
    d = call(jarvis, ctx, "repo_overview", path=str(repo)).data
    assert d["git"]["branch"].startswith("main") and d["git"]["uncommitted_changes"] == 0
    assert d["languages"][0]["language"] == "Python" and d["todo_markers"] == 1 and "pytest" in d["test_command"]
    assert "initial commit" in d["git"]["recent_commits"][0]
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    assert "calc.py" in call(jarvis, ctx, "git_status", path=str(repo)).data["status"]
    assert "-    return a + b" in call(jarvis, ctx, "git_diff", path=str(repo)).data["diff"]
    assert call(jarvis, ctx, "search_code", pattern="def add", path=str(repo)).data["matches"][0]["file"] == "calc.py"


def test_coding_workflow_edit_test_review(jarvis, ctx, repo):
    """plan -> execute (approved edit) -> test -> review, including a failing run being reported faithfully."""
    res = call(jarvis, ctx, "replace_in_file", path=str(repo / "calc.py"), old="return a + b", new="return a - b")
    run(jarvis.executor.decide(res.approval_id, True))
    res = call(jarvis, ctx, "run_tests", path=str(repo))
    out = run(jarvis.executor.decide(res.approval_id, True))
    assert '"passed": false' in out["result"] and "test_add" in out["result"]
    res = call(jarvis, ctx, "replace_in_file", path=str(repo / "calc.py"), old="return a - b", new="return a + b")
    run(jarvis.executor.decide(res.approval_id, True))
    res = call(jarvis, ctx, "run_tests", path=str(repo))
    assert '"passed": true' in run(jarvis.executor.decide(res.approval_id, True))["result"]
    assert len(list(jarvis.settings.trash_dir.iterdir())) == 2  # each edit kept a backup


def test_dev_tasks(jarvis, ctx):
    t = call(jarvis, ctx, "add_dev_task", title="write docs", due_in_days=-1).data
    listing = call(jarvis, ctx, "list_dev_tasks").data
    assert listing["open"] == 1 and listing["tasks"][0]["overdue"]
    call(jarvis, ctx, "update_dev_task", id=t["id"], status="done")
    assert call(jarvis, ctx, "list_dev_tasks").data["open"] == 0
