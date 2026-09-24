#!/usr/bin/env python3
"""generate.py — deterministically compile an action plan into a Playwright spec.

Supports two input schemas:
  • Raw action plan   — original parser output (aria_label / text_content / css_selector)
  • Refined plan      — output of refine_plan.py (target.playwright_locator / target.strategy
                        / target.locator_value / value / refinement.grounded)

When a step carries a grounded refined locator it is emitted directly.
Ungrounded or unrefined steps fall back to the original heuristic locator logic.
Unresolved required steps stay in the spec as TODO/REVIEW comments so a draft
remains inspectable, but the generated test fails before page actions.

    python -m qa_pipeline.generate outputs/plans/refined_action_plan.json outputs/tests/generated.spec.ts
"""
import re
import ast
import json
import argparse
import os
from pathlib import Path

from qa_pipeline import config
from qa_pipeline.runtime_auth import canonical_origin

GREEN, BLUE, YELLOW, DIM, BOLD, RESET = (
    "\033[92m", "\033[94m", "\033[93m", "\033[2m", "\033[1m", "\033[0m"
)
CYAN = "\033[96m"
INCOMPLETE_EXIT_CODE = 3


# ─────────────────────────── helpers ────────────────────────────────────────

def q(s) -> str:
    """Single-quoted JS/TS string literal with proper escaping."""
    s = "" if s is None else str(s)
    return (
        "'"
        + s.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        + "'"
    )


def _one_line(text) -> str:
    """Flatten text so a generated line comment cannot become executable code."""
    return " ".join(str("" if text is None else text).split())


def _fmt_conf(conf) -> str:
    """Format a refinement confidence for logs and comments without crashing."""
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        return "n/a"
    return f"{conf:.2f}"


