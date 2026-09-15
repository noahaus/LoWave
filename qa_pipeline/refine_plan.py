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
2. Walks the flow in a browser. Before each step it snapshots visible controls:
   native interactive elements plus custom clickable widgets (chips/tabs/spans
   with a pointer cursor). Raw HTML would blow the context window.
3. An LLM grounds each fuzzy step against a *compacted* snapshot (ranked, capped)
   so the prompt stays under typical API token-per-minute limits. It picks the
   real element, emits a DURABLE Playwright locator, extracts the value, and may
   RECLASSIFY the action (e.g. a native <select> mislabelled as a click).
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
from pathlib import Path
import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, expect, Page, Locator, Error as PlaywrightError

from langchain_core.prompts import ChatPromptTemplate

from datetime import datetime

from qa_pipeline import config
from qa_pipeline.llm import build_llm
from qa_pipeline.runtime_auth import acquire_storage_state, canonical_origin


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
    expected_outcome: dict[str, Any] = Field(default_factory=dict)
    original_selector: Optional[str] = None


class RefinedStep(BaseModel):
    """Structured output from the grounding LLM."""
    step_number: int
    action: ActionType = Field(description="Corrected action; reclassify if the DOM contradicts it.")
    element_index: Optional[int] = Field(default=None, description="Index in the DOM snapshot; null for navigate/wait/scroll.")
    locator_strategy: LocatorStrategy = Field(description="Durable locator kind; prefer testid>role>label>placeholder>text>css.")
    locator_value: str = Field(description="Accessible name / label / test id / text / css selector.")
    role_name: Optional[str] = Field(default=None, description="ARIA role when strategy=='role'.")
    host_tag: Optional[str] = Field(default=None, description="Matched element's tag; used to scope text locators.")
    title_only_name: Optional[str] = Field(default=None, description="Accessible name supplied only by title, with no rendered text.")
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
  // Handles are valid only for one snapshot. Dynamic UIs can make a previously
  // indexed element ineligible, so clear every old marker before reindexing.
  document.querySelectorAll('[data-ai-index]').forEach((el) => el.removeAttribute('data-ai-index'));
  const SEMANTIC = 'a,button,input,select,textarea,summary,[role],[onclick],[tabindex]:not([tabindex="-1"]),h1,h2,h3,h4,h5,h6,[data-testid],[data-test-id],[class*="badge"],[jsaction],[contenteditable="true"]';
  const POINTER_TAGS = 'div,span,li,td,th,p,label,section,article,header,nav,em,strong,i,b';
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    return r.width > 1 && r.height > 1 &&
           s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const ownName = (el) => {
    const text = (el.innerText || el.value || '').trim();
    return text ||
           (el.getAttribute('aria-label') || '').trim() ||
           (el.getAttribute('placeholder') || '').trim() ||
           (el.getAttribute('title') || '').trim() ||
           '';
  };
  const innermostSameText = (el) => {
    const text = (el.innerText || '').trim();
    let cur = el;
    while (true) {
      const kids = Array.from(cur.children).filter(isVisible);
      if (kids.length === 1 && (kids[0].innerText || '').trim() === text && text) {
        cur = kids[0];
        continue;
      }
      break;
    }
    return cur;
  };
  const seen = new Set();
  const els = [];
  const add = (el) => {
    if (!el || seen.has(el) || !isVisible(el)) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'body' || tag === 'html') return;
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (role === 'none' || role === 'presentation') return;
    seen.add(el);
    els.push(el);
  };
  document.querySelectorAll(SEMANTIC).forEach(add);

  // Custom chips/tabs/menus: clickable in the UI (pointer cursor) but not a
  // native control and often missing role/onclick/tabindex.
  for (const el of document.querySelectorAll(POINTER_TAGS)) {
    if (seen.has(el) || !isVisible(el)) continue;
    if (window.getComputedStyle(el).cursor !== 'pointer') continue;
    const name = ownName(el);
    if (!name || name.length > 80) continue;
    let covered = false;
    for (const existing of els) {
      if (existing !== el && el.contains(existing)) { covered = true; break; }
    }
    if (covered) continue;
    add(innermostSameText(el));
  }

  // Page titles used in asserts (h1–h6, <b>/<strong>) are often not clickable,
  // so the pointer pass misses them. Nav already captured the same word as a link.
  for (const el of document.querySelectorAll('h1,h2,h3,h4,h5,h6,b,strong,[role="heading"]')) {
    if (seen.has(el) || !isVisible(el)) continue;
    const name = ownName(el);
    if (!name || name.length > 80) continue;
    let inside = false;
    for (const existing of els) {
      if (existing !== el && existing.contains(el)) { inside = true; break; }
    }
    if (inside) continue;
    add(el);
  }

  const controls = els.filter((el) => {
    const tag = el.tagName.toLowerCase();
    if (['a','button','input','select','textarea','summary','h1','h2','h3','h4','h5','h6','b','strong'].includes(tag)) return true;
    const lines = ((el.innerText || '').trim().split('\n').length);
    return lines <= 4;
  });

  return controls.map((el, i) => {
    el.setAttribute('data-ai-index', String(i));   // ephemeral handle for THIS snapshot
    const r = el.getBoundingClientRect();
    const label = el.getAttribute('aria-label') ||
                  (el.labels && el.labels[0] && el.labels[0].textContent) || null;
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
      (tag === 'h1' || tag === 'h2' || tag === 'h3' || tag === 'h4' || tag === 'h5' || tag === 'h6') ? 'heading' :
      null
    );
    // Playwright accessible-name and text locators match source text, while
    // innerText includes CSS transformations such as text-transform: uppercase.
    const rawText = (el.textContent || el.value || '').trim();
    const firstLine = rawText.split(/\n/).map(s => s.trim()).filter(Boolean)[0] || rawText;
    return {
      index: i,
      tag: tag,
      role: implicitRole,
      explicit_role: explicitRole,
      type: el.getAttribute('type'),
      text: firstLine.slice(0, 80),
      label: label,
      placeholder: el.getAttribute('placeholder'),
      title: el.getAttribute('title'),
      name: el.getAttribute('name'),
      id: el.id || null,
      testid: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || null,
      icon: (() => {
        const svg = el.querySelector('svg[data-lucide], svg.lucide');
        if (!svg) return null;
        if (svg.dataset?.lucide) return svg.dataset.lucide;
        const iconClass = Array.from(svg.classList).find(
          cls => cls.startsWith('lucide-') && cls !== 'lucide-icon'
        );
        return iconClass ? iconClass.slice('lucide-'.length) : null;
      })(),
      bg: window.getComputedStyle(el).backgroundColor,   // helps match "blue button"
      box: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
    };
  });
}
"""


_NAV_CONTEXT_RE = re.compile(
    r"execution context was destroyed|most likely because of a navigation",
    re.I,
)


_BLOCK_URL_RE = re.compile(
    r"/sorry/|recaptcha|captcha|challenge|checkpoint|cgi/denied",
    re.I,
)
_BLOCK_TEXT_RE = re.compile(
    r"unusual traffic|are you (a |really )?a robot|why did this happen|"
    r"i['’]m not a robot|enable javascript|access denied|"
    r"pardon our interruption|checking your browser|verify you are human",
    re.I,
)
_ELEMENT_ACTIONS = {"click", "type", "select", "hover", "assert", "drag"}


def _element_search_blob(el: dict[str, Any]) -> str:
    return " ".join(
        str(el.get(k) or "")
        for k in ("text", "label", "placeholder", "title", "name", "testid", "id", "icon")
    )


_NOISE_ROLES = {"none", "presentation", "generic", "main", "group"}
_CONTROL_TAGS = {"a", "button", "input", "select", "textarea", "summary"}
_CONTROL_ROLES = {
    "button", "link", "textbox", "combobox", "tab", "menuitem",
    "checkbox", "radio", "searchbox", "option", "status", "alert",
}
_FLASH_ROLES = {"status", "alert", "alertdialog"}
_SUBMITISH = re.compile(
    r"\b(add event|save|submit|create|delete|sign in|log in|continue|apply)\b",
    re.I,
)
_FLASH_ASSERT_RE = re.compile(
    r"\b(toast|snackbar|notification|flash(?:\s+message)?|banner)\b"
    r"|confirms? (?:the )?\w+ was (?:created|added|saved|updated|deleted|submitted)"
    r"|was (?:created|added|saved|updated|deleted) successfully",
    re.I,
)
# gpt-4o org TPM here is 30k; keep a single grounding call well under that.
_LLM_DOM_CAP = 80
_LLM_DOM_CAP_RETRY = 36
_STEP_WORD_RE = re.compile(r"[a-z0-9]{3,}")


def _is_noise_element(el: dict[str, Any]) -> bool:
    tag = (el.get("tag") or "").lower()
    if tag in {"body", "html"}:
        return True
    role = (el.get("role") or "").lower()
    if role in {"none", "presentation"}:
        return True
    text = el.get("text") or ""
    if text.count("\n") >= 3 and tag not in _CONTROL_TAGS:
        return True
    blob = _element_search_blob(el).strip()
    if not blob and tag not in _CONTROL_TAGS and not el.get("testid"):
        return True
    return False


def _step_keywords(fuzzy: FuzzyStep) -> list[str]:
    blob = f"{fuzzy.target} {fuzzy.value or ''} {fuzzy.action}"
    words = _STEP_WORD_RE.findall(blob.lower())
    stop = {
        "the", "and", "for", "with", "from", "into", "that", "this", "click",
        "type", "press", "select", "assert", "navigate", "should", "page",
        "button", "field", "input", "tab", "unverified", "parser", "guessed",
        "selector",
    }
    return [w for w in words if w not in stop]


def _score_element(el: dict[str, Any], keywords: list[str]) -> float:
    blob = _element_search_blob(el).lower()
    score = 0.0
    if blob:
        score += 2
    tag = (el.get("tag") or "").lower()
    role = (el.get("role") or "").lower()
    if tag in _CONTROL_TAGS:
        score += 5
    if role in _CONTROL_ROLES:
        score += 4
    if el.get("testid"):
        score += 8
    if role in _NOISE_ROLES:
        score -= 6
    for kw in keywords:
        if kw in blob:
            score += 14
    box = el.get("box") or {}
    y = float(box.get("y") or 0)
    x = float(box.get("x") or 0)
    if 0 <= y <= 900 and 0 <= x <= 1400:
        score += 1
        score += max(0.0, (700 - y) / 400)
    return score


def compact_element_for_llm(el: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"index": el.get("index"), "tag": el.get("tag")}
    role = el.get("role")
    if role and role not in {"none", "presentation"}:
        out["role"] = role
    for key in ("type", "text", "label", "placeholder", "title", "name", "testid", "id", "icon"):
        val = el.get(key)
        if val:
            out[key] = val[:80] if isinstance(val, str) else val
    box = el.get("box") or {}
    if box:
        out["xy"] = [box.get("x"), box.get("y")]
    return {k: v for k, v in out.items() if v not in (None, "", [])}


_EMAIL_VALUE_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_TOKEN_VALUE_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+|\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9_-]{40,}\b")
_SECRET_STORAGE_KEY_RE = re.compile(
    r"(?i)(?:^|[_-])(access|refresh|id)?token(?:$|[_-])|password|passcode|secret|credential|authorization|session"
)


def _secret_fragments_from_storage_state(state: dict) -> set[str]:
    """Collect secret-bearing values, never storage keys or origin metadata."""
    fragments: set[str] = set()

    def add_value(value: Any, *, secret_field: bool = False) -> None:
        if isinstance(value, str):
            if secret_field and len(value) >= 6:
                fragments.add(value)
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError):
                return
            add_value(decoded)
        elif isinstance(value, dict):
            for key, child in value.items():
                add_value(child, secret_field=bool(_SECRET_STORAGE_KEY_RE.search(str(key))))
        elif isinstance(value, list):
            for child in value:
                add_value(child, secret_field=secret_field)
        elif secret_field and value is not None:
            fragments.add(str(value))

    for cookie in state.get("cookies", []):
        add_value(cookie.get("value"), secret_field=True)
    for origin in state.get("origins", []):
        for item in origin.get("localStorage", []):
            add_value(
                item.get("value"),
                secret_field=bool(_SECRET_STORAGE_KEY_RE.search(str(item.get("name") or ""))),
            )
        for database in origin.get("indexedDB", []):
            for store in database.get("stores", []):
                for record in store.get("records", []):
                    add_value(record.get("value"))
    return fragments


def redact_authenticated_text(value: str, state: dict) -> str:
    """Remove session secrets and credential-shaped values from runtime text."""
    secrets = sorted(_secret_fragments_from_storage_state(state), key=len, reverse=True)
    cleaned = value
    for secret in secrets:
        cleaned = cleaned.replace(secret, "")
    cleaned = _EMAIL_VALUE_RE.sub("", cleaned)
    cleaned = _TOKEN_VALUE_RE.sub("", cleaned)
    return " ".join(cleaned.split())


def redact_authenticated_dom(elements: list[dict[str, Any]], state: dict) -> list[dict[str, Any]]:
    """Remove signed-in values before snapshots reach logs, models, or plans."""

    safe: list[dict[str, Any]] = []
    for source in elements:
        item = {
            key: redact_authenticated_text(value, state) if isinstance(value, str) else value
            for key, value in source.items()
        }
        tag = str(item.get("tag") or "").lower()
        input_type = str(item.get("type") or "").lower()
        if tag in {"input", "textarea", "select"} and input_type not in {"button", "submit", "reset"}:
            item["text"] = ""
        safe.append(item)
    return safe


def select_dom_for_llm(
    dom: list[dict[str, Any]],
    fuzzy: FuzzyStep,
    *,
    cap: int = _LLM_DOM_CAP,
) -> list[dict[str, Any]]:
    """Rank and compact the live snapshot so one LLM call stays under TPM limits."""
    keywords = _step_keywords(fuzzy)
    usable = [el for el in dom if not _is_noise_element(el)]
    if not usable:
        usable = list(dom)

    pinned = [
        el for el in usable
        if (el.get("role") or "").lower() in _FLASH_ROLES
    ]
    keyword_hits = []
    rest = []
    for el in usable:
        blob = _element_search_blob(el).lower()
        if keywords and any(kw in blob for kw in keywords):
            keyword_hits.append(el)
        else:
            rest.append(el)

    rest.sort(key=lambda el: _score_element(el, keywords), reverse=True)
    chosen: list[dict[str, Any]] = []
    seen: set[int] = set()
    for el in pinned + keyword_hits + rest:
        idx = el.get("index")
        if idx in seen:
            continue
        seen.add(idx)
        chosen.append(el)
        if len(chosen) >= cap:
            break
    chosen.sort(key=lambda el: int(el.get("index") or 0))
    return [compact_element_for_llm(el) for el in chosen]


def _is_too_large_request(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "request too large" in msg
        or "rate_limit_exceeded" in msg
        or "tokens per min" in msg
        or "error code: 429" in msg
        or "429" in msg and "token" in msg
    )


async def describe_block_page(page: Page) -> Optional[str]:
    """Return a reason if the tab is a captcha/interstitial instead of the app."""
    url = page.url or ""
    if _BLOCK_URL_RE.search(url):
        return f"block URL {url}"
    try:
        text = await page.inner_text("body", timeout=2000)
    except Exception:
        return None
    if text and _BLOCK_TEXT_RE.search(text):
        snippet = " ".join(text.split())[:120]
        return f"bot-check copy on {url}: {snippet!r}"
    return None


async def wait_for_app_page(page: Page, *, headless: bool) -> None:
    """Fail fast (headless) or wait for the user (headed) when a bot-check is showing."""
    reason = await describe_block_page(page)
    if not reason:
        return
    log(f"  Bot-check / interstitial detected: {reason}", "WARN")
    if headless:
        raise RuntimeError(
            "The site served a bot-check/captcha page instead of the app, so the "
            "target control is not in the DOM. Re-run refine headed (`--headed` or "
            "the GUI 'Show browser' option) and complete the check, then continue. "
            f"URL: {page.url}"
        )
    budget_s = 120
    log(f"  Complete the check in the visible browser. Waiting up to {budget_s}s…", "WARN")
    for _ in range(budget_s // 2):
        await page.wait_for_timeout(2000)
        reason = await describe_block_page(page)
        if not reason:
            log("  Interstitial cleared — continuing", "OK")
            await wait_for_stable_page(page)
            return
    raise RuntimeError(
        f"Still on a bot-check/captcha page after {budget_s}s. URL: {page.url}"
    )


def log_snapshot_preview(elements: list[dict[str, Any]]) -> None:
    bits: list[str] = []
    for el in elements[:12]:
        name = " ".join(str(el.get("text") or el.get("label") or el.get("placeholder") or "").split())[:40]
        bits.append(f"{el.get('index')}:{el.get('tag')}/{el.get('role') or '-'} {name!r}")
    if bits:
        log("  Snapshot: " + " | ".join(bits))
    extra = len(elements) - 12
    if extra > 0:
        log(f"  … {extra} more")


def grounding_matches_dom(step: RefinedStep, dom: list[dict[str, Any]]) -> bool:
    """True when the grounded locator corresponds to something in this snapshot."""
    if step.action not in {"click", "type", "select", "hover", "drag"}:
        return True
    if step.element_index is not None:
        matched = next((el for el in dom if el.get("index") == step.element_index), None)
        if not matched:
            return False
        if step.locator_strategy == "css":
            selector = (step.locator_value or "").strip().lower()
            tag = (matched.get("tag") or "").strip().lower()
            if selector == tag and sum((el.get("tag") or "").lower() == tag for el in dom) > 1:
                return False
        return True
    needle = (step.locator_value or "").strip()
    if not needle or needle.lower() == "n/a":
        return False
    low = needle.lower()
    for el in dom:
        if low in _element_search_blob(el).lower():
            return True
    return False


async def launch_browser_page(p, *, headless: bool, storage_state: dict | None = None):
    """Launch Chromium in a realistic context so sites are less likely to serve a bot wall."""
    args = ["--disable-blink-features=AutomationControlled"]
    browser = None
    try:
        browser = await p.chromium.launch(channel="chrome", headless=headless, args=args)
        log(f"Launched system Chrome (headless={headless})")
    except Exception as e:
        log(f"  System Chrome unavailable ({e}) — using Playwright Chromium", "WARN")
        browser = await p.chromium.launch(headless=headless, args=args)
        log(f"Launched Playwright Chromium (headless={headless})")
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        storage_state=storage_state,
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    )
    page = await context.new_page()
    return browser, context, page


async def wait_for_stable_page(page: Page, *, timeout_ms: int = 15000) -> None:
    """Wait until the current document can run JS.

    Do not require networkidle — ads and analytics prevent it on many sites,
    and a navigation in flight will destroy the next page.evaluate().
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception as e:
        log(f"  Load wait (domcontentloaded): {e}", "WARN")
    try:
        await page.wait_for_load_state("load", timeout=min(timeout_ms, 10000))
    except Exception:
        pass
    await page.wait_for_timeout(150)


