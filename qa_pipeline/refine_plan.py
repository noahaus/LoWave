"""
refine_plan.py
==============
One script. Input: an action plan produced by the steps parser (the
action_plan.json schema). Output: a refined action plan, grounded against the
LIVE DOM of the running app, in the same schema the Playwright generator reads.

    parse_steps -> action_plan.json --> [THIS SCRIPT] --> refined_action_plan.json --> generate

What it does, step by step
--------------------------
1. Loads the action plan and adapts it internally (the parser's css_selectors are
   treated as UNVERIFIED hints, because the steps LLM cannot see the DOM; the
   human-readable `description` is the real signal).
2. Walks the flow in a browser. Before each step it snapshots only the visible,
   interactive elements (raw HTML would blow the context window).
3. An LLM grounds each fuzzy step against that snapshot: picks the real element,
   emits a DURABLE Playwright locator, extracts the value, and may RECLASSIFY the
   action (e.g. a native <select> mislabelled as a click).
4. Executes the step and asserts the expected result. Low confidence or a failed
   action triggers a re-snapshot + re-ground (self-heal).
5. Writes the refined plan back out, with a per-step `refinement` block
   (confidence / grounded / reclassified_from / notes) and populated
   `metadata.known_ambiguities` for your review gate.

Requires a RUNNING app — DOM grounding needs a live DOM.

    pip install -e ".[anthropic]"   # or [openai] / [google] / [ollama]
    playwright install chromium
    python -m qa_pipeline.refine_plan --plan action_plan.json --out refined_action_plan.json

The grounding LLM is provider-agnostic — pick a backend with --backend
(anthropic | openai | google | ollama) or the LLM_BACKEND env var. Keep
temperature=0 for determinism.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, expect, Page, Locator

from langchain_core.prompts import ChatPromptTemplate

from datetime import datetime

from qa_pipeline import config
from qa_pipeline.llm import build_llm


def log(msg: str, level: str = "INFO") -> None:
    """Print a timestamped, levelled log line to stdout."""
    icons = {"INFO": "i", "OK": "v", "WARN": "!", "ERROR": "x", "STEP": ">", "RETRY": "~"}
    icon = icons.get(level, " ")
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{icon}] {msg}", flush=True)


# ===========================================================================
# 1. Schemas
# ===========================================================================

ActionType = Literal[
    "click", "type", "select", "hover", "scroll",
    "navigate", "wait", "assert", "drag", "press", "terminate",
]
LocatorStrategy = Literal["role", "label", "text", "testid", "placeholder", "css"]


class FuzzyStep(BaseModel):
    """Internal representation after adapting the parser's plan."""
    step_number: int
    step_id: str
    action: ActionType
    target: str                       # free-text description (+ unverified hint)
    value: Optional[str] = None
    expected_result: Optional[str] = None
    original_selector: Optional[str] = None


class RefinedStep(BaseModel):
    """Structured output from the grounding LLM."""
    step_number: int
    action: ActionType = Field(description="Corrected action; reclassify if the DOM contradicts it.")
    element_index: Optional[int] = Field(default=None, description="Index in the DOM snapshot; null for navigate/wait/scroll.")
    locator_strategy: LocatorStrategy = Field(description="Durable locator kind; prefer testid>role>label>placeholder>text>css.")
    locator_value: str = Field(description="Accessible name / label / test id / text / css selector.")
    role_name: Optional[str] = Field(default=None, description="ARIA role when strategy=='role'.")
    value: Optional[str] = None
    expected_result: str
    confidence: float = Field(ge=0.0, le=1.0)
    reclassified: bool = Field(default=False, description="True if you changed the action type from the input.")
    notes: Optional[str] = Field(default=None, description="Flag ambiguity here (the ⚠️ convention).")
    assert_values: list[str] = Field(default_factory=list, description="For assert steps: the literal visible strings from the DOM to check. Each entry becomes a separate toContainText/toBeVisible assertion. NEVER use description prose here — only text you can see in the DOM snapshot.")