def _has_unresolved(line: str) -> bool:
    """True when compiled output still contains a TODO or REVIEW comment."""
    for ln in str(line).splitlines():
        stripped = ln.strip()
        if stripped.startswith("// TODO") or stripped.startswith("// REVIEW"):
            return True
    return False


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
        lucide = re.fullmatch(
            r"(?P<tag>[a-z][\w-]*):has\((?:svg)?\[data-lucide=(?P<quote>[\"'])(?P<icon>[a-z0-9_-]+)(?P=quote)\]\)",
            value,
            re.I,
        )
        if lucide:
            tag = lucide.group("tag")
            icon = lucide.group("icon")
            value = (
                f'{tag}:has(svg[data-lucide="{icon}"]), '
                f'{tag}:has(svg.lucide-{icon})'
            )
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
    # Parse locator syntax, never execute model-supplied expressions. Quoted
    # parentheses and escaped quotes are data, not call delimiters.
    methods = {**snake_to_camel, **{v: v for v in snake_to_camel.values()},
               "nth": "nth", "first": "first", "last": "last"}

    def literal(node):
        value = ast.literal_eval(node)
        if isinstance(value, str):
            return q(value)
        if value is None or isinstance(value, (bool, int, float)):
            return json.dumps(value, allow_nan=False)
        raise ValueError("Unsupported locator argument")

    def render(node):
        if isinstance(node, ast.Name) and node.id == "page":
            return "page"
        if isinstance(node, ast.Attribute) and node.attr in {"first", "last"}:
            return f"{render(node.value)}.{node.attr}()"
        if not isinstance(node, ast.Call):
            raise ValueError("Expected a locator call")
        if isinstance(node.func, ast.Name):
            owner, name = "page", node.func.id
        elif isinstance(node.func, ast.Attribute):
            owner, name = render(node.func.value), node.func.attr
        else:
            raise ValueError("Unsupported locator call")
        if name not in methods or any(k.arg is None for k in node.keywords):
            raise ValueError("Unsupported locator method")
        parts = [literal(arg) for arg in node.args]
        if node.keywords:
            parts.append("{ " + ", ".join(f"{k.arg}: {literal(k.value)}" for k in node.keywords) + " }")
        return f"{owner}.{methods[name]}({', '.join(parts)})"

    try:
        return render(ast.parse(raw.strip(), mode="eval").body)
    except (SyntaxError, ValueError, TypeError):
        return None


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
    multi_field_description = field_value and "still shows" in str(field_value).lower()
    if field_value is not None and expr and "checked" not in eo and not multi_field_description:
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

    text_contains = eo.get("text_contains")
    if text_contains:
        if expr:
            if "getByText" in expr and ".locator(" not in expr:
                lines.append(f"await expect({expr}.first()).toBeVisible();")
                lines.append(f"await expect({expr}.first()).toContainText({q(text_contains)});")
            else:
                lines.append(f"await expect({expr}).toContainText({q(text_contains)});")
        else:
            lines.append(
                f"await expect(page.getByText({q(text_contains)}, {{ exact: false }})).toBeVisible();"
            )
        handled.add("text_contains")

    visible_text = eo.get("visible_text")
    if visible_text:
        lines.append(
            f"await expect(page.getByText({q(visible_text)}, {{ exact: false }}).filter({{ visible: true }}).first()).toBeVisible();"
        )
        handled.add("visible_text")

    visible_text_exact = eo.get("visible_text_exact")
    if visible_text_exact:
        lines.append(
            f"await expect(page.getByText({q(visible_text_exact)}, {{ exact: true }}).filter({{ visible: true }}).first()).toBeVisible();"
        )
        handled.add("visible_text_exact")

    visible_heading = eo.get("visible_heading")
    if visible_heading:
        lines.append(
            f"await expect(page.getByRole('heading', {{ name: {q(visible_heading)}, exact: true }}).filter({{ visible: true }}).first()).toBeVisible();"
        )
        handled.add("visible_heading")

    if isinstance(eo.get("title"), str) and expr:
        lines.append(f"await expect({expr}).toHaveAttribute('title', {q(eo['title'])});")
        handled.add("title")
    accessible_name = eo.get("accessible_name")
    if isinstance(accessible_name, str) and expr:
        lines.append(f"await expect({expr}).toHaveAccessibleName({q(accessible_name)});")
        handled.add("accessible_name")

    visibility = str(eo.get("visible", eo.get("visibility", ""))).lower()
    if visibility in {"true", "false", "visible", "hidden"} and expr:
        matcher = "toBeVisible" if visibility in {"true", "visible"} else "toBeHidden"
        lines.append(f"await expect({expr}).{matcher}();")
        handled.update(key for key in ("visible", "visibility") if key in eo)

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
            lines = [
                f"await expect({expr}).toBeVisible();"
                if (
                    target.get("title_only_name") == v
                    and target.get("strategy") == "role"
                    and target.get("locator_value") == v
                )
                else f"await expect({expr}).toContainText({q(v)});"
                for v in assert_values
            ]
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
                return f"await expect({expr}.first()).toBeVisible();"
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

    return (
        f"// TODO: assert step {num} has no locatable target — "
        f"{_one_line(step.get('description', ''))}"
    )


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

    # Explicit failed grounding is unresolved even when notes are absent.
    # Raw plans with no refinement block still use heuristic locators.
    if refinement and refinement.get("grounded") is False:
        note = refinement.get("notes") or "explicit failed grounding"
        conf = refinement.get("confidence", 0)
        return (
            f"// TODO (refinement failed, conf={_fmt_conf(conf)}): {_one_line(note)}\n"
            f"  // action={action!r}  locator={target.get('playwright_locator')!r}  value={value!r}",
            True,
        )

    if action == "navigate":
        url = value or target.get("value") or base_url
        return f"await page.goto({q(url)}, {{ waitUntil: 'load' }});", False

    if action in ("type", "select"):
        loc = locator(target, action, refinement)
        if loc is None:
            return (
                f"// TODO: step {num} has no locatable target — "
                f"{_one_line(step.get('description', ''))}",
                True,
            )
        if value is None:
            return (
                f"// TODO: step {num} ({action}) is missing value — "
                f"{_one_line(step.get('description', ''))}",
                True,
            )
        verb = "fill" if action == "type" else "selectOption"
        return f"await {loc}.{verb}({q(value)});", False

    if action == "click":
        loc = locator(target, action, refinement)
        if loc is None:
            return (
                f"// TODO: step {num} click has no locatable target — "
                f"{_one_line(step.get('description', ''))}",
                True,
            )
        return f"await {loc}.click();", False

    if action == "wait":
        return "await page.waitForLoadState('load');", False

    if action == "assert":
        line = emit_assert(step, refinement)
        return line, _has_unresolved(line)

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

    return (
        f"// TODO: step {num} has unhandled action {action!r} — "
        f"{_one_line(step.get('description', ''))}",
        True,
    )


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
    return line, needs_review or _has_unresolved(line)


# ──────────────────────────── run ───────────────────────────────────────────