async def wait_for_ui_settle(
    page: Page,
    *,
    watch_flash: bool = False,
    authenticated_state: dict | None = None,
) -> Optional[str]:
    """After a click/submit, wait out save spinners and capture a toast/status.

    Many apps delay the success toast until a modal overlay is removed. A 150ms
    load wait is too short, and a later LLM round-trip is too late — the toast
    is already gone. Capture the flash here, while it is on screen.
    """
    await wait_for_stable_page(page)
    had_overlay = False
    try:
        overlay = page.locator("#overlay, .overlay")
        if await overlay.count() > 0:
            had_overlay = True
            log("  Waiting for overlay/modal to close")
            await overlay.first.wait_for(state="detached", timeout=6000)
            log("  Overlay/modal dismissed", "OK")
    except Exception as e:
        log(f"  Overlay wait: {e}", "WARN")
    if not (watch_flash or had_overlay):
        return None
    flash: Optional[str] = None
    try:
        loc = page.get_by_role("status").or_(page.get_by_role("alert"))
        await loc.first.wait_for(state="visible", timeout=2500)
        flash = " ".join((await loc.first.inner_text()).split())
        if flash and authenticated_state is not None:
            flash = redact_authenticated_text(flash, authenticated_state)
        if flash:
            log(f"  Captured flash/status: {flash!r}", "OK")
    except Exception:
        pass
    return flash