# ===========================================================================
# 2. Live-DOM extraction (runs in the browser; returns a compact element list)
# ===========================================================================

_EXTRACT_JS = r"""
() => {
  const sel = 'a,button,input,select,textarea,summary,[role],[onclick],[tabindex]:not([tabindex="-1"]),h1,h2,h3,h4,h5,h6,[data-testid],[data-test-id],[class*="badge"]';
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    return r.width > 1 && r.height > 1 &&
           s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const els = Array.from(document.querySelectorAll(sel)).filter(isVisible);
  return els.map((el, i) => {
    el.setAttribute('data-ai-index', String(i));   // ephemeral handle for THIS snapshot
    const r = el.getBoundingClientRect();
    const label = el.getAttribute('aria-label') ||
                  (el.labels && el.labels[0] && el.labels[0].innerText) || null;
    const tag = el.tagName.toLowerCase();
    const explicitRole = el.getAttribute('role');
    const implicitRole = explicitRole || (
      tag === 'select'   ? 'listbox'   :
      tag === 'input' && el.type === 'checkbox' ? 'checkbox' :
      tag === 'input' && el.type === 'radio'    ? 'radio'    :
      tag === 'input'    ? 'textbox'   :
      tag === 'textarea' ? 'textbox'   :
      tag === 'button'   ? 'button'    :
      tag === 'a'        ? 'link'      :
      tag === 'summary'  ? 'button'    :
      null
    );
    return {
      index: i,
      tag: tag,
      role: implicitRole,
      explicit_role: explicitRole,
      type: el.getAttribute('type'),
      text: (el.innerText || el.value || '').trim().slice(0, 80),
      label: label,
      placeholder: el.getAttribute('placeholder'),
      name: el.getAttribute('name'),
      id: el.id || null,
      testid: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || null,
      bg: window.getComputedStyle(el).backgroundColor,   // helps match "blue button"
      box: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
    };
  });
}
"""


async def snapshot_interactive_dom(page: Page) -> list[dict[str, Any]]:
    log(f"Snapshotting interactive DOM on: {page.url}")
    elements = await page.evaluate(_EXTRACT_JS)
    log(f"DOM snapshot complete — {len(elements)} interactive element(s) found")
    return elements


# ===========================================================================
# 3. Grounding chain (LangChain + structured output)
# ===========================================================================

_SYSTEM = """You are a QA grounding engine. You receive ONE fuzzy step from a \
steps-derived test plan plus a JSON list of the interactive elements currently \
visible in the app's DOM (each with an `index` and attributes: text, role, label, \
placeholder, testid, tag, background colour, bounding box).

Resolve the fuzzy step to a concrete, reproducible RefinedStep.

Rules:
- Pick the single best-matching element; return its `index` as `element_index`.
- Emit the MOST DURABLE locator, preferring: testid > role(+accessible name) >
  label > placeholder > text > css.
- Match on semantics first (text/label/role/name); use position (bounding box) and
  colour only to break ties, e.g. "blue button bottom-right".
- The step may carry a parser-guessed css selector marked UNVERIFIED. Treat it as a
  weak hint only; NEVER trust it over what you actually see in the element list, and
  NEVER emit an element that is not in the list.
- RECLASSIFY the action when the matched element contradicts it: if the element is a
  native <select> but the action is 'click', return action 'select' with the option
  in `value`; if a 'type' target is actually a button/link, fix it. Set
  reclassified=true whenever you change the action type.
- `role_name` MUST be the exact ARIA role string of the matched element (e.g.
  "region", "button", "textbox", "heading", "link", "dialog"). NEVER leave it null
  when strategy=="role" and NEVER default it to "button" — use the actual role from
  the DOM snapshot (the `role` field on the element, or infer from tag: div[role]=
  "region"/"section", input="textbox", a="link", button="button", h1-h6="heading").
- For a native <select> element (tag="select", role="listbox"): ALWAYS use
  strategy="label" with the element's aria-label or associated label text as
  locator_value. Never use strategy="role" for selects — getByLabel is more durable
  and avoids strict-mode ambiguity. The action should be "select", not "click".
- For navigate/wait/scroll with no element, set element_index=null and
  locator_value="n/a".
- If no element is a confident match, return your best guess with confidence < 0.5
  and explain the ambiguity in `notes` (the ⚠️ flag).
- expected_result: restate what should visibly change, grounded in the real UI.

ASSERT STEPS — special rules:
- `value` must be a SHORT literal string that is ACTUALLY VISIBLE in the DOM snapshot.
  NEVER copy the step description into `value`. The description is human prose explaining
  intent; it will never literally appear in the UI.
- `assert_values` is a list of every individual visible string you want to verify.
  Use this when a step checks multiple fields (e.g. title, priority, date, description).
  Each entry must be a short literal string visible in the DOM (e.g. ["first task", "Low",
  "1996-07-04", "this is a description"]).  Leave empty [] for non-assert steps.
- If the success message in the DOM is "Task created" but the description says
  "Task created successfully", use "Task created" — what is VISIBLE wins.
- If the assert target is a container (role=region/section/article), set the locator
  to that container and populate assert_values with the strings it should contain."""

