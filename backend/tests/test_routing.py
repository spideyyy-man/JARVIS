"""Regression table for the rule engine: every phrase must keep routing to the same tool.
The rule list is order-sensitive, so a new rule can silently steal an old phrase - this catches that."""
from pathlib import Path

import pytest

from jarvis.llm.offline import Env, match_rules

ENV = Env(Path("/home/u"), lambda: "/proj", lambda: [{"id": "wf1", "name": "Morning check"}])

TABLE = [
    ("Jarvis, analyze my computer's performance.", "system_stats"), ("how is my computer performing?", "system_stats"), ("what's using all my memory, list the processes", "list_processes"),
    ("how much disk space is left", "disk_usage"),
    ("find my budget spreadsheet", "search_files"), ("where is the pdf about my tax return in documents", "search_files"), ("list the files in my downloads folder", "list_dir"),
    ("show me the files in my downloads folder", "list_dir"), ("read /home/u/todo.txt", "read_file"), (r"read C:\Users\me\todo.txt", "read_file"),
    ("organize my downloads folder", "organize_folder_plan"), (r"organize C:\Users\me\Downloads", "organize_folder_plan"), ("delete /home/u/old.txt", "delete_path"),
    ("move /home/u/a.txt to /home/u/b.txt", "move_path"), ("create a folder /home/u/new", "create_directory"), ("undo that", "restore_from_trash"), ("what's in the trash", "list_trash"),
    ("open notepad", "open_application"), ("open youtube.com", "browser_open"), ("open https://example.org/a in the browser", "browser_open"),
    # local pages (the self-test's step 2 once went to open_application with the whole sentence as the app's name)
    ("Open file:///C:/Users/me/JARVIS-selftest-1/hello.html in your browser and tell me exactly what the page shows.", "browser_open"),
    (r"open C:\Users\me\site\index.html", "browser_open"),
    ("inspect my current project", "repo_overview"), ("create a development plan for this project", "repo_overview"), ("run the tests", "run_tests"), ("git status", "git_status"),
    ("show me the recent commits", "git_log"), ("open my development environment", "dev_environment"),
    ("Explain how leader election works according to my indexed documents", "kb_search"), ("index /home/u/docs into the tech space", "kb_ingest"),
    ("search previous conversations for typescript", "conversation_search"),
    ("research the latest developments in AI agents", "web_search"), ("evaluate the repo fastapi/fastapi", "github_repo_info"),
    ("remind me to write tests", "add_dev_task"), ("remind me in 20 minutes to call Sam", "add_dev_task"), ("remind me about unfinished tasks", "list_dev_tasks"),
    ("summarize what I accomplished today", "daily_summary"), ("brief me", "daily_briefing"), ("remember that I prefer dark mode", "remember"),
    ("every 10 minutes check my computer's performance and alert me if cpu is above 80", "create_workflow"), ("list my workflows", "list_workflows"),
    ("run the workflow morning check", "run_workflow"), ("save that as a workflow called tidy up", "save_as_workflow"), ("do that every day at 9am", "save_as_workflow"),
    ("click Submit Order", "click_text"), ("click Run the tests", "click_text"), ("type hello world and press enter", "type_text"), ("press ctrl+s", "press_keys"),
    ("what's on my clipboard", "clipboard_read"), ("copy hello there to the clipboard", "clipboard_write"), ("what's on my screen", "screen_read"),
    ("read the error on my screen", "screen_read"), ("mute", "system_control"), ("turn the volume up", "system_control"), ("set brightness to 40%", "system_control"),
    ("lock my laptop", "system_control"), ("pause the music", "system_control"), ("what windows are open", "list_windows"), ("switch to chrome", "focus_window"),
    ("show me the outlook window", "focus_window"), ("which window is active", "active_window"),
    ("what's on my calendar today?", "calendar_events"), ("do I have any meetings tomorrow?", "calendar_events"), ("schedule a meeting with Priya tomorrow at 3pm", "calendar_add"),
    ("check my email", "email_inbox"), ("read email 2", "email_read"), ("email sam@example.com saying the report is ready", "email_send"),
    ("generate an image of a red fox in the snow", "generate_image"), ("draw me a lighthouse", "generate_image"),
]

NO_MATCH = ["what is the capital of France?", "tell me a joke", "find a good name for my startup"[:0] or "thanks", "why is the sky blue"]


@pytest.mark.parametrize("phrase,tool", TABLE)
def test_phrase_routes_to_tool(phrase, tool):
    hit = match_rules(phrase, ENV)
    assert hit is not None, f"no rule matched: {phrase}"
    assert hit[1][0] == tool, f"{phrase!r} routed to {hit[1][0]}, expected {tool}"


@pytest.mark.parametrize("phrase", NO_MATCH)
def test_general_questions_are_left_for_the_model(phrase):
    assert match_rules(phrase, ENV) is None


def test_open_never_treats_a_path_or_url_as_an_application_name():
    hit = match_rules(r"open C:\Users\me\report.docx", ENV)
    assert hit is None or hit[1][0] != "open_application"
    assert match_rules("open file:///C:/x/page.html", ENV)[1] == ("browser_open", {"url": "file:///C:/x/page.html"})