def _is_flash_assert(fuzzy: FuzzyStep) -> bool:
    return fuzzy.action == "assert" and bool(_FLASH_ASSERT_RE.search(fuzzy.target or ""))


async def snapshot_interactive_dom(page: Page, authenticated_state: dict | None = None) -> list[dict[str, Any]]:
    last_err: Optional[Exception] = None
    for attempt in range(1, 4):
        await wait_for_stable_page(page)
        try:
            log(f"Snapshotting interactive DOM on: {page.url}")
            elements = await page.evaluate(_EXTRACT_JS)
            if len(elements) < 8:
                log(f"  Sparse snapshot ({len(elements)}) — waiting for more UI", "WARN")
                await page.wait_for_timeout(2000)
                elements = await page.evaluate(_EXTRACT_JS)
            if authenticated_state is not None:
                elements = redact_authenticated_dom(elements, authenticated_state)
            log(f"DOM snapshot complete — {len(elements)} interactive element(s) found")
            log_snapshot_preview(elements)
            return elements
        except Exception as e:
            last_err = e
            if attempt < 3 and _NAV_CONTEXT_RE.search(str(e)):
                log(f"  Snapshot interrupted by navigation — retrying ({attempt}/3)", "RETRY")
                continue
            raise
    raise last_err  # pragma: no cover