_HUMAN = """FUZZY STEP:
{fuzzy_step}

RECENT CONTEXT (already executed, newest last):
{history}

CURRENT DOM (visible interactive elements only):
{dom}

REMINDER — if this is an assert step:
- Set `assert_values` to the literal visible strings from the DOM above (not description prose).
- Set `value` to the single most important visible string, or null if assert_values covers it.

Return the grounded RefinedStep."""


def build_refiner(backend: str, model: Optional[str], temperature: float = 0.0):
    log(f"Building grounding chain with backend={backend} model={model or 'default'}")
    llm = build_llm(backend, model, temperature)
    structured = llm.with_structured_output(RefinedStep)
    prompt = ChatPromptTemplate.from_messages([("system", _SYSTEM), ("human", _HUMAN)])
    log("Grounding chain ready")
    return prompt | structured


async def ground_step(chain, fuzzy: FuzzyStep, dom: list[dict], history: list[str]) -> RefinedStep:
    log(f"  Grounding step {fuzzy.step_number}: [{fuzzy.action}] {fuzzy.target[:80]}...")
    log(f"  Sending {len(dom)} DOM elements + {min(len(history), 6)} history entries to LLM")
    step = await chain.ainvoke({
        "fuzzy_step": fuzzy.model_dump_json(indent=2),
        "history": "\n".join(history[-6:]) or "(none)",
        "dom": json.dumps(dom, ensure_ascii=False),
    })
    step.step_number = fuzzy.step_number           # keep numbering authoritative
    step.reclassified = step.reclassified or (step.action != fuzzy.action)

    # If the LLM forgot to carry over the value for actions that need one, fall back to fuzzy
    if step.value is None and fuzzy.value is not None and step.action in ("type", "press", "select", "navigate"):
        log(f"  LLM returned value=None for [{step.action}] — restoring fuzzy value: {fuzzy.value!r}", "WARN")
        step.value = fuzzy.value

    log(f"  LLM grounded to: {step.locator_strategy}:{step.locator_value!r}  confidence={step.confidence:.2f}")

    # Print the specific DOM element the LLM chose
    if step.element_index is not None:
        matched = next((el for el in dom if el.get("index") == step.element_index), None)
        if matched:
            log(f"  Matched DOM element:")
            log(f"    index      : {matched.get('index')}")
            log(f"    tag        : {matched.get('tag')}  type={matched.get('type')}  role={matched.get('role')}")
            log(f"    text       : {matched.get('text', '')!r}")
            log(f"    label      : {matched.get('label')!r}")
            log(f"    placeholder: {matched.get('placeholder')!r}")
            log(f"    testid     : {matched.get('testid')!r}")
            log(f"    id         : {matched.get('id')!r}")
            log(f"    box        : {matched.get('box')}")
        else:
            log(f"  Matched element index {step.element_index} not found in snapshot", "WARN")
    else:
        log(f"  No DOM element index (expected for navigate/wait/scroll)")

    if step.reclassified:
        log(f"  Action reclassified: [{fuzzy.action}] -> [{step.action}]", "WARN")
    return step


