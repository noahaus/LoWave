# QA Pipeline GUI (Electron)

Desktop UI for running `qa-parse` → `qa-refine` → `qa-generate` against a numbered steps file.

## Setup

From the repo root (with `.venv` already installed and `qa-parse` on PATH via the venv):

```bash
npm run gui:install
npm run serve                 # terminal 1 — demo app
source .venv/bin/activate     # terminal 2
npm run gui
```

## Smoke test (same runner as the GUI)

```bash
# parse + generate only (fast)
npm run gui:smoke -- --stages=parse,generate

# explicit steps file
npm run gui:smoke -- examples/workflows/login_and_search_report.txt --stages=parse,generate
```

The GUI window uses the same `pipeline-runner.js`. Launch with `npm run gui` (unsets `ELECTRON_RUN_AS_NODE` so Electron APIs work).

## Notes

- Defaults to Ollama `qwen3-coder:30b`; switch backend/model in the form for cloud APIs.
- Refine requires the demo app (or your `QA_BASE_URL`) to be reachable.
- Generated specs land in `tests/<steps-stem>.spec.ts`.
- If the window fails to open in some agent environments, unset `ELECTRON_RUN_AS_NODE` (the `npm run gui` script already does this).