# ===========================================================================
# 3. Grounding chain (LangChain + structured output)
# ===========================================================================

_SYSTEM = """You are a QA grounding engine. You receive ONE fuzzy step from a \
steps-derived test plan plus a JSON list of currently visible elements: native \
controls AND custom clickable widgets (chips, tabs, menu items implemented as \
div/span with a pointer cursor). Each entry has an `index` and attributes: \
text, role, label, placeholder, testid, tag, background colour, bounding box.

Resolve the fuzzy step to a concrete, reproducible RefinedStep.

Rules:
- Pick the single best-matching element; return its `index` as `element_index`.
- Emit the MOST DURABLE locator, preferring: testid > role(+exact accessible name) >
  exact label > placeholder > text > css.
- Accessible names are matched EXACTLY. Never rely on substring label matching:
  getByLabel("Search") also matches "Search by voice", "Search for Images", and
  "Google Search". Prefer strategy="role" with the element's real role (e.g.
  combobox) plus the exact name "Search".
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
  "region", "button", "textbox", "combobox", "heading", "link", "dialog"). NEVER leave it null
  when strategy=="role" and NEVER default it to "button" — use the actual role from
  the DOM snapshot (the `role` / `explicit_role` field). A <textarea role="combobox">
  is a combobox, NOT a textbox, even though textarea's implicit role is textbox.
- When strategy=="role", `locator_value` is the accessible name (label), NEVER the
  role string. Wrong: locator_value="combobox". Right: role_name="combobox",
  locator_value="Search".
- For "press Enter/Tab/Escape" (submit a field): set action to "press" and value to
  the key (Enter, Tab, Escape). Do not use type with a newline, and do not treat it
  as a mouse click.
- For a native <select> element (tag="select", role="listbox"): ALWAYS use
  strategy="label" with the element's aria-label or associated label text as
  locator_value. Never use strategy="role" for selects — getByLabel is more durable
  and avoids strict-mode ambiguity. The action should be "select", not "click".
- If the matched element has no ARIA role (tag is div/span/li/td and `role` is null),
  use strategy="text" with ONE visible line from `text` (or aria-label). Do NOT
  invent role="tab" / role="button" / role="link". Do NOT put a newline, `<br>`,
  or a second line (report id, subtitle) in locator_value — getByText exact match
  will not find innerText that spans multiple nodes.
- For navigate/wait/scroll with no element, set element_index=null and
  locator_value="n/a".
- For assert steps, ALWAYS set `element_index` to a real snapshot row. Assert is not
  navigate — null index is wrong.
- If the same visible string appears twice (sidebar "Dashboard" link AND a page
  title "Dashboard"), pick the page title: prefer tag h1–h6, then b/strong, and
  avoid role=link. A heading/title assert must not target navigation.
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

CURRENT DOM (visible controls and clickable chips/tabs):
{dom}

REMINDER — if this is an assert step:
- Set `assert_values` to the literal visible strings from the DOM above (not description prose).
- Set `value` to the single most important visible string, or null if assert_values covers it.

Return the grounded RefinedStep."""


def build_refiner(backend: str, model: Optional[str], temperature: float = 0.0):
    log(f"Building grounding chain with backend={backend} model={model or 'default'}")
    llm = build_llm(backend, model, temperature, max_tokens=2048)
    structured = llm.with_structured_output(RefinedStep)
    prompt = ChatPromptTemplate.from_messages([("system", _SYSTEM), ("human", _HUMAN)])
    log("Grounding chain ready")
    return prompt | structured