# ===========================================================================
# 4. Locator building — a live Locator, and a durable string for the plan
# ===========================================================================

def to_locator(page: Page, step: RefinedStep) -> Locator:
    s, v = step.locator_strategy, step.locator_value
    if s == "role" and step.role_name:
        return page.get_by_role(step.role_name, name=v) if v else page.get_by_role(step.role_name)
    if s == "label":
        return page.get_by_label(v)
    if s == "placeholder":
        return page.get_by_placeholder(v)
    if s == "text":
        return page.get_by_text(v)
    if s == "testid":
        return page.get_by_test_id(v)
    if step.element_index is not None:                       # css / fallback
        return page.locator(f'[data-ai-index="{step.element_index}"]')
    return page.locator(v)


_KNOWN_ARIA_ROLES = {
    "region", "section", "article", "main", "navigation", "heading",
    "textbox", "checkbox", "combobox", "listbox", "option", "link",
    "img", "table", "row", "cell", "dialog", "alert", "banner",
    "button", "radio", "menuitem", "tab", "tabpanel", "tree", "treeitem",
}


def locator_expr(step: RefinedStep) -> str:
    """Playwright locator as a source string, for the generator to emit directly.

    Select elements have implicit role=listbox but getByLabel is always more
    durable — when role==listbox and the locator_value is an accessible name
    (not a role keyword), we emit get_by_label instead.
    """
    q = json.dumps
    s, v = step.locator_strategy, step.locator_value

    if s == "label":
        return f"get_by_label({q(v)})"

    if s == "role":
        role = step.role_name or (v if v in _KNOWN_ARIA_ROLES else "button")

        # <select aria-label="Priority"> → getByLabel('Priority') is more durable
        # than getByRole('listbox', { name: 'Priority' }) and avoids strict-mode issues
        if role == "listbox" and v and v not in _KNOWN_ARIA_ROLES:
            return f"get_by_label({q(v)})"

        name_part = f", name={q(v)}" if (v and v not in _KNOWN_ARIA_ROLES) else ""
        return f"get_by_role({q(role)}{name_part})"

    if s == "placeholder":
        return f"get_by_placeholder({q(v)})"
    if s == "text":
        return f"get_by_text({q(v)})"
    if s == "testid":
        return f"get_by_test_id({q(v)})"
    return f"locator({q(v)})"


# ===========================================================================
# 5. Execute one grounded step + assert its expected result
# ===========================================================================

