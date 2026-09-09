#!/usr/bin/env python3
"""generate.py — deterministically compile an action plan into a Playwright spec.

Supports two input schemas:
  • Raw action plan   — original parser output (aria_label / text_content / css_selector)
  • Refined plan      — output of refine_plan.py (target.playwright_locator / target.strategy
                        / target.locator_value / value / refinement.grounded)

When a step carries a grounded refined locator it is emitted directly.
Ungrounded or unrefined steps fall back to the original heuristic locator logic.
Steps that failed refinement are emitted as commented-out TODO blocks so the
spec still runs — skipping just the broken step — rather than failing to compile.

    python -m qa_pipeline.generate refined_action_plan.json tests/generated.spec.ts
"""
import re
import json
import argparse
from pathlib import Path

from qa_pipeline import config

GREEN, BLUE, YELLOW, DIM, BOLD, RESET = (
    "\033[92m", "\033[94m", "\033[93m", "\033[2m", "\033[1m", "\033[0m"
)
CYAN = "\033[96m"


# ─────────────────────────── helpers ────────────────────────────────────────

def q(s) -> str:
    """Single-quoted JS/TS string literal with proper escaping."""
    s = "" if s is None else str(s)
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _one_line(text) -> str:
    """Flatten text so a generated line comment cannot become executable code."""
    return " ".join(str("" if text is None else text).split())


_SENTINELS = {"n/a", "n\\a", "na", "none", "null", "nil", "-", "--", "unknown", ""}


def _is_sentinel(value) -> bool:
    """Return True when refinement supplied a placeholder, not a locator."""
    return isinstance(value, str) and value.strip().lower() in _SENTINELS


def _js_regex_pattern(value: str) -> str:
    """Escape text for a JavaScript regex literal, including its `/` delimiter."""
    return re.sub(r"([\\/.*+?^${}()|\[\]])", r"\\\1", str(value))


def _by_label_js(name: str) -> str:
    """Accessible-name locator with exact match (avoids 'Search' hitting 'Search by voice')."""
    return f"page.getByLabel({q(name)}, {{ exact: true }})"


def _by_role_js(role: str, name: str | None = None) -> str:
    if name:
        return f"page.getByRole({q(role)}, {{ name: {q(name)}, exact: true }})"
    return f"page.getByRole({q(role)})"


def _refined_locator_expr(target: dict) -> str | None:
    """Convert a refined target block into a Playwright page.* call.

    refine_plan.py stores the locator as a Python-style expression
    (e.g. get_by_label("Email", exact=True)).  We convert it to the TS equivalent
    (page.getByLabel('Email', { exact: true })).
    """
    strategy = (target.get("strategy") or "").strip().lower()
    value = target.get("locator_value")
    if _is_sentinel(value):
        return None
    if value and strategy in {"css", "xpath", "testid"}:
        if strategy == "testid":
            return f"page.getByTestId({q(value)})"
        return f"page.locator({q(value)})"

    raw = target.get("playwright_locator")
    if not raw:
        return None
    sole_argument = re.fullmatch(r"\s*locator\(\s*(['\"])(.*?)\1\s*\)\s*", raw)
    if sole_argument and _is_sentinel(sole_argument.group(2)):
        return None

    # Python snake_case -> JS camelCase method names
    snake_to_camel = {
        "get_by_role":        "getByRole",
        "get_by_label":       "getByLabel",
        "get_by_placeholder": "getByPlaceholder",
        "get_by_text":        "getByText",
        "get_by_test_id":     "getByTestId",
        "locator":            "locator",
    }
    expr = raw
    for py_name, js_name in snake_to_camel.items():
        expr = expr.replace(py_name + "(", js_name + "(")

    # Convert Python args to JS: getByRole("combobox", name="Search", exact=True)
    # -> getByRole('combobox', { name: 'Search', exact: true })
    def _to_js_args(m):
        inner = m.group(1)
        kwargs = re.findall(r'(\w+)="([^"]*)"', inner)
        bool_kwargs = re.findall(r'(\w+)=(True|False)', inner)
        positional = re.findall(r'^"([^"]*)"', inner)
        parts = []
        if positional:
            parts.append(q(positional[0]))
        obj_bits = [f"{k}: {q(v)}" for k, v in kwargs]
        obj_bits.extend(f"{k}: {v.lower()}" for k, v in bool_kwargs)
        if obj_bits:
            parts.append("{ " + ", ".join(obj_bits) + " }")
        elif not positional:
            parts.append(inner.replace('"', "'"))
        return "(" + ", ".join(parts) + ")"

    expr = re.sub(r'\(([^)]*)\)', _to_js_args, expr)
    return f"page.{expr}"


