# Demo apps ("Kestrel")

Two self-contained, dependency-free web apps used as fixtures for the QA pipeline. Both are pure vanilla JS with in-memory state that resets on reload, so every recorded run starts from an identical, reproducible state.

Serve them from the repo root with:

```bash
npm run serve        # serves this folder at http://localhost:3000
```

## `index.html` — Kestrel Expense Reports

A small SaaS-style app: login, dashboard, a reports table with search/sort/filter, a multi-step "new report" wizard, a report detail view, and settings.

- **Demo login:** `demo@kestrel.app` / `test1234`
- Routes use hash navigation: `#/login`, `#/dashboard`, `#/reports`, `#/reports/new`, `#/settings`.
- Interactive elements carry `data-testid` attributes, which the pipeline prefers as the most durable locators.

## `calendar.html` — Kestrel Calendar

A month/week calendar with event create/edit/delete, drag-to-reschedule, category filtering, and search. No login. "Today" is pinned to a fixed date (2026-07-15) so recordings stay reproducible.

---

These are throwaway fixtures. To target your own app, set `QA_BASE_URL` (and, if needed, `QA_USERNAME` / `QA_PASSWORD`) in your `.env` and point the pipeline there instead.
