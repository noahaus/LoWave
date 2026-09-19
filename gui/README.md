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

- Defaults to Ollama `qwen3-coder:30b`; switch backend/model in **Settings** (saved for every project).
- Refine requires the demo app (or your project URL) to be reachable. **Show browser** is on by default so you can complete captchas; uncheck it for headless refine.
- Latest artifacts are scoped to the canonical steps path, its contents, base URL, and selected spec name. Plans live in `.qa-pipeline/workflows/<workflow-hash>/`; generated specs live under `tests/generated/<workflow-hash>/` so Playwright still discovers them. Re-running the unchanged workflow reuses those paths, while changing its steps or target uses a new scope.
- Raw CLI output is written to `.qa-pipeline/workflows/<workflow-hash>/logs/<timestamp>.log`. The GUI **Run status** panel shows plain-English progress instead of that dump. If a stage or workflow step fails, the panel explains why in everyday language and points at the saved log.
- Skipping parse requires that workflow scope's raw action plan before refine or generate can run. Skipping refine makes generate use that raw plan. Skipping generate requires that scope's generated spec before tests can run. The runner fails before invoking a CLI when a prerequisite is missing.
- Existing root-level plans and specs are left untouched. This update does not migrate or delete them.
- If the window fails to open in some agent environments, unset `ELECTRON_RUN_AS_NODE` (the `npm run gui` script already does this).
- While a run is in progress, **Cancel** stops the current stage (`qa-parse` / refine / generate / Playwright) so you can start over.
- Incomplete generation (unresolved required steps, including an empty workflow) is distinct from cancel and from runner/provider failure. The GUI marks the run incomplete / needs review, keeps the draft spec, does not start Playwright, and unlocks the controls. That draft is not a passed QA run.
