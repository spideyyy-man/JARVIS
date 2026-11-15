# JARVIS - personal AI operating system

A local, voice-first assistant that understands requests, plans, calls tools on your computer under
an explicit permission model, remembers context, and runs automations.

- **Backend**: Python 3.11+, FastAPI, SQLite. `backend/`
- **Frontend**: React + TypeScript (Vite), served by the backend. `frontend/`
- **Design and trade-offs**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Evidence for the ten acceptance workflows**: [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)

## Run it

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd ../frontend && npm install && npm run build
cd .. && cp .env.example .env        # optional: add ANTHROPIC_API_KEY
backend/.venv/bin/python -m jarvis
```

Open the link it prints (`http://127.0.0.1:8765/api/auth?token=...`). The token is per-install and is
stored as an HttpOnly cookie; the API refuses requests without it.

Use Chrome or Edge for voice: speech recognition uses the browser's Web Speech API.

## What works without any API key

JARVIS ships with a deterministic **offline engine** so it is usable (and testable) with no model.
It maps recognisable requests to tools: performance, processes, disk, file search, folder
organisation, repository inspection, git, tests, development plans, document indexing and cited
retrieval, web research, reminders, daily summary, scheduled/conditional workflows. It is pattern
matching, not reasoning - open-ended conversation needs a model. With `ANTHROPIC_API_KEY` set (or a
local OpenAI-compatible model), the same agents and tools are driven by the model instead, and the
offline engine becomes the automatic fallback when the API is unreachable.

## Security model (summary)

| Level | Meaning | Examples |
|---|---|---|
| 1 READ | Inspect only. Runs automatically. | system stats, search/read files, git log, web search |
| 2 CONFIRM | Changes something. Queued with a preview until you approve. | write/move/delete files, run commands, commits |
| 3 TRUSTED | CONFIRM tools you allowlisted. Only tools the code marks low-risk are eligible. | create folder, copy, run tests |

Deleting, overwriting, running arbitrary commands and committing can never be allowlisted. Every tool
call is written to an audit log. File access is confined to `JARVIS_ALLOWED_ROOTS`; credential files
are never readable; anything that looks like a secret is redacted before it reaches the model or UI.

## Test

```bash
cd backend && .venv/bin/python -m pytest -q          # unit + integration tests
backend/.venv/bin/python scripts/acceptance.py       # the ten acceptance workflows -> docs/ACCEPTANCE.md
node frontend/e2e/smoke.mjs                          # browser end-to-end (needs the server running)
```

## Known limits

See "Limits and dependencies" in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). In short: no native
desktop shell yet, voice depends on the browser, the default embedder is lexical rather than neural,
and the Anthropic provider has not been run against the live API from this repository's CI.