# ─────────────────── fallback (unrefined) locator ───────────────────────────

def _fallback_locator(target: dict, action: str) -> str | None:
    """Original heuristic locator — used when no refined locator is available."""
    aria = target.get("aria_label")
    text = target.get("text_content")
    css  = target.get("css_selector") or ""

    if _is_sentinel(aria):
        aria = None
    if _is_sentinel(text):
        text = None
    if _is_sentinel(css):
        css = ""

    if action == "click":
        name = text or aria
        if name:
            return _by_role_js("button", name)

    if action in ("type", "select"):
        if aria:
            return _by_label_js(aria)

    if action == "assert":
        if css in ("h1", "h2", "h3") and text:
            return _by_role_js("heading", text)
        if aria:
            return _by_label_js(aria)
        if css:
            return f"page.locator({q(css)})"
        if text:
            return f"page.getByText({q(text)}, {{ exact: true }})"

    if aria:
        return _by_label_js(aria)
    if css:
        return f"page.locator({q(css)})"
    if text:
        return f"page.getByText({q(text)}, {{ exact: true }})"
    return None


def locator(target: dict, action: str, refinement: dict | None) -> str | None:
    """Return the best available locator for a step.

    Priority: grounded refined locator > fallback heuristic.
    """
    if refinement and refinement.get("grounded"):
        expr = _refined_locator_expr(target)
        if expr:
            return expr
    return _fallback_locator(target, action)


# ─────────────────────────── assert emitter ─────────────────────────────────

def _is_prose(text: str | None) -> bool:
    """Return True if text looks like description prose rather than visible UI text.

    Heuristics: longer than 80 chars, ends with a period, or starts with a
    capital and contains words like 'should', 'verifies', 'matches'.
    """
    if not text:
        return False
    prose_signals = (
        "should",
        "verifies",
        "matches",
        "input value",
        "ensure",
        "confirm",
        " is displayed",
        " is visible",
        " is present",
        "still shows",
        "non-empty",
    )
    return (
        len(text) > 80
        or (text[0].isupper() and text.endswith("."))
        or any(w in text.lower() for w in prose_signals)
    )


