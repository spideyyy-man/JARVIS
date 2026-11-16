# Browser end-to-end evidence

Headless Chromium, 1440x900, against a running backend serving the built UI. Speech hardware is replaced by in-page fakes; HTTP, WebSocket streaming, approvals and file changes on disk are real. Screenshots: `frontend/e2e/screenshots/`.

## UI (`frontend/e2e/smoke.mjs`, rule engine)

```text
PASS  unauthenticated browser sees the locked gate
PASS  home: live CPU/memory/disk vitals  (6%Memory0.9 / 15.7 GB)
PASS  home: offline-engine state shown honestly
PASS  home: command streamed a real answer
PASS  home: event added by voice/text appears on today's calendar
PASS  conversation: approval card shows what will happen  (Organize /tmp/claude-0/-home-user/b5e894da-45db-5b2e-8ae4-57f86d43add6)
PASS  approval: files untouched while pending
PASS  approval: global pending indicator
PASS  approval: action executed only after Approve
PASS  approval: “approve all for this task” stops repeat prompts
PASS  conversation: list shows saved conversations
PASS  voice: recognition + synthesis detected
PASS  voice: speech without the wake word is ignored
PASS  voice: wake-word utterance sent as a voice request, wake word stripped  ({"text":"how is my computer performing","source":"voice"})
PASS  voice: reply streamed into the voice screen
PASS  voice: reply spoken aloud with Markdown stripped  (System performance - Linux 6.18.44-fc-v470 (x8664), up 0.2 h)
PASS  voice: barge-in cancels speech
PASS  agents: all seven agents listed
PASS  agents: structured inter-agent messages shown
PASS  computer: file explorer lists a real folder
PASS  computer: disks and process table
PASS  coding: repository overview with detected test command
PASS  coding: dev task added to the board
PASS  knowledge: indexed a document and retrieved the passage with its source
PASS  research: workspace renders tasks, sources and reports panels
PASS  automation: invalid schedule rejected with the server's reason
PASS  automation: workflow created, run, and per-step log shown
PASS  settings: destructive tools cannot be allowlisted
PASS  settings: low-risk tool allowlisted for trusted automation
PASS  settings: audit log records the approval decision
PASS  settings: high-impact capabilities are off by default
PASS  settings: a capability can be switched on and off by the user
PASS  settings: behaviour switches present
PASS  settings: task approval listed and revocable
PASS  settings: diagnostics run and report on this machine
PASS  settings: degraded model state disclosed
PASS  no console errors or failed API requests during the run

37/37 checks passed
```

## Model in the loop (`frontend/e2e/model_loop.mjs`)

Backend configured with an OpenAI-compatible model at `frontend/e2e/stub_llm.py` - a keyword-driven stand-in that streams and deliberately produces small-model mistakes. It proves the plumbing, not model quality.

```text
PASS  a model is configured (no offline badge)
PASS  model answered using a real tool result; its text-form tool call was repaired and hidden  (JARVIS18:20:21Your CPU is at 29.4 percent and nothing looks wrong.
 op)
PASS  requests were streamed, single-agent mode, shortlist of tools  ({"stream":true,"tools_offered":12,"has_delegate":false,"action":"system_stats"})
PASS  one tool round-trip took 2 model calls (not 4)  (2 calls)
PASS  write is held for approval
PASS  after approval the task continued by itself and verified the result  (JARVIS18:20:21Verified: the note now contains 'hello from the loop'. The task is)
PASS  simple command took the instant fast path (model not called)
PASS  model's private <think> text never reaches the user
PASS  no page errors
```
