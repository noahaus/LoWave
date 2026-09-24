---
name: qa-pipeline_2
description: Turns numbered English steps into Playwright tests via qa-parse, qa-refine, and qa-generate. Use when generating E2E tests from steps.txt or examples/workflows, running the QA pipeline, or refining/grounding action plans against the demo app.
---

# QA Pipeline

Three-stage flow: **steps.txt → outputs/plans/action_plan.json → outputs/plans/refined_action_plan.json → outputs/tests/*.spec.ts**.

## Prerequisites

- Activate the venv: `source .venv/bin/activate`
- Demo app serving: `npm run serve` → `http://localhost:3000` (leave running)
- LLM: `.env` API key for Anthropic/OpenAI/Google, **or** Ollama (`--backend ollama --model qwen3-coder:30b` works if installed)
- Chromium for refine/test: `playwright install chromium` (Python + Node Playwright as needed)

## Full pipeline

Copy and track:

```
Progress:
- [ ] Serve demo app
- [ ] qa-parse
- [ ] qa-refine
- [ ] qa-generate
- [ ] playwright test
```

**1. Parse** (steps → rough plan)

```bash
qa-parse <steps.txt> outputs/plans/action_plan.json \
  --base-url http://localhost:3000 \
  --username demo@kestrel.app --password test1234
```

Ollama fallback: add `--backend ollama --model qwen3-coder:30b`.

**2. Refine** (ground on live DOM — app must be up)

```bash
qa-refine --plan outputs/plans/action_plan.json --out outputs/plans/refined_action_plan.json --url http://localhost:3000
```

Same `--backend` / `--model` as parse if needed. Use `--headed` to watch the browser.

**3. Generate** (plan → Playwright spec)

```bash
qa-generate outputs/plans/refined_action_plan.json outputs/tests/<name>.spec.ts --base-url http://localhost:3000
```

**4. Run**

```bash
QA_SLOWMO=0 npx playwright test outputs/tests/<name>.spec.ts
```

## Steps files

- Format: numbered English lines (`1. …`); `#` comments and blank lines OK
- Ready examples: `examples/workflows/*.txt`
  - `login_and_search_report.txt` → expense app (`index.html`)
  - `create_expense_report.txt` → expense app wizard
  - `calendar_create_event.txt` → use `--base-url http://localhost:3000/calendar.html`

## Partial runs

| Goal | Command |
|------|---------|
| Skip parse | Hand-edit or reuse `outputs/plans/action_plan.json` / `examples/action_plan.json` |
| Skip refine | `qa-generate` still works; locators are weaker |
| Generate only | `qa-generate examples/refined_action_plan.json outputs/tests/generated.spec.ts` |

Module form if CLIs missing: `python -m qa_pipeline.parse_steps` / `refine_plan` / `generate`.

## Defaults

- Credentials: `demo@kestrel.app` / `test1234` (or `QA_USERNAME` / `QA_PASSWORD`)
- URL: `QA_BASE_URL` or `http://localhost:3000`
- Write specs under `outputs/tests/`; prefer a descriptive name over overwriting `generated.spec.ts` when running a named workflow
