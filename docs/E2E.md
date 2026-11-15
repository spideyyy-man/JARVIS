# Browser end-to-end evidence

Output of `node frontend/e2e/smoke.mjs` against a running backend serving the built UI (headless Chromium, 1440x900).
Speech recognition and synthesis are replaced by in-page fakes because a headless browser has no microphone or recogniser; everything else (HTTP, WebSocket streaming, approvals, file changes on disk) is real. Screenshots: `frontend/e2e/screenshots/`.

```text
PASS  unauthenticated browser sees the locked gate
PASS  home: live CPU/memory/disk vitals  (5%Memory0.8 / 15.7 GB)
PASS  home: offline-engine state shown honestly
PASS  home: command streamed a real answer
PASS  conversation: approval card shows what will happen  (Organize /tmp/claude-0/-home-user/b5e894da-45db-5b2e-8ae4-57f86d43add6)
PASS  approval: files untouched while pending
PASS  approval: global pending indicator
PASS  approval: action executed only after Approve
PASS  conversation: list shows saved conversations
PASS  voice: recognition + synthesis detected
PASS  voice: speech without the wake word is ignored
PASS  voice: wake-word utterance sent as a voice request, wake word stripped  ({"text":"how is my computer performing","source":"voice"})
PASS  voice: reply streamed into the voice screen
PASS  voice: reply spoken aloud with Markdown stripped  (System performance - Linux 6.18.44-fc-v470 (x8664), up 0.0 h)
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
PASS  settings: degraded model state disclosed
PASS  no console errors or failed API requests during the run

32/32 checks passed
```
