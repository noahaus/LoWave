# QA Pipeline GUI (Electron)

Desktop UI for running `qa-parse` → `qa-refine` → `qa-generate` against numbered steps files, grouped by **project** (the web app under test).

## Setup

From the repo root (with `.venv` already installed and `qa-parse` on PATH via the venv):

```bash
npm run gui:install
npm run serve                 # terminal 1 — demo app
source .venv/bin/activate     # terminal 2
npm run gui
```

## Projects

Each project is a container for one web app:

```
projects/<slug>/
  project.json    # name, baseUrl, username (no password)
  steps/*.txt
```

On first launch the GUI seeds a **Kestrel Demo** project (`http://localhost:3000`) and copies `examples/workflows/*.txt` into it. Create more projects from the home screen (name + URL). Open a project to add or import extra steps files, then run the pipeline against the selected file.

Passwords are not stored in `project.json`. Enter them on the run form (or rely on `.env` `QA_PASSWORD`).

## Smoke test (same runner as the GUI)

```bash
# parse + generate only (fast)
npm run gui:smoke -- --stages=parse,generate

# explicit steps file
npm run gui:smoke -- examples/workflows/login_and_search_report.txt --stages=parse,generate
```

The GUI window uses the same `pipeline-runner.js`. Launch with `npm run gui` (unsets `ELECTRON_RUN_AS_NODE` so Electron APIs work).

## Notes

- Defaults to Ollama `qwen3-coder:30b`; switch backend/model in the project run form for cloud APIs.
- Refine requires the demo app (or your project URL) to be reachable.
- Generated specs land in `tests/<steps-stem>.spec.ts`.
- If the window fails to open in some agent environments, unset `ELECTRON_RUN_AS_NODE` (the `npm run gui` script already does this).
- While a run is in progress, **Cancel** stops the current stage (`qa-parse` / refine / generate / Playwright) so you can start over.