async def ground_step(chain, fuzzy: FuzzyStep, dom: list[dict], history: list[str]) -> RefinedStep:
    log(f"  Grounding step {fuzzy.step_number}: [{fuzzy.action}] {fuzzy.target[:80]}...")
    payload = select_dom_for_llm(dom, fuzzy)
    dumped = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    log(
        f"  Sending {len(payload)}/{len(dom)} DOM elements "
        f"({len(dumped)} chars) + {min(len(history), 6)} history entries to LLM"
    )

    async def _invoke(dom_payload: list[dict[str, Any]]) -> RefinedStep:
        return await chain.ainvoke({
            "fuzzy_step": fuzzy.model_dump_json(),
            "history": "\n".join(history[-6:]) or "(none)",
            "dom": json.dumps(dom_payload, ensure_ascii=False, separators=(",", ":")),
        })

    try:
        step = await _invoke(payload)
    except Exception as e:
        if not _is_too_large_request(e):
            raise
        smaller = select_dom_for_llm(dom, fuzzy, cap=_LLM_DOM_CAP_RETRY)
        log(
            f"  Request too large ({e.__class__.__name__}) — retrying with "
            f"{len(smaller)} elements",
            "RETRY",
        )
        await asyncio.sleep(1.5)
        step = await _invoke(smaller)
    step.step_number = fuzzy.step_number           # keep numbering authoritative
    step.reclassified = step.reclassified or (step.action != fuzzy.action)

    # Parse owns literal values. Refine grounds the target, not what gets typed,
    # selected, pressed or navigated to.
    if fuzzy.value is not None and step.action in ("type", "press", "select", "navigate"):
        if step.value != fuzzy.value:
            log(f"  Restoring parser value for [{step.action}]: {fuzzy.value!r}", "WARN")
        step.value = fuzzy.value
    if step.action == "press":
        step.value = _normalize_key(step.value or fuzzy.value)

    _sanitize_text_locator(step, fuzzy)
    _resolve_assert_target(step, fuzzy, dom)
    _promote_unique_icon_locator(step, fuzzy, dom)
    # This is observed DOM evidence, never a model-owned claim.
    step.title_only_name = None
    if step.element_index is not None:
        matched = next((el for el in dom if el.get("index") == step.element_index), None)
        if matched:
            if not step.host_tag:
                step.host_tag = matched.get("tag")
            title = (matched.get("title") or "").strip()
            if title and not (matched.get("text") or "").strip() and step.locator_strategy == "role":
                step.title_only_name = title

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
            if step.locator_strategy == "role":
                if not step.role_name:
                    step.role_name = matched.get("role") or matched.get("explicit_role")
                if _is_role_token(step.locator_value, step.role_name):
                    name = (matched.get("label") or matched.get("placeholder") or "").strip()
                    if name:
                        log(
                            f"  Role locator_value {step.locator_value!r} is a role token "
                            f"— using accessible name {name!r}",
                            "WARN",
                        )
                        step.locator_value = name
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

_KNOWN_ARIA_ROLES = {
    "region", "section", "article", "main", "navigation", "heading",
    "textbox", "checkbox", "combobox", "listbox", "option", "link",
    "img", "table", "row", "cell", "dialog", "alert", "banner",
    "button", "radio", "menuitem", "tab", "tabpanel", "tree", "treeitem",
}


def _is_role_token(value: Optional[str], role_name: Optional[str] = None) -> bool:
    """True when locator_value is an ARIA role (e.g. 'combobox'), not an accessible name."""
    if not value:
        return True
    v = value.strip().lower()
    if v in _KNOWN_ARIA_ROLES:
        return True
    return bool(role_name) and v == role_name.strip().lower()


def _role_accessible_name(step: RefinedStep) -> Optional[str]:
    """Name passed to getByRole(..., name=). None if locator_value is just the role."""
    v = (step.locator_value or "").strip()
    if not v or _is_role_token(v, step.role_name):
        return None
    return v


def _primary_visible_text(value: Optional[str], fuzzy: Optional[FuzzyStep] = None) -> str:
    """Playwright getByText cannot match innerText that a <br> joined with a newline.

    Keep a single visual line. Prefer the line that overlaps the step wording.
    """
    if not value:
        return ""
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", value) if ln.strip()]
    if not lines:
        return value.strip()
    if len(lines) == 1:
        return lines[0]
    keywords = _step_keywords(fuzzy) if fuzzy else []
    if keywords:
        def overlap(ln: str) -> int:
            low = ln.lower()
            return sum(1 for kw in keywords if kw in low)
        ranked = sorted(lines, key=overlap, reverse=True)
        if overlap(ranked[0]) > 0:
            return ranked[0]
    return lines[0]


_TEXT_SCOPE_TAGS = {"b", "strong", "h1", "h2", "h3", "h4", "h5", "h6"}


def _text_locator_expr(value: str, host_tag: Optional[str] = None) -> str:
    q = lambda item: json.dumps(item, ensure_ascii=False)
    v = _primary_visible_text(value)
    tag = (host_tag or "").lower()
    if tag in _TEXT_SCOPE_TAGS:
        return f"locator({q(tag)}).get_by_text({q(v)}, exact=True)"
    return f"get_by_text({q(v)}, exact=True)"


def _text_locator(page: Page, value: str, host_tag: Optional[str] = None) -> Locator:
    v = _primary_visible_text(value)
    tag = (host_tag or "").lower()
    if tag in _TEXT_SCOPE_TAGS:
        return page.locator(tag).get_by_text(v, exact=True)
    return page.get_by_text(v, exact=True)


def _resolve_assert_target(step: RefinedStep, fuzzy: FuzzyStep, dom: list[dict[str, Any]]) -> None:
    """When 'Dashboard' is both a nav link and a page title, prefer the title."""
    if step.action != "assert":
        return
    needle = _primary_visible_text(step.locator_value if step.locator_strategy == "text" else "", fuzzy)
    if not needle and step.assert_values:
        needle = _primary_visible_text(step.assert_values[0], fuzzy)
    if not needle and step.value:
        needle = _primary_visible_text(step.value, fuzzy)
    if not needle:
        return
    low = needle.lower()
    matches = [
        el for el in dom
        if (el.get("text") or "").strip().lower() == low
        or low == (el.get("label") or "").strip().lower()
    ]
    if not matches:
        matches = [el for el in dom if low in _element_search_blob(el).lower()]
    if not matches:
        return

    wants_heading = bool(re.search(r"\b(heading|title|h1)\b", fuzzy.target or "", re.I))

    def rank(el: dict[str, Any]) -> int:
        tag = (el.get("tag") or "").lower()
        role = (el.get("role") or "").lower()
        score = 0
        if tag in _TEXT_SCOPE_TAGS or role == "heading":
            score += 10
        if wants_heading and (tag in _TEXT_SCOPE_TAGS or role == "heading"):
            score += 8
        if role == "link":
            score -= 6
        return score

    best = max(matches, key=rank)
    step.element_index = best.get("index")
    step.host_tag = best.get("tag")
    if step.locator_strategy in ("text", "css") or not step.locator_value or step.locator_value == "n/a":
        step.locator_strategy = "text"
        step.locator_value = needle
    if step.confidence < 0.6 and rank(best) >= 10:
        step.confidence = 0.75
        log(
            f"  Assert target disambiguated to <{best.get('tag')}> "
            f"index {best.get('index')} {needle!r}",
            "OK",
        )


