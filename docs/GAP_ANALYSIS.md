# Gap analysis: from "assistant with tools" to "agent"

What the previous version lacked for agentic use, what was built to close each gap, how it was
verified, and what is still open. "Verified" always means on Linux, in tests; nothing here has run
on Windows or against a real language model yet.

## Closed in this round

| # | Gap | What was built | Verified by |
|---|---|---|---|
| 1 | The loop assumed a well-behaved model. Small CPU models write tool calls as text, misname tools, send `"5"` for `5`, invent arguments, repeat themselves | `robust.py`: recovery of text-form tool calls, name and argument repair (never onto an unrelated tool), a loop guard that warns then stops, per-call timeout with failover, old tool results shortened to protect a small context window | `tests/test_robust.py`; model-in-the-loop browser test against a deliberately sloppy stand-in model |
| 2 | Tasks died at the first approval | After a decision (approve or deny) the task resumes by itself with the result in context, up to 20 times in a row | `test_task_resumes_after_approval_and_finishes`, desktop multi-step test, browser test |
| 3 | One approval per click made desktop automation unusable | "Approve all `<tool>` for this task": 30 minutes, one tool, one conversation. Never available for delete, run command, git commit or send email. Revocable in Settings. Also by voice: "approve all" | `test_approve_for_this_task_...`, browser test |
| 4 | Every request went through the slow model | Fast path: recognisable simple commands run on the rule engine instantly; anything needing synthesis still goes to the model | `test_fast_path_...`, browser test |
| 5 | Four model calls per action (orchestrator -> specialist -> back) | Flat mode: one agent, a relevance-ranked shortlist of 12 tools out of ~85, plus `list_tools` to discover the rest. Two model calls per action. Default for local models | `test_flat_mode_...`, browser test (2 calls measured) |
| 6 | Plans were written and forgotten | `update_plan`: steps are marked in progress / done / failed as work proceeds and shown in the conversation | `test_plan_is_tracked_step_by_step` |
| 7 | No reuse, no undo | "Do that every day at 9" / "save that as a workflow" turns the last task's steps into a workflow. "Undo that" restores a delete; edits can be rolled back | `test_save_what_was_just_done_...`, `test_undo_delete_...` |
| 8 | Only remembered what it was told to | Statements of lasting fact ("I prefer...", "my manager is...") are stored automatically, de-duplicated; questions and commands are ignored | `test_auto_memory_...` |
| 9 | No calendar or email | Calendar: any iCalendar feed (with repeats) plus local events; announced ten minutes before; included in the briefing. Email: IMAP read, SMTP send behind a mandatory approval | `tests/test_personal.py` (fake mail servers, generated feed) |
| 10 | Automations only fired on a clock | Folder watch trigger: runs once per new, fully written file | `test_file_arrival_triggers_...` |
| 11 | Local-model replies arrived in one block | SSE streaming with tool-call fragments; `<think>` text and half-written tool calls are never shown or spoken | `test_openai_compatible_streaming_...`, stream-filter test |
| 12 | No signal when an action follows outside content | Approvals requested after reading a web page, the screen, the clipboard or an email carry a warning naming the source | `test_action_after_untrusted_content_...` |
| 13 | Desktop automation could not wait, scroll or switch apps | `wait_for_text`, `mouse_scroll`, `list_windows`, `focus_window` | GUI test on a virtual display (wait); matching logic unit-tested (windows); scroll and focus untested |
| 14 | No way to find out what works on a given machine | `python -m jarvis.doctor` and Settings > Diagnostics: checks every dependency and probes whether the model can really call a tool | `test_doctor_...`, browser test |

## Still open

| Gap | Why it is open | What would close it |
|---|---|---|
| Never run with a real language model | No model or key was available in the build environment | Run `python -m jarvis.doctor` on the laptop with Ollama; then one real conversation |
| Never run on Windows | Built on Linux | First run on the laptop; tray, hotkey, autostart, volume/brightness keys, clipboard and window switching are the untested parts |
| A 3B CPU model cannot plan long tasks reliably | Model capability, not plumbing | A larger model (slower), or a hosted model for hard tasks; the repair layer only fixes format errors, not bad decisions |
| Tool shortlist is lexical | Default embedder matches words, helped by a hand-written hint list | `JARVIS_EMBEDDER=openai:nomic-embed-text` (untested) for semantic matching |
| Icons and images cannot be clicked | Screen understanding is text recognition | A vision model (needs a GPU to be usable) |
| Flat mode gives up permission separation | One agent holds every tool, so content it reads shares a context with tools that modify things | Approvals and the untrusted-content warning still apply; use `JARVIS_AGENT_MODE=team` with a fast/hosted model to restore separation |
| Task approvals are a trade-off | A granted tool runs unprompted for 30 minutes, including after reading untrusted content | Keep grants for low-stakes tools; revoke in Settings |
| Web research not verified live | The one live attempt from the build environment was not completed | Run it on the laptop |
| Email and calendar only tested against stand-ins | No real account | Configure an app password and run the doctor |
| No phone access, no installer | Out of scope so far | - |