async def execute_step(page: Page, step: RefinedStep, base_url: str) -> None:
    loc = to_locator(page, step)
    a = step.action
    log(f"  Executing [{a}] on {step.locator_strategy}:{step.locator_value!r}")
    if a == "navigate":
        target_url = step.value or base_url
        log(f"  Navigating to: {target_url}")
        await page.goto(target_url)
    elif a == "click":
        log(f"  Clicking element")
        await loc.click()
    elif a == "type":
        log(f"  Typing value: {step.value!r}")
        await loc.fill(step.value or "")
    elif a == "press":
        log(f"  Pressing key: {step.value or 'Enter'!r}")
        await loc.press(step.value or "Enter")
    elif a == "select":
        log(f"  Selecting option: {step.value!r}")
        await loc.select_option(label=step.value) if step.value else None
    elif a == "hover":
        log(f"  Hovering over element")
        await loc.hover()
    elif a == "scroll":
        log(f"  Scrolling element into view")
        await loc.scroll_into_view_if_needed()
    elif a == "wait":
        ms = int(step.value) if (step.value or "").isdigit() else 1000
        log(f"  Waiting {ms}ms")
        await page.wait_for_timeout(ms)
    elif a == "assert":
        # Literal visible strings the model extracted (assert_values); never the
        # prose description.
        values = [v for v in (step.assert_values or []) if v]
        if not values and step.value:
            values = [step.value]
        if len(values) > 1:
            # Multi-value assert: every string must appear in the shared container.
            for val in values:
                log(f"  Asserting element contains: {val!r}")
                await expect(loc).to_contain_text(val, timeout=5000)
        elif len(values) == 1:
            val = values[0]
            try:
                log(f"  Asserting element contains: {val!r}")
                await expect(loc).to_contain_text(val, timeout=5000)
            except AssertionError:
                # The grounded locator and the asserted value disagree — the model
                # pointed at the wrong element. Re-derive the locator FROM the text
                # being verified so the two cannot diverge, and write it back so the
                # generated spec emits the same durable locator.
                log(f"  Grounded locator did not contain {val!r} — re-grounding via text", "WARN")
                await expect(page.get_by_text(val)).to_contain_text(val, timeout=5000)
                step.locator_strategy = "text"
                step.locator_value = val
                step.role_name = None
                step.element_index = None
                log(f"  Re-grounded assert to get_by_text({val!r})", "OK")
        else:
            log(f"  Asserting element is visible")
            await expect(loc).to_be_visible(timeout=5000)
    elif a == "drag":
        raise NotImplementedError("Add drag source/target handling for your app.")
    elif a == "terminate":
        log(f"  Terminate marker — no DOM action to perform")
    log(f"  Action complete", "OK")


# ===========================================================================
# 6. Adapter — parser plan -> FuzzyStep list  (schema + values + navigate fixes)
# ===========================================================================

_QUOTED = re.compile(r"'([^']*)'")


def _first_quoted(text: str) -> Optional[str]:
    m = _QUOTED.search(text or "")
    return m.group(1) if m else None


def _is_element_ref(sel: Optional[str]) -> bool:
    return bool(sel) and (sel.startswith("#") or sel.startswith("."))


def adapt_plan(plan: dict) -> tuple[list[FuzzyStep], str, list[str]]:
    base_url = plan.get("workflow", {}).get("base_url", "")
    workflow_name = plan.get("workflow", {}).get("name", "(unnamed)")
    total = len(plan.get("steps", []))
    log(f"Adapting plan: {workflow_name!r}  |  {total} step(s)  |  base_url={base_url!r}")
    steps: list[FuzzyStep] = []
    warnings: list[str] = []
    seen_first_navigate = False

    for s in plan["steps"]:
        step_no = s["step"]
        action = s["action"]
        desc = s.get("description", "")
        hint = (s.get("target") or {}).get("css_selector")
        value = _first_quoted(desc)

        target = desc + (f"  [parser-guessed selector '{hint}' — UNVERIFIED]" if hint else "")

        if action == "navigate":
            if not seen_first_navigate:
                seen_first_navigate = True
                value = base_url
                log(f"  Step {step_no}: first navigate -> using base_url {base_url!r}")
            elif _is_element_ref(hint):
                action = "wait"       # spurious "navigate to #page" = a transition result
                value = None
                msg = (f"step {step_no}: downgraded spurious navigate->{hint} to a wait; "
                       f"consider an assert on the destination page instead.")
                warnings.append(msg)
                log(f"  Step {step_no}: {msg}", "WARN")
            else:
                value = value or base_url

        steps.append(FuzzyStep(
            step_number=step_no,
            step_id=s.get("id", f"step_{step_no}"),
            action=action,
            target=target,
            value=value,
            expected_result=desc,
            original_selector=hint,
        ))
    log(f"Plan adapted — {len(steps)} fuzzy step(s) ready  ({len(warnings)} pre-flight warning(s))")
    for w in warnings:
        log(f"  {w}", "WARN")
    return steps, base_url, warnings


# ===========================================================================
# 7. Self-healing refine loop
# ===========================================================================