def _promote_unique_icon_locator(
    step: RefinedStep, fuzzy: FuzzyStep, dom: list[dict[str, Any]]
) -> None:
    """Replace a tag-only CSS guess with a stable descendant-icon selector."""
    if step.element_index is None or step.locator_strategy != "css":
        return
    matched = next((el for el in dom if el.get("index") == step.element_index), None)
    if not matched:
        return
    tag = (matched.get("tag") or "").strip().lower()
    icon = (matched.get("icon") or "").strip().lower()
    current = (step.locator_value or "").strip().lower()
    is_positional = bool(
        re.fullmatch(rf"{re.escape(tag)}(?::nth-(?:of-type|child)\(\d+\))?", current)
    )
    is_lucide_guess = bool(
        re.fullmatch(
            rf"{re.escape(tag)}:has\((?:svg)?\[data-lucide=[\"'][a-z0-9_-]+[\"']\]\)",
            current,
        )
    )
    if not (is_positional or is_lucide_guess):
        return
    if not re.fullmatch(r"[a-z0-9_-]+", icon):
        return
    if icon not in _step_keywords(fuzzy):
        return
    same = [
        el for el in dom
        if (el.get("tag") or "").strip().lower() == tag
        and (el.get("icon") or "").strip().lower() == icon
    ]
    if len(same) != 1:
        return
    step.locator_value = (
        f'{tag}:has(svg[data-lucide="{icon}"]), {tag}:has(svg.lucide-{icon})'
    )
    step.notes = None
    step.confidence = max(step.confidence, 0.85)


def _sanitize_text_locator(step: RefinedStep, fuzzy: Optional[FuzzyStep] = None) -> None:
    if step.locator_strategy not in ("text", "label", "placeholder", "role"):
        return
    cleaned = _primary_visible_text(step.locator_value, fuzzy)
    if cleaned and cleaned != step.locator_value:
        log(
            f"  Collapsing multi-line locator {step.locator_value!r} → {cleaned!r}",
            "WARN",
        )
        step.locator_value = cleaned


def _normalize_key(value: Optional[str]) -> str:
    if value is None or value in ("\n", "\\n"):
        return "Enter"
    raw = value.strip()
    if not raw:
        return "Enter"
    aliases = {
        "enter": "Enter",
        "return": "Enter",
        "tab": "Tab",
        "esc": "Escape",
        "escape": "Escape",
        "backspace": "Backspace",
        "delete": "Delete",
        "space": "Space",
        "arrowup": "ArrowUp",
        "arrowdown": "ArrowDown",
        "arrowleft": "ArrowLeft",
        "arrowright": "ArrowRight",
    }
    return aliases.get(raw.lower(), raw)


def to_locator(page: Page, step: RefinedStep) -> Locator:
    s, v = step.locator_strategy, step.locator_value
    if s == "role" and step.role_name:
        name = _role_accessible_name(step)
        return (
            page.get_by_role(step.role_name, name=name, exact=True)
            if name else page.get_by_role(step.role_name)
        )
    if s == "label":
        return page.get_by_label(v, exact=True)
    if s == "placeholder":
        return page.get_by_placeholder(v, exact=True)
    if s == "text":
        return _text_locator(page, v, step.host_tag)
    if s == "testid":
        return page.get_by_test_id(v)
    if step.element_index is not None:                       # css / fallback
        return page.locator(f'[data-ai-index="{step.element_index}"]')
    return page.locator(v)


def locator_expr(step: RefinedStep) -> str:
    """Playwright locator as a source string, for the generator to emit directly.

    Select elements have implicit role=listbox but getByLabel is always more
    durable — when role==listbox and the locator_value is an accessible name
    (not a role keyword), we emit get_by_label instead.
    """
    q = lambda item: json.dumps(item, ensure_ascii=False)
    s, v = step.locator_strategy, step.locator_value

    if s == "label":
        return f"get_by_label({q(v)}, exact=True)"

    if s == "role":
        role = step.role_name or (v if v in _KNOWN_ARIA_ROLES else "button")
        name = _role_accessible_name(step)

        # <select aria-label="Priority"> → getByLabel('Priority') is more durable
        # than getByRole('listbox', { name: 'Priority' }) and avoids strict-mode issues
        if role == "listbox" and name:
            return f"get_by_label({q(name)}, exact=True)"

        if name:
            return f"get_by_role({q(role)}, name={q(name)}, exact=True)"
        return f"get_by_role({q(role)})"

    if s == "placeholder":
        return f"get_by_placeholder({q(v)}, exact=True)"
    if s == "text":
        return _text_locator_expr(v, step.host_tag)
    if s == "testid":
        return f"get_by_test_id({q(v)})"
    return f"locator({q(v)})"


# ===========================================================================
# 5. Execute one grounded step + assert its expected result
# ===========================================================================

