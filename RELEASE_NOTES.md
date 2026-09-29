# Release notes

**From:** `00c8918a8f544b3993ebb28005b328ec0034ece0` (7 Sep 2026) — app icon  
**Through:** `a14692228d546d6959583470617802d209a54193` (19 Sep 2026) — merge of PR #13 (`improvement/gui-logs`)

Nothing after `a146922` is in this note. The cutoff is the GUI logs merge: denser layout, generated-test tiles, live pass/fail, and plain-English run status with logs saved on disk.

This release takes LoWave from a working pipeline with a new app identity to a desktop QA tool that can generate, ground, and run Playwright tests without dumping raw CLI noise at the operator.

---

## Highlights

- LoWave has an app icon and a denser desktop UI that keeps steps, logs, and generated tests on one screen.
- Parse, refine, and generate are safer to rerun: workflows are isolated, incomplete specs no longer look like passes, and locators/assertions survive the round trip to the live DOM.
- Signed-in apps can be tested through a local authentication hook without putting credentials in steps, prompts, plans, or logs.
- Claude Code and Codex CLI subscriptions can drive the pipeline when an API key is not the right fit.

---

## LoWave GUI

Starting from the icon in `00c8918`, the desktop app is now the primary way to run the pipeline.

- **App identity.** Window, dock, and in-app icon so LoWave reads as a product rather than a terminal wrapper.
- **Project workspace.** Home, project, About, and Settings views. Backend and model live in Settings and apply to every project. Passwords stay off disk.
- **Tighter layout.** Steps files, file contents (Steps / Logs), and generated-test tiles sit side by side so parse and test stay on one screen.
- **Generated-test tiles.** Numbered steps render as tiles. A Playwright step reporter paints them pass/fail while Test runs. Click a tile to expand that step’s generated script.
- **Plain-English run status.** Stage progress and failures are explained in everyday language. Raw CLI output is saved to a timestamped log under `.qa-pipeline/workflows/<hash>/logs/` instead of filling the panel.
- **Incomplete vs failed vs cancelled.** Generate can finish with gaps (unresolved steps). The GUI marks the run as needing review, keeps the draft spec, and does not start Playwright or report success.
- **Cancel.** Stop the current parse, refine, generate, or Playwright stage and unlock the controls.
- **Show browser.** Headed refine is on by default so captchas and bot checks can be completed; uncheck for headless.

---

## Pipeline reliability

Artifacts and generated tests are scoped so two workflows cannot overwrite each other, and generated code is meant to run rather than merely compile.

- Workflow plans and specs are keyed by steps path, contents, base URL, and spec name (`.qa-pipeline/workflows/<hash>/` and `tests/generated/<hash>/`). Existing root-level files are left in place.
- Explicit spec names are validated; skipping a stage fails fast if that workflow’s prerequisite artifact is missing.
- Generate emits safer Playwright steps, rejects placeholder locators (`n/a`, and similar), and preserves authored assertion values instead of substituting guessed ones.
- Locator case and parser-owned assertion semantics are kept through DOM snapshots and refinement, including empty values and mixed counts.
- Low-confidence or ungrounded steps surface as incomplete rather than a green run. Incomplete generation uses a distinct exit status (`3`) from cancel and from provider failure.
- Refine handles reviewed locator edge cases (visible controls, custom clickable elements, bot-check pages) without treating a partial spec as success.

---

## Runtime authentication

For apps that need a real session, authentication is an explicit opt-in via a **trusted local hook** (`runtime/auth-hook-runner.cjs`, `qa-parse --runtime-auth`, `qa-refine --auth-hook`, `qa-generate --runtime-auth`).

- The hook signs in at run time and returns Playwright storage state over a local pipe. Credentials do not enter the steps file, model prompt, saved plan, generated spec, command line, or pipeline logs.
- Session cookies and nested state are redacted in diagnostics. Encoded, chunked, and nested secret forms are covered; non-secret session content is left intact.
- Generated authenticated specs fail closed if `QA_AUTH_HOOK` is missing. State is accepted only for the app’s exact HTTP(S) origin.
- Helper processes started by a hook are cleaned up so a failed or cancelled run does not leave browsers or shells behind.
- Authenticated workflows are safely rerunnable: hook identity is part of the workflow scope, so a different hook does not reuse the wrong cached plan or spec.

See `runtime/README.md` for the trust boundary. There is no GUI picker for the hook in this release.

---

## LLM backends

- Optional **Claude Code** (`claude-cli`) and **Codex** (`codex-cli`) providers run through a signed-in local CLI (stdin prompts; Codex uses a read-only filesystem sandbox).
- Leave `QA_MODEL` / the GUI Model field blank to use the selected CLI’s default.
- These backends are optional; confirm subscription terms before treating them as a supported commercial integration.

---

## Upgrade notes

- **Artifact paths.** New runs write under `.qa-pipeline/workflows/` and `tests/generated/<workflow-hash>/`. Old `action_plan.json` / `tests/generated.spec.ts` at the repo root are not migrated or deleted.
- **Incomplete generate.** Callers that treated any generate exit as success must handle exit code `3` as “draft needs review.”
- **Runtime auth.** Use `--runtime-auth` / `--auth-hook` together across parse, refine, generate, and Playwright. Do not combine the hook with username/password flags.
- **GUI settings.** Backend and model moved off the project form onto Settings and persist for all projects.

---

## Commits in this release

| Date | Commit | Summary |
|------|--------|---------|
| 2026-09-07 | `00c8918` | App icon for LoWave |
| 2026-09-09 | `1fa272e` … `90fd6c0` | Preserve parsed values, assertions, and locator case; reject placeholder locators |
| 2026-09-13 | `b2cfe3c` … `aae1104` | Isolate workflow artifacts; incomplete generate; Claude/Codex; locator review fixes |
| 2026-09-13–14 | `32fd83c` … `faaa077` | Runtime auth hook, secret redaction, process cleanup (PR #4) |
| 2026-09-14 | `78e315f` | README title |
| 2026-09-19 | `d8a7889` | UI changes to optimize screen usage (step tiles, live reporter, status copy) |
| 2026-09-19 | `a14692228d546d6959583470617802d209a54193` | Merge PR #13 — GUI logs and layout (release cutoff) |