async def refine(
    plan: dict,
    start_url: Optional[str],
    backend: str,
    model: Optional[str],
    headless: bool,
    max_retries: int,
    confidence_floor: float,
) -> dict:
    fuzzy_steps, base_url, warnings = adapt_plan(plan)
    start_url = start_url or base_url
    chain = build_refiner(backend, model)

    refined_steps: list[RefinedStep] = []
    original_by_no = {s["step"]: s for s in plan["steps"]}
    history: list[str] = []
    ambiguities: list[str] = list(warnings)

    log(f"Launching Chromium browser (headless={headless})")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page()
        log(f"Navigating to start URL: {start_url}")
        await page.goto(start_url)
        log(f"Page loaded: {page.url!r}", "OK")

        total = len(fuzzy_steps)
        for idx, fuzzy in enumerate(fuzzy_steps, 1):
            log("")
            log(f"--- Step {fuzzy.step_number} / {total}  [{fuzzy.action}] ---", "STEP")
            log(f"    Target: {fuzzy.target[:100]}")
            if fuzzy.value:
                log(f"    Value : {fuzzy.value!r}")

            if fuzzy.action == "terminate":
                log(f"  Terminate marker — end of flow, skipping DOM grounding/execution", "OK")
                step = RefinedStep(
                    step_number=fuzzy.step_number,
                    action="terminate",
                    element_index=None,
                    locator_strategy="css",
                    locator_value="n/a",
                    value=None,
                    expected_result=fuzzy.expected_result or "Flow complete",
                    confidence=1.0,
                    reclassified=False,
                    notes=None,
                    assert_values=[],
                )
                refined_steps.append(step)
                history.append(f"step {step.step_number}: terminate (end of flow)")
                continue

            attempt = 0
            while True:
                attempt += 1
                dom = await snapshot_interactive_dom(page)
                step = await ground_step(chain, fuzzy, dom, history)

                if step.confidence < confidence_floor and attempt <= max_retries:
                    log(f"  Confidence {step.confidence:.2f} below floor {confidence_floor} — retrying (attempt {attempt}/{max_retries})", "RETRY")
                    history.append(f"[retry {attempt}] low confidence, step {fuzzy.step_number}")
                    await page.wait_for_timeout(500)
                    continue
                try:
                    await execute_step(page, step, base_url)
                    log(f"  Waiting for network idle...")
                    await page.wait_for_load_state("networkidle", timeout=5000)

                    # Execution succeeded — stamp the step as definitively grounded
                    step.notes = (step.notes or "").replace("⚠️", "").strip() or None
                    log(f"  Step executed successfully — marking as grounded", "OK")

                    refined_steps.append(step)
                    # Only flag as ambiguous if confidence is still low AND it actually ran
                    if step.confidence < confidence_floor:
                        msg = f"step {step.step_number}: ran OK but low confidence ({step.confidence:.2f}) — verify locator is durable"
                        ambiguities.append(msg)
                        log(f"  Flagged: {msg}", "WARN")
                    if step.reclassified:
                        msg = f"step {step.step_number}: action reclassified to '{step.action}' after inspecting the DOM."
                        ambiguities.append(msg)
                        log(f"  Flagged: {msg}", "WARN")
                    history.append(f"step {step.step_number}: {step.action} -> "
                                   f"{step.locator_strategy}:{step.locator_value} "
                                   f"(conf {step.confidence:.2f})")
                    log(f"  Step {fuzzy.step_number} complete  (conf={step.confidence:.2f})", "OK")
                    break
                except Exception as e:
                    if attempt <= max_retries:
                        log(f"  Execution failed (attempt {attempt}/{max_retries}): {e} — retrying", "RETRY")
                        history.append(f"[retry {attempt}] exec failed, step {fuzzy.step_number}: {e}")
                        await page.wait_for_timeout(500)
                        continue
                    log(f"  Step {fuzzy.step_number} FAILED after {attempt} attempt(s): {e}", "ERROR")
                    step.notes = f"⚠️ FAILED after {attempt} attempts: {e}. " + (step.notes or "")
                    step.confidence = min(step.confidence, 0.2)
                    refined_steps.append(step)
                    ambiguities.append(f"step {step.step_number}: FAILED to execute — {e}")
                    history.append(f"step {step.step_number}: FAILED, flagged")
                    break

        log("")
        log(f"All steps processed. Closing browser.")
        await browser.close()
        log("Browser closed.", "OK")

    return _serialize(plan, fuzzy_steps, refined_steps, original_by_no, ambiguities, base_url)