async def _execute_authored_assertion(page: Page, loc, outcome: dict[str, Any]) -> bool:
    """Execute parser-owned assertion semantics without letting grounding rewrite them."""
    handled = False

    if isinstance(outcome.get("title"), str):
        await expect(loc).to_have_attribute("title", outcome["title"], timeout=5000)
        handled = True
    accessible_name = outcome.get("accessible_name")
    if isinstance(accessible_name, str):
        await expect(loc).to_have_accessible_name(accessible_name, timeout=5000)
        handled = True
    visibility = str(outcome.get("visible", outcome.get("visibility", ""))).lower()
    if visibility in {"true", "false", "visible", "hidden"}:
        if visibility in {"true", "visible"}:
            await expect(loc).to_be_visible(timeout=5000)
        else:
            await expect(loc).to_be_hidden(timeout=5000)
        handled = True

    if outcome.get("url_equals"):
        await expect(page).to_have_url(str(outcome["url_equals"]), timeout=5000)
        handled = True
    if outcome.get("url_contains"):
        await expect(page).to_have_url(
            re.compile(re.escape(str(outcome["url_contains"]))), timeout=5000
        )
        handled = True
    if outcome.get("url_not_contains"):
        await expect(page).not_to_have_url(
            re.compile(re.escape(str(outcome["url_not_contains"]))), timeout=5000
        )
        handled = True

    absent_text = outcome.get("visible_text_absent") or outcome.get("not_visible_text")
    if absent_text:
        await expect(page.get_by_text(str(absent_text), exact=False)).to_have_count(0)
        handled = True

    checked_value = outcome.get("checked")
    if checked_value is not None:
        normalized = str(checked_value).strip().lower()
        if normalized in {"true", "checked", "yes", "1"}:
            checked = True
        elif normalized in {"false", "unchecked", "no", "0"}:
            checked = False
        else:
            raise ValueError(f"Unsupported checked assertion value: {checked_value!r}")
        await expect(loc).to_be_checked(checked=checked, timeout=5000)
        handled = True

    field_value = outcome.get("field_value")
    multi_field_description = field_value and "still shows" in str(field_value).lower()
    if field_value is not None and checked_value is None and not multi_field_description:
        await expect(loc).to_have_value(str(field_value), timeout=5000)
        handled = True

    if "element_count" in outcome:
        count_match = re.match(r"\s*(\d+)", str(outcome["element_count"]))
        if not count_match:
            raise ValueError(f"Unsupported element_count assertion: {outcome['element_count']!r}")
        count = int(count_match.group(1))
        if not (absent_text and count == 0):
            await expect(loc).to_have_count(count, timeout=5000)
        handled = True

    text_contains = outcome.get("text_contains")
    if text_contains:
        await expect(loc).to_contain_text(str(text_contains), timeout=5000)
        handled = True

    visible_text = outcome.get("visible_text")
    if visible_text:
        await expect(
            page.get_by_text(str(visible_text), exact=False).filter(visible=True).first
        ).to_be_visible(timeout=5000)
        handled = True

    visible_text_exact = outcome.get("visible_text_exact")
    if visible_text_exact:
        await expect(
            page.get_by_text(str(visible_text_exact), exact=True).filter(visible=True).first
        ).to_be_visible(timeout=5000)
        handled = True

    visible_heading = outcome.get("visible_heading")
    if visible_heading:
        await expect(
            page.get_by_role("heading", name=str(visible_heading), exact=True)
            .filter(visible=True).first
        ).to_be_visible(timeout=5000)
        handled = True

    return handled


async def execute_step(
    page: Page,
    step: RefinedStep,
    base_url: str,
    expected_outcome: Optional[dict[str, Any]] = None,
) -> None:
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
        key = _normalize_key(step.value)
        log(f"  Focusing target then pressing {key!r} on the page")
        try:
            await loc.focus(timeout=5000)
        except Exception as e:
            log(f"  Focus failed ({e}) — sending {key!r} to the focused page", "WARN")
        await page.keyboard.press(key)
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
        if expected_outcome and await _execute_authored_assertion(page, loc, expected_outcome):
            descriptive = {"assertion", "assert_values", "element_visible", "visible_element", "element_state"}
            semantic = set(expected_outcome) - descriptive
            pagewide = {"visible_text", "visible_text_exact", "visible_heading", "visible_text_absent", "not_visible_text", "url_equals", "url_contains", "url_not_contains"}
            if semantic and semantic <= pagewide:
                step.confidence = max(step.confidence, 0.95)
            log("  Authored assertion executed", "OK")
            return
        # Literal visible strings the model extracted (assert_values); never the
        # prose description. Duplicate names (nav + page title) are OK for
        # visibility — uniqueness is only required for clicks.
        values = [v for v in (step.assert_values or []) if v]
        if not values and step.value:
            values = [v for v in [_primary_visible_text(step.value)] if v]
        if not values and step.locator_strategy == "text":
            values = [v for v in [_primary_visible_text(step.locator_value)] if v]
        if len(values) > 1:
            for val in values:
                log(f"  Asserting element contains: {val!r}")
                await expect(loc).to_contain_text(val, timeout=5000)
        elif len(values) == 1:
            val = _primary_visible_text(values[0])
            if step.title_only_name == val and _role_accessible_name(step) == val:
                log(f"  Asserting title-only grounded role/name is visible: {val!r}")
                await expect(loc).to_be_visible(timeout=5000)
                return
            log(f"  Asserting visible text: {val!r}")
            try:
                n = await loc.count()
            except Exception:
                n = 1
            try:
                if n > 1:
                    await expect(loc.first).to_be_visible(timeout=5000)
                else:
                    await expect(loc).to_contain_text(val, timeout=5000)
            except (AssertionError, PlaywrightError):
                log(f"  Grounded locator did not uniquely contain {val!r} — asserting first visible occurrence", "WARN")
                await expect(page.get_by_text(val, exact=True).first).to_be_visible(timeout=5000)
                step.locator_strategy = "text"
                step.locator_value = val
                log(f"  Re-grounded assert to get_by_text({val!r}).first", "OK")
        else:
            log(f"  Asserting element is visible")
            try:
                n = await loc.count()
            except Exception:
                n = 1
            await expect((loc.first if n > 1 else loc)).to_be_visible(timeout=5000)
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