def emit_assert(step: dict, refinement: dict | None) -> str:
    """Emit one or more Playwright expect() lines for an assert step.

    Multi-value asserts (assert_values list) each become their own
    toContainText() call against the container locator so that a single
    step checking title + priority + date + description compiles correctly.
    """
    target = step.get("target", {}) or {}
    eo     = step.get("expected_outcome", {}) or {}
    css    = target.get("css_selector") or ""
    num    = step.get("step")

    lines: list[str] = []
    handled: set[str] = set()
    expr = locator(target, "assert", refinement)

    # Parser expectations are authoritative. Emit their direct Playwright
    # equivalents before considering any text suggested during grounding.
    if eo.get("url_equals"):
        lines.append(f"await expect(page).toHaveURL({q(eo['url_equals'])});")
        handled.add("url_equals")

    if eo.get("url_contains"):
        lines.append(f"await expect(page).toHaveURL(/{_js_regex_pattern(eo['url_contains'])}/);")
        handled.add("url_contains")

    if eo.get("url_not_contains"):
        lines.append(f"await expect(page).not.toHaveURL(/{_js_regex_pattern(eo['url_not_contains'])}/);")
        handled.add("url_not_contains")

    absent_text = eo.get("visible_text_absent") or eo.get("not_visible_text")
    if absent_text:
        lines.append(
            f"await expect(page.getByText({q(absent_text)}, {{ exact: false }})).toHaveCount(0);"
        )
        handled.update({key for key in ("visible_text_absent", "not_visible_text") if key in eo})

    field_value = eo.get("field_value")
    if field_value and expr and "checked" not in eo and not _is_prose(str(field_value)):
        lines.append(f"await expect({expr}).toHaveValue({q(eo['field_value'])});")
        handled.add("field_value")

    if "checked" in eo and expr:
        checked = str(eo["checked"]).strip().lower()
        if checked in {"true", "checked", "yes", "1"}:
            lines.append(f"await expect({expr}).toBeChecked({{ checked: true }});")
            handled.add("checked")
        elif checked in {"false", "unchecked", "no", "0"}:
            lines.append(f"await expect({expr}).toBeChecked({{ checked: false }});")
            handled.add("checked")
        else:
            return f"// TODO: assert step {num} cannot safely express checked={eo['checked']!r}"
        if str(field_value).strip().lower() in {"checked", "unchecked"}:
            handled.add("field_value")

    if "element_count" in eo and expr:
        count_match = re.match(r"\s*(\d+)", str(eo["element_count"]))
        if not count_match:
            return f"// TODO: assert step {num} cannot safely express element_count={eo['element_count']!r}"
        count = int(count_match.group(1))
        if not (absent_text and count == 0):
            lines.append(f"await expect({expr}).toHaveCount({count});")
        handled.add("element_count")

    visible_text = eo.get("text_contains") or eo.get("visible_text")
    if visible_text and not _is_prose(visible_text):
        if expr:
            if "getByText" in expr and ".locator(" not in expr:
                lines.append(f"await expect({expr}.first()).toBeVisible();")
            else:
                lines.append(f"await expect({expr}).toContainText({q(visible_text)});")
        else:
            lines.append(
                f"await expect(page.getByText({q(visible_text)}, {{ exact: false }})).toBeVisible();"
            )
        handled.update({key for key in ("text_contains", "visible_text") if key in eo})

    if lines:
        descriptive = {"assertion", "assert_values", "element_visible", "visible_element", "element_state"}
        unsupported = set(eo) - handled - descriptive
        if unsupported:
            lines.append(
                f"// TODO: assert step {num} cannot safely express "
                + ", ".join(sorted(unsupported))
            )
        return "\n  ".join(lines)

    semantic_keys = set(eo) - {"assertion", "assert_values", "element_visible", "visible_element", "element_state"}
    if semantic_keys:
        return (
            f"// TODO: assert step {num} cannot safely express "
            + ", ".join(sorted(semantic_keys))
        )

    # ── multi-value assert (refined plan populates assert_values) ────────────
    assert_values = eo.get("assert_values") or []
    # Filter out any prose that slipped in
    assert_values = [v for v in assert_values if v and not _is_prose(v)]

    if assert_values and refinement and refinement.get("grounded"):
        expr = _refined_locator_expr(target)
        if expr:
            lines = [f"await expect({expr}).toContainText({q(v)});" for v in assert_values]
            return "\n  ".join(lines)

    # ── single-value assert ──────────────────────────────────────────────────
    # Prefer text_content (visible DOM text) over assertion (may be prose)
    text = target.get("text_content") or eo.get("text_contains") or eo.get("visible_text")

    # Only fall back to assertion field if it is NOT prose
    if not text:
        candidate = eo.get("assertion")
        text = None if _is_prose(candidate) else candidate

    # Use refined locator for single-value assert if grounded
    if refinement and refinement.get("grounded"):
        expr = _refined_locator_expr(target)
        if expr:
            if text and not _is_prose(text):
                if "getByText" in expr and ".locator(" not in expr:
                    return f"await expect({expr}.first()).toBeVisible();"
                return f"await expect({expr}).toContainText({q(text)});"
            if "getByText" in expr and ".locator(" not in expr:
                return f"await expect({expr}.first).toBeVisible();"
            return f"await expect({expr}).toBeVisible();"

    # Fallback: heuristic locator — accessible name before guessed CSS
    if css in ("h1", "h2", "h3") and text:
        return f"await expect({_by_role_js('heading', text)}).toBeVisible();"

    aria = target.get("aria_label")
    if aria:
        expr = _by_label_js(aria)
        return (f"await expect({expr}).toContainText({q(text)});"
                if text else
                f"await expect({expr}).toBeVisible();")

    if css:
        return (f"await expect(page.locator({q(css)})).toContainText({q(text)});"
                if text else
                f"await expect(page.locator({q(css)})).toBeVisible();")

    if text:
        return f"await expect(page.getByText({q(text)}, {{ exact: false }})).toBeVisible();"

    return f"// TODO: assert step {num} has no locatable target — {step.get('description','')}"