# ===========================================================================
# 8. Serialize back into the parser's schema (drop-in for the generator)
# ===========================================================================

def _serialize(plan, fuzzy_steps, refined_steps, original_by_no, ambiguities, base_url) -> dict:
    fuzzy_by_no = {f.step_number: f for f in fuzzy_steps}
    out_steps = []
    for r in refined_steps:
        original = original_by_no.get(r.step_number, {})
        fuzzy = fuzzy_by_no.get(r.step_number)
        out_steps.append({
            "step": r.step_number,
            "id": original.get("id", f"step_{r.step_number}"),
            "action": r.action,
            "description": original.get("description", ""),
            "value": r.value,
            "target": {
                "playwright_locator": locator_expr(r),   # e.g. get_by_role("button", name="Sign In")
                "strategy": r.locator_strategy,
                "locator_value": r.locator_value,
                "role": r.role_name,
                "css_selector": r.locator_value if r.locator_strategy == "css" else None,
                "original_selector": fuzzy.original_selector if fuzzy else None,
            },
            "expected_outcome": {
                "assertion": r.expected_result,
                "assert_values": r.assert_values,
            },
            "refinement": {
                "confidence": round(r.confidence, 2),
                # grounded=True if the step actually executed (no FAILED note), regardless of LLM confidence
                "grounded": not (r.notes or "").startswith("⚠️ FAILED"),
                "executed": not (r.notes or "").startswith("⚠️ FAILED"),
                "reclassified": r.reclassified,
                "notes": r.notes or None,
            },
        })

    out = json.loads(json.dumps(plan))            # deep copy of workflow/metadata
    out["steps"] = out_steps
    out.setdefault("metadata", {})
    out["metadata"]["total_steps"] = len(out_steps)
    out["metadata"]["known_ambiguities"] = ambiguities
    out["metadata"].setdefault("recommended_edge_cases", [])
    out["metadata"]["refined"] = True
    return out


# ===========================================================================
# 9. CLI
# ===========================================================================

def main() -> None:
    ap = argparse.ArgumentParser(description="Refine a steps-derived action plan against the live DOM.")
    ap.add_argument("--plan", required=True, help="Path to the input action_plan.json")
    ap.add_argument("--out", default="refined_action_plan.json", help="Where to write the refined plan")
    ap.add_argument("--url", default=None, help="Start URL (defaults to workflow.base_url, then $QA_BASE_URL)")
    ap.add_argument("--backend", default=None,
                    choices=["ollama", "anthropic", "openai", "google"],
                    help="LLM provider for grounding (default: $LLM_BACKEND or anthropic)")
    ap.add_argument("--model", default=None, help="override the backend's default model")
    ap.add_argument("--headed", action="store_true", help="Run the browser headed (visible)")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--confidence-floor", type=float, default=0.5)
    args = ap.parse_args()

    backend = config.backend(args.backend)

    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)

    refined = asyncio.run(refine(
        plan,
        start_url=args.url,
        backend=backend,
        model=args.model,
        headless=not args.headed,
        max_retries=args.max_retries,
        confidence_floor=args.confidence_floor,
    ))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(refined, f, indent=2, ensure_ascii=False)

    amb = refined["metadata"]["known_ambiguities"]
    print(f"Wrote {args.out}  ({refined['metadata']['total_steps']} steps)")
    if amb:
        print(f"\n{len(amb)} item(s) flagged for review:")
        for a in amb:
            print(f"  - {a}")
    else:
        print("No ambiguities flagged.")


if __name__ == "__main__":
    main()