_PRESS_STEP = re.compile(
    r"\bpress(?:es|ed)?\s+(enter|return|tab|escape|esc|backspace|delete)\b",
    re.I,
)


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
        parsed_value = s.get("input_value") if "input_value" in s else s.get("value")
        value = parsed_value if parsed_value is not None else _first_quoted(desc)

        press_match = _PRESS_STEP.search(desc)
        if press_match and action in ("type", "click", "press"):
            action = "press"
            value = _normalize_key(press_match.group(1))
        elif action == "type" and s.get("input_value") in ("\n", "\\n"):
            action = "press"
            value = "Enter"
        elif action == "press":
            value = _normalize_key(value or s.get("input_value") or s.get("value") or "Enter")

        target = desc + (f"  [parser-guessed selector '{hint}' — UNVERIFIED]" if hint else "")

        if action == "navigate":
            if not seen_first_navigate:
                seen_first_navigate = True
                value = value or base_url
                log(f"  Step {step_no}: first navigate -> using {value!r}")
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
            expected_outcome=dict(s.get("expected_outcome") or {}),
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
    auth_hook: Optional[str] = None,
) -> dict:
    plan_base_url = plan.get("workflow", {}).get("base_url", "")
    requested_start_url = start_url or plan_base_url
    if auth_hook:
        if canonical_origin(requested_start_url) != canonical_origin(plan_base_url):
            raise ValueError("runtime authentication start URL must match the plan base URL origin")
        Path(auth_hook).resolve(strict=True)
        runtime_helper = Path(__file__).resolve().parent.parent / "runtime" / "auth-hook-runner.cjs"
        if not runtime_helper.exists():
            raise RuntimeError("runtime authentication requires a source or editable checkout containing runtime/auth-hook-runner.cjs")
    fuzzy_steps, base_url, warnings = adapt_plan(plan)
    start_url = start_url or base_url
    chain = build_refiner(backend, model)

    refined_steps: list[RefinedStep] = []
    original_by_no = {s["step"]: s for s in plan["steps"]}
    history: list[str] = []
    ambiguities: list[str] = list(warnings)
    last_flash: Optional[str] = None

    log(f"Launching browser (headless={headless})")
    async with async_playwright() as p:
        storage_state = acquire_storage_state(auth_hook, start_url) if auth_hook else None
        browser, context, page = await launch_browser_page(p, headless=headless, storage_state=storage_state)
        log(f"Navigating to start URL: {start_url}")
        await page.goto(start_url)
        log(f"Page loaded: {page.url!r}", "OK")

        stop_flow = False
        total = len(fuzzy_steps)
        try:
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

            # Captured flash evidence cannot establish authored URL, field, or
            # text expectations. Run those through normal execution and retry.
            if _is_flash_assert(fuzzy) and last_flash and not fuzzy.expected_outcome:
                log(f"  Toast/status assert using captured message {last_flash!r}", "OK")
                step = RefinedStep(
                    step_number=fuzzy.step_number,
                    action="assert",
                    element_index=None,
                    locator_strategy="role",
                    locator_value=last_flash,
                    role_name="status",
                    value=last_flash,
                    expected_result=fuzzy.expected_result or last_flash,
                    confidence=0.9,
                    reclassified=False,
                    notes=None,
                    assert_values=[last_flash],
                )
                try:
                    await execute_step(page, step, base_url, fuzzy.expected_outcome)
                except Exception as e:
                    log(
                        f"  Flash already dismissed ({e}) — accepting captured {last_flash!r}",
                        "WARN",
                    )
                last_flash = None
                step.notes = None
                refined_steps.append(step)
                history.append(
                    f"step {step.step_number}: assert flash {step.locator_value!r}"
                )
                log(f"  Step {fuzzy.step_number} complete  (conf={step.confidence:.2f})", "OK")
                continue

            attempt = 0
            while True:
                attempt += 1
                try:
                    await wait_for_app_page(page, headless=headless)
                except Exception as e:
                    log(f"  Step {fuzzy.step_number} blocked by interstitial: {e}", "ERROR")
                    step = RefinedStep(
                        step_number=fuzzy.step_number,
                        action=fuzzy.action,
                        element_index=None,
                        locator_strategy="css",
                        locator_value="n/a",
                        value=fuzzy.value,
                        expected_result=fuzzy.expected_result or "",
                        confidence=0.1,
                        reclassified=False,
                        notes=f"⚠️ FAILED after {attempt} attempts: {e}",
                        assert_values=[],
                    )
                    refined_steps.append(step)
                    ambiguities.append(f"step {step.step_number}: FAILED to execute — {e}")
                    history.append(f"step {step.step_number}: FAILED, flagged")
                    stop_flow = True
                    break

                dom = await snapshot_interactive_dom(page, storage_state if auth_hook else None)
                step = await ground_step(chain, fuzzy, dom, history)

                if not grounding_matches_dom(step, dom) and step.action in {"click", "type", "select", "hover", "drag"}:
                    log(
                        f"  Grounded {step.locator_strategy}:{step.locator_value!r} "
                        f"is not in this snapshot — not clicking a guessed locator",
                        "WARN",
                    )
                    step.confidence = min(step.confidence, 0.2)
                    if attempt <= max_retries:
                        history.append(
                            f"[retry {attempt}] locator not in snapshot, step {fuzzy.step_number}"
                        )
                        await page.wait_for_timeout(1000)
                        continue
                    step.notes = (
                        f"⚠️ FAILED after {attempt} attempts: target {step.locator_value!r} "
                        f"is not in the live DOM (page {page.url}). "
                        + (step.notes or "")
                    )
                    step.confidence = min(step.confidence, 0.2)
                    refined_steps.append(step)
                    ambiguities.append(
                        f"step {step.step_number}: FAILED to execute — "
                        f"target {step.locator_value!r} not present in DOM snapshot"
                    )
                    history.append(f"step {step.step_number}: FAILED, flagged")
                    log(f"  Step {fuzzy.step_number} FAILED: target not in snapshot", "ERROR")
                    break

                if step.confidence < confidence_floor and attempt <= max_retries:
                    log(f"  Confidence {step.confidence:.2f} below floor {confidence_floor} — retrying (attempt {attempt}/{max_retries})", "RETRY")
                    history.append(f"[retry {attempt}] low confidence, step {fuzzy.step_number}")
                    await page.wait_for_timeout(500)
                    continue
                try:
                    await execute_step(page, step, base_url, fuzzy.expected_outcome)
                    log(f"  Waiting for page to settle...")
                    flash = await wait_for_ui_settle(
                        page,
                        watch_flash=bool(
                            _SUBMITISH.search(f"{step.locator_value or ''} {step.value or ''}")
                        ),
                        authenticated_state=storage_state if auth_hook else None,
                    )
                    if flash:
                        last_flash = flash
                        history.append(f"flash message: {flash}")

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

            if stop_flow:
                log("  Remaining steps skipped — browser is on a bot-check page", "WARN")
                break

        finally:
            log("")
            log("All steps processed. Closing browser.")
            await context.close()
            await browser.close()
            log("Browser closed.", "OK")

    return _serialize(plan, fuzzy_steps, refined_steps, original_by_no, ambiguities, base_url)


# ===========================================================================
# 8. Serialize back into the parser's schema (drop-in for the generator)
# ===========================================================================

def _merge_expected_outcome(original: dict, refined: RefinedStep) -> dict:
    """Add grounding evidence without discarding the parser's expectations."""
    merged = dict(original.get("expected_outcome") or {})
    if refined.expected_result:
        merged.setdefault("assertion", refined.expected_result)
    if refined.assert_values:
        merged["assert_values"] = list(refined.assert_values)
    else:
        merged.setdefault("assert_values", [])
    return merged


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
                "title_only_name": r.title_only_name,
                "css_selector": r.locator_value if r.locator_strategy == "css" else None,
                "original_selector": fuzzy.original_selector if fuzzy else None,
            },
            "expected_outcome": _merge_expected_outcome(original, r),
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
                    choices=config.BACKENDS,
                    help="LLM provider for grounding (default: $LLM_BACKEND or anthropic)")
    ap.add_argument("--model", default=None, help="override the backend's default model")
    ap.add_argument("--headed", action="store_true", help="Run the browser headed (visible)")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--confidence-floor", type=float, default=0.5)
    ap.add_argument("--auth-hook", default=None,
                    help="trusted local JS authentication hook; session state remains memory-only")
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
        auth_hook=args.auth_hook,
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
