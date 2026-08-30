# Web App QA Pipeline

Turn numbered English workflow steps into a runnable [Playwright](https://playwright.dev) test — automatically.

```
steps.txt  ──►  parse_steps  ──►  action_plan.json
                                       │
                                       ▼
                                  refine_plan   (grounds each step on the live DOM)
                                       │
                                       ▼
                               refined_action_plan.json
                                       │
                                       ▼
                                   generate      ──►  tests/generated.spec.ts  ──►  npx playwright test
```

The three stages are independent CLI tools. A text LLM reads your steps file and drafts a rough plan; a second pass walks the *running* app, matches each step to a real element, and emits durable locators; the final stage compiles that into TypeScript.

Everything app-specific — the URL under test, login credentials, LLM provider — is configured through environment variables or command-line flags. Nothing is hardcoded, so you can point it at your own app.

---

## Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for Playwright)
- An **LLM backend**: an API key for Anthropic / OpenAI / Google, *or* a local [Ollama](https://ollama.com) install.

## Install

```bash
# 1. Python side — installs the CLI tools qa-parse / qa-refine / qa-generate
python -m venv .venv && source .venv/bin/activate
pip install -e ".[anthropic]"          # or ".[openai]", ".[google]", ".[ollama]"
playwright install chromium

# 2. Node side — Playwright test runner
npm install

# 3. Configure
cp .env.example .env                   # then fill in your API key etc.
```

## Configure

Copy `.env.example` to `.env` and set what you need. Every value also has a matching CLI flag that takes precedence.

| Variable | Purpose | Default |
|----------|---------|---------|
| `QA_BASE_URL` | URL of the app under test | `http://localhost:3000` |
| `QA_USERNAME` / `QA_PASSWORD` | Login for workflows with a sign-in step (optional) | read from the steps file |
| `LLM_BACKEND` | `anthropic` \| `openai` \| `google` \| `ollama` | `anthropic` |
| `QA_MODEL` | Pin a specific model (optional) | per-backend default |
| `ANTHROPIC_API_KEY` etc. | Credentials for your chosen backend | — |

---

## Quick start (bundled demo app)

The repo ships with a small self-contained demo app under `demo_app/` so you can try the whole pipeline in a couple of minutes with no external services.

**1. Serve the demo app** (leave this running in its own terminal):

```bash
npm run serve                 # serves demo_app/ at http://localhost:3000
```

`demo_app/index.html` is *Kestrel*, an expense-reports SPA. Demo login: **demo@kestrel.app** / **test1234**. (`demo_app/calendar.html` is a second fixture with no login.)

**2. Generate a test from a plan.** A ready-made example plan lives in `examples/`, so you can skip straight to generation:

```bash
qa-generate examples/refined_action_plan.json tests/generated.spec.ts
```

**3. Run it:**

```bash
npm test                      # or: npx playwright test --headed
```

---

## Steps file format

Stage 1 reads a plain UTF-8 `.txt` of numbered English instructions. Blank lines and `#` comments are ignored:

```text
# Login and open first report
1. Go to the app home page
2. Type demo@kestrel.app into the email field
3. Type test1234 into the password field
4. Click Sign in
5. Open the first expense report in the table
6. Assert the report detail view is visible
7. Click Back to reports
```

See `examples/kestrel_login_steps.txt` for a copy you can run against the demo app.

---

## Running the full pipeline on your own steps

```bash
# Stage 1 — steps.txt → rough plan (needs an LLM)
qa-parse examples/kestrel_login_steps.txt action_plan.json \
  --base-url http://localhost:3000 \
  --username demo@kestrel.app --password test1234

# Stage 2 — ground the plan against the LIVE app (app must be running)
qa-refine --plan action_plan.json --out refined_action_plan.json --headed

# Stage 3 — compile to a Playwright spec
qa-generate refined_action_plan.json tests/generated.spec.ts

# Run
npx playwright test tests/generated.spec.ts --headed
```

If you didn't `pip install`, the same tools run as modules from the repo root:

```bash
python -m qa_pipeline.parse_steps examples/kestrel_login_steps.txt action_plan.json
python -m qa_pipeline.refine_plan --plan action_plan.json --out refined_action_plan.json
python -m qa_pipeline.generate    refined_action_plan.json tests/generated.spec.ts
```

Run any tool with `--help` for its full flag list.

### Why the refine stage?

The steps LLM cannot see the DOM, so its selectors are guesses. `qa-refine` opens the app, snapshots the visible interactive elements before each step, asks the model to match the step to a *real* element, and emits a durable locator (preferring `data-testid` → ARIA role → label → text). It self-heals on low confidence, can reclassify an action (e.g. a `<select>` mislabeled as a click), and flags anything ambiguous in `metadata.known_ambiguities` for review. You can also hand-write or hand-edit an `action_plan.json` and skip stage 1 entirely.

---

## Repository layout

```
.
├── qa_pipeline/            # the Python tool
│   ├── config.py           # env/CLI settings resolution
│   ├── llm.py              # shared LangChain model factory
│   ├── parse_steps.py      # stage 1: steps.txt → action_plan.json
│   ├── refine_plan.py      # stage 2: ground plan on live DOM
│   └── generate.py         # stage 3: plan → Playwright spec
├── demo_app/               # self-contained fixture apps (Kestrel)
│   ├── index.html          # expense reports (has login)
│   └── calendar.html       # calendar (no login)
├── examples/               # sample steps + plans to try immediately
│   ├── kestrel_login_steps.txt
│   ├── workflows/          # usability workflows for the demo apps
│   │   ├── login_and_search_report.txt
│   │   ├── create_expense_report.txt
│   │   └── calendar_create_event.txt
│   ├── action_plan.json
│   └── refined_action_plan.json
├── tests/                  # generated Playwright specs land here
├── e2e/                    # a hand-written example spec
├── playwright.config.ts    # testDir + baseURL (from QA_BASE_URL)
├── pyproject.toml          # Python packaging + console scripts + extras
├── requirements.txt
└── .env.example
```

## Action plan schema

Both stages read and write the same JSON shape, so you can inspect or edit plans by hand:

```jsonc
{
  "workflow": { "title": "...", "base_url": "...", "summary": "..." },
  "steps": [
    {
      "step": 1,
      "action": "navigate|type|click|select|scroll|hover|drag|wait|assert",
      "description": "what the user should do / verify",
      "target": { "aria_label": "...", "text_content": "...", "css_selector": "..." },
      "input_value": "text typed or option selected",
      "expected_outcome": { "url_contains": "..." }
    }
  ],
  "metadata": { "known_ambiguities": [], "recommended_edge_cases": [] }
}
```

See `examples/` for full samples.

## Troubleshooting

- **Schema/JSON errors from stage 1** — the model returned prose; try a stronger model via `--model`, or tighten the wording of ambiguous steps.
- **No numbered steps found** — each instruction must start with `1.` / `2)` style numbering.
- **`qa-refine` can't reach the app** — confirm it's actually serving at `QA_BASE_URL`.
- **TODO comments in the generated spec** — a step couldn't be grounded; check `metadata.known_ambiguities` and refine or edit the plan.

## License

MIT — see [LICENSE](LICENSE).