def generate(plan_path: Path, output_path: Path, base_url_override: str | None = None, runtime_auth: bool = False) -> int:
    """Compile a plan file into a Playwright spec. Returns the number of TODO warnings."""
    if not plan_path.exists():
        raise SystemExit(f"ERROR: action plan not found at {plan_path}")

    parsed   = json.loads(plan_path.read_text())
    base_url = (base_url_override
                or parsed.get("workflow", {}).get("base_url")
                or config.base_url(None))
    if runtime_auth:
        canonical_origin(base_url)
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
        desc   = _one_line(s.get("description", ""))
        ref    = s.get("refinement") or {}
        grounded = ref.get("grounded", False)
        conf     = ref.get("confidence")
        reclassified = ref.get("reclassified", False)

        if is_todo:
            warnings += 1

        source_tag = ""
        if is_refined:
            if grounded:
                source_tag = f" {GREEN}[refined conf={_fmt_conf(conf)}]{RESET}"
            elif ref:
                source_tag = f" {YELLOW}[ungrounded conf={_fmt_conf(conf)}]{RESET}"
            else:
                source_tag = f" {DIM}[no refinement data]{RESET}"
        if reclassified:
            source_tag += f" {YELLOW}[reclassified]{RESET}"

        print(f"\n  {BLUE}Step [{num}]{RESET} {YELLOW}{action}{RESET} — {desc}{source_tag}")
        mark = f"{YELLOW}⚠{RESET}" if is_todo else f"{GREEN}→{RESET}"
        for ln in line.splitlines():
            print(f"    {mark} {ln}")

    if not steps:
        warnings = 1

    incomplete = warnings > 0
    parts: list[str] = []
    if incomplete:
        if not steps:
            reason = "Generated spec is incomplete: empty workflow has no compiled steps."
        else:
            reason = (
                f"Generated spec is incomplete: {warnings} required step(s) were not compiled. "
                "Inspect TODO/REVIEW comments; this is not a passing QA run."
            )
        parts.append(f"  throw new Error({q(reason)});")
    for s, line, _ in compiled:
        parts.append(f"  // Step {s.get('step')}: {_one_line(s.get('description', ''))}")
        parts.append(f"  {line}")
    body = "\n".join(parts)
    import_source = "@playwright/test"
    runtime_setup = ""
    if runtime_auth:
        fixture_file = Path(__file__).resolve().parent.parent / "runtime" / "auth-fixture.ts"
        if not fixture_file.exists():
            raise RuntimeError(
                "runtime authentication fixture is unavailable; use a source or editable checkout"
            )
        fixture = fixture_file.with_suffix("")
        try:
            import_source = os.path.relpath(fixture, output_path.resolve().parent).replace(os.sep, "/")
        except ValueError as exc:
            raise RuntimeError(
                "runtime authentication fixture and generated test must be on the same filesystem drive"
            ) from exc
        if not import_source.startswith("."):
            import_source = "./" + import_source
        runtime_setup = f"test.use({{ baseURL: {q(base_url)} }});\n\n"
    code = (
        f"import {{ test, expect }} from '{import_source}';\n\n"
        f"{runtime_setup}"
        f"test({q(title)}, async ({{ page }}) => {{\n"
        f"{body}\n"
        "});\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(code)

    print(f"\n{BOLD}━━━ Done ━━━{RESET}")
    print(f"  ✓ Written to {GREEN}{output_path}{RESET}")
    if incomplete:
        print(
            f"  {YELLOW}⚠ Incomplete generation — the spec fails before page actions "
            f"and is not a passing QA run{RESET}"
        )
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
    ap.add_argument("plan", nargs="?", default=str(config.DEFAULT_REFINED_PLAN),
                    help="Path to the (refined) action plan JSON")
    ap.add_argument("output", nargs="?", default=str(config.DEFAULT_SPEC),
                    help="Where to write the generated .spec.ts")
    ap.add_argument("--base-url", default=None,
                    help="Override the plan's base_url (default: plan value, then $QA_BASE_URL)")
    ap.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Export an inspectable draft with exit 0 when required steps are unresolved. "
             "The generated spec still fails before page actions.",
    )
    ap.add_argument("--runtime-auth", action="store_true",
                    help="emit a spec that requires explicit runtime authentication")
    args = ap.parse_args()

    warnings = generate(Path(args.plan), Path(args.output), args.base_url, args.runtime_auth)
    if warnings and not args.allow_incomplete:
        raise SystemExit(INCOMPLETE_EXIT_CODE)


if __name__ == "__main__":
    main()