# ─────────────────────────── step emitter ───────────────────────────────────

def emit_step(step: dict, base_url: str) -> tuple[str, bool]:
    """Return (playwright_line, is_todo).

    is_todo=True means the step could not be fully resolved and was emitted
    as a commented-out block — the caller should count it as a warning.
    """
    action     = step.get("action")
    target     = step.get("target", {}) or {}
    refinement = step.get("refinement")
    num        = step.get("step")

    # Refined plan stores value at the top level; raw plan uses input_value
    value = step.get("value") or step.get("input_value")

    # Steps that failed refinement get a prominent TODO comment
    if refinement and not refinement.get("grounded") and refinement.get("notes"):
        note = refinement["notes"]
        conf = refinement.get("confidence", 0)
        return (
            f"// TODO (refinement failed, conf={conf:.2f}): {_one_line(note)}\n"
            f"  // action={action!r}  locator={target.get('playwright_locator')!r}  value={value!r}",
            True,
        )

    if action == "navigate":
        url = value or target.get("value") or base_url
        return f"await page.goto({q(url)}, {{ waitUntil: 'load' }});", False

    if action in ("type", "select"):
        loc = locator(target, action, refinement)
        if loc is None:
            return f"// TODO: step {num} has no locatable target — {step.get('description','')}", True
        if value is None:
            return f"// TODO: step {num} ({action}) is missing value — {step.get('description','')}", True
        verb = "fill" if action == "type" else "selectOption"
        return f"await {loc}.{verb}({q(value)});", False

    if action == "click":
        loc = locator(target, action, refinement)
        if loc is None:
            return f"// TODO: step {num} click has no locatable target — {step.get('description','')}", True
        return f"await {loc}.click();", False

    if action == "wait":
        return "await page.waitForLoadState('load');", False

    if action == "assert":
        line = emit_assert(step, refinement)
        return line, line.startswith("// TODO")

    if action == "hover":
        loc = locator(target, action, refinement)
        if loc:
            return f"await {loc}.hover();", False

    if action == "press":
        key = value or "Enter"
        if key in ("\n", "\\n"):
            key = "Enter"
        loc = locator(target, action, refinement)
        if loc:
            return (
                f"await {loc}.focus();\n"
                f"  await page.keyboard.press({q(key)});",
                False,
            )
        return f"await page.keyboard.press({q(key)});", False

    if action == "scroll":
        loc = locator(target, action, refinement)
        if loc:
            return f"await {loc}.scrollIntoViewIfNeeded();", False

    if action == "terminate":
        return f"// Flow terminated intentionally at step {num}.", False

    return f"// TODO: step {num} has unhandled action {action!r} — {step.get('description','')}", True


def _compile_step(step: dict, base_url: str) -> tuple[str, bool]:
    """Compile a step and preserve low-confidence grounding in the artifact."""
    line, needs_review = emit_step(step, base_url)
    refinement = step.get("refinement") or {}
    confidence = refinement.get("confidence")
    if (
        refinement.get("grounded")
        and isinstance(confidence, (int, float))
        and confidence < 0.5
    ):
        note = f"// REVIEW: low-confidence grounding ({confidence:.2f}); verify this locator."
        return f"{note}\n  {line}", True
    return line, needs_review


# ──────────────────────────── run ───────────────────────────────────────────

def generate(plan_path: Path, output_path: Path, base_url_override: str | None = None) -> int:
    """Compile a plan file into a Playwright spec. Returns the number of TODO warnings."""
    if not plan_path.exists():
        raise SystemExit(f"ERROR: action plan not found at {plan_path}")

    parsed   = json.loads(plan_path.read_text())
    base_url = (base_url_override
                or parsed.get("workflow", {}).get("base_url")
                or config.base_url(None))
    title    = parsed.get("workflow", {}).get("title", "workflow")
    steps    = parsed.get("steps", [])
    is_refined = parsed.get("metadata", {}).get("refined", False)

    print(f"\n{BOLD}━━━ Playwright Script Generator ━━━{RESET}")
    print(f"  Plan:     {plan_path}")
    print(f"  Refined:  {CYAN}{'yes — using grounded locators' if is_refined else 'no — using heuristic fallback'}{RESET}")
    print(f"  Title:    {title}")
    print(f"  Base URL: {base_url}")
    print(f"  Steps:    {len(steps)}")
    print(f"  Output:   {output_path}\n")

    compiled = [(s, *_compile_step(s, base_url)) for s in steps]   # (step, line, is_todo)

    print(f"{BOLD}━━━ Step → Playwright mapping ━━━{RESET}")
    warnings = 0
    for s, line, is_todo in compiled:
        num    = s.get("step", "?")
        action = s.get("action", "?")
        desc   = s.get("description", "")
        ref    = s.get("refinement") or {}
        grounded = ref.get("grounded", False)
        conf     = ref.get("confidence")
        reclassified = ref.get("reclassified", False)

        if is_todo:
            warnings += 1

        source_tag = ""
        if is_refined:
            if grounded:
                source_tag = f" {GREEN}[refined conf={conf:.2f}]{RESET}"
            elif ref:
                source_tag = f" {YELLOW}[ungrounded conf={conf:.2f}]{RESET}"
            else:
                source_tag = f" {DIM}[no refinement data]{RESET}"
        if reclassified:
            source_tag += f" {YELLOW}[reclassified]{RESET}"

        print(f"\n  {BLUE}Step [{num}]{RESET} {YELLOW}{action}{RESET} — {desc}{source_tag}")
        mark = f"{YELLOW}⚠{RESET}" if is_todo else f"{GREEN}→{RESET}"
        for ln in line.splitlines():
            print(f"    {mark} {ln}")

    # Assemble the TS spec
    body = "\n".join(
        f"  // Step {s.get('step')}: {s.get('description','')}\n  {line}"
        for s, line, _ in compiled
    )
    code = (
        "import { test, expect } from '@playwright/test';\n\n"
        f"test({q(title)}, async ({{ page }}) => {{\n"
        f"{body}\n"
        "});\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(code)

    print(f"\n{BOLD}━━━ Done ━━━{RESET}")
    print(f"  ✓ Written to {GREEN}{output_path}{RESET}")
    if warnings:
        print(f"  {YELLOW}⚠ {warnings} step(s) emitted as TODO — review before running{RESET}")

    # Surface any known ambiguities from the refined plan
    ambiguities = parsed.get("metadata", {}).get("known_ambiguities", [])
    if ambiguities:
        print(f"\n  {YELLOW}Known ambiguities from refinement:{RESET}")
        for a in ambiguities:
            print(f"    • {a}")

    print(f"\n  Run with:  npx playwright test {output_path} --headed\n")
    return warnings


def main() -> None:
    ap = argparse.ArgumentParser(description="Compile a (refined) action plan into a Playwright spec.")
    ap.add_argument("plan", nargs="?", default="refined_action_plan.json",
                    help="Path to the (refined) action plan JSON")
    ap.add_argument("output", nargs="?", default="tests/generated.spec.ts",
                    help="Where to write the generated .spec.ts")
    ap.add_argument("--base-url", default=None,
                    help="Override the plan's base_url (default: plan value, then $QA_BASE_URL)")
    args = ap.parse_args()

    generate(Path(args.plan), Path(args.output), args.base_url)


if __name__ == "__main__":
    main()
