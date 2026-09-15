"""Regression tests for preserving the parser's contract during refinement."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from playwright.async_api import async_playwright

from qa_pipeline.refine_plan import (
    FuzzyStep,
    RefinedStep,
    _execute_authored_assertion,
    _serialize,
    adapt_plan,
    execute_step,
    ground_step,
    locator_expr,
    snapshot_interactive_dom,
    to_locator,
)


def test_resnapshot_clears_stale_ai_indexes_from_dynamic_dom() -> None:
    async def run() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content('<div id="composer" style="cursor:pointer;width:100px;height:30px">Composer</div>')
            await snapshot_interactive_dom(page)
            await page.evaluate("""() => {
              const old = document.querySelector('#composer');
              old.style.cursor = 'default';
              old.textContent = '';
              const button = document.createElement('button');
              button.textContent = 'Settings';
              document.body.appendChild(button);
            }""")
            await snapshot_interactive_dom(page)
            assert await page.locator('[data-ai-index="0"]').count() == 1
            assert await page.locator('button[data-ai-index="0"]').count() == 1
            await browser.close()
    asyncio.run(run())


def test_snapshot_preserves_title_only_button_name() -> None:
    async def run() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content('<button title="Private"><svg></svg></button>')
            snapshot = await snapshot_interactive_dom(page)
            assert snapshot[0]["title"] == "Private"
            assert await page.get_by_role("button", name="Private", exact=True).count() == 1
            await browser.close()
    asyncio.run(run())


def test_snapshot_exposes_an_icon_name_for_an_unnamed_button() -> None:
    """Removing descendant-icon capture would make icon-only controls indistinguishable."""
    async def run() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content('<button><svg data-lucide="send"></svg></button>')
            snapshot = await snapshot_interactive_dom(page)
            assert snapshot[0]["icon"] == "send"
            await browser.close()
    asyncio.run(run())


def test_snapshot_exposes_react_lucide_icon_class_for_an_unnamed_button() -> None:
    """lucide-react identifies icons by class, not data-lucide."""
    async def run() -> None:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page()
            await page.set_content(
                '<button><svg class="lucide lucide-settings" aria-hidden="true"></svg></button>'
            )
            snapshot = await snapshot_interactive_dom(page)
            assert snapshot[0]["icon"] == "settings"
            await browser.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "model_selector",
    ["button", "button:nth-of-type(7)", 'button:has([data-lucide="send"])'],
)
def test_ground_step_replaces_a_broad_button_selector_with_its_unique_icon(model_selector) -> None:
    fuzzy = FuzzyStep(
        step_number=1, step_id="send", action="click", target="send icon button"
    )
    claimed = RefinedStep(
        step_number=1, action="click", element_index=1, locator_strategy="css",
        locator_value=model_selector, expected_result="Entry saves", confidence=0.68,
    )
    dom = [
        {"index": 0, "tag": "button", "role": "button", "text": "Settings", "icon": "settings"},
        {"index": 1, "tag": "button", "role": "button", "text": "", "icon": "send"},
    ]
    grounded = asyncio.run(ground_step(_FixedChain(claimed), fuzzy, dom, []))
    assert grounded.locator_strategy == "css"
    assert grounded.locator_value == (
        'button:has(svg[data-lucide="send"]), button:has(svg.lucide-send)'
    )


def test_ambiguous_broad_css_does_not_count_as_grounded() -> None:
    step = RefinedStep(
        step_number=1, action="click", element_index=1, locator_strategy="css",
        locator_value="button", expected_result="Entry saves", confidence=0.68,
    )
    dom = [
        {"index": 0, "tag": "button", "role": "button", "text": "Cancel"},
        {"index": 1, "tag": "button", "role": "button", "text": ""},
    ]
    from qa_pipeline.refine_plan import grounding_matches_dom
    assert not grounding_matches_dom(step, dom)


def test_ground_step_does_not_promote_the_wrong_icon_for_the_instruction() -> None:
    fuzzy = FuzzyStep(
        step_number=1, step_id="settings", action="click", target="Settings icon button"
    )
    claimed = RefinedStep(
        step_number=1, action="click", element_index=1, locator_strategy="css",
        locator_value="button:nth-of-type(7)", expected_result="Settings open", confidence=0.42,
    )
    dom = [
        {"index": 0, "tag": "button", "role": "button", "text": "", "icon": "settings"},
        {"index": 1, "tag": "button", "role": "button", "text": "", "icon": "send"},
    ]
    grounded = asyncio.run(ground_step(_FixedChain(claimed), fuzzy, dom, []))
    assert grounded.locator_value == "button:nth-of-type(7)"
    assert grounded.confidence == 0.42


def test_authored_title_checks_attribute_and_visibility():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content('<button title="Private">Settings</button>')
            loc = page.get_by_role("button")
            assert await _execute_authored_assertion(page, loc, {"title": "Private", "visible": True})
            with pytest.raises(AssertionError):
                await _execute_authored_assertion(page, loc, {"title": "Public", "visible": True})
            await browser.close()
    asyncio.run(run())


def test_assert_uses_grounded_role_name_for_title_only_button() -> None:
    async def run() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content('<button title="Private"><svg></svg></button>')
            step = RefinedStep(
                step_number=1,
                action="assert",
                element_index=0,
                locator_strategy="role",
                locator_value="Private",
                role_name="button",
                title_only_name="Private",
                expected_result="Private is visible",
                confidence=1.0,
                assert_values=["Private"],
            )
            await execute_step(page, step, "http://localhost")
            assert step.locator_strategy == "role"
            await browser.close()
    asyncio.run(run())


def test_visible_text_is_pagewide_but_text_contains_stays_locator_scoped() -> None:
    async def run() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content('<h2>my journey</h2><p hidden>Golden thread</p><article>Golden thread</article>')
            loc = page.get_by_role("heading", name="my journey")
            assert await _execute_authored_assertion(page, loc, {"visible_text": "Golden thread"})
            with pytest.raises(AssertionError):
                await _execute_authored_assertion(page, loc, {"visible_text": "Missing journey"})
            with pytest.raises(AssertionError):
                await _execute_authored_assertion(page, loc, {"text_contains": "Golden thread"})
            await browser.close()
    asyncio.run(run())


def test_successful_pagewide_assertion_replaces_irrelevant_model_confidence() -> None:
    async def run() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content('<main>Golden thread</main>')
            step = RefinedStep(
                step_number=1, action="assert", element_index=0,
                locator_strategy="css", locator_value="body",
                expected_result="Post is visible", confidence=0.18,
            )
            await execute_step(
                page, step, "http://localhost", {"visible_text": "Golden thread"}
            )
            assert step.confidence >= 0.9
            await browser.close()
    asyncio.run(run())


def test_parser_exact_text_and_heading_expectations_execute_directly() -> None:
    async def run() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content('<h2>Welcome to the Portal</h2><p>Golden thread</p>')
            loc = page.locator("body")
            assert await _execute_authored_assertion(
                page, loc, {"visible_text_exact": "Golden thread"}
            )
            assert await _execute_authored_assertion(
                page, loc, {"visible_heading": "Welcome to the Portal"}
            )
            with pytest.raises(AssertionError):
                await _execute_authored_assertion(
                    page, loc, {"visible_heading": "the portal"}
                )
            await browser.close()
    asyncio.run(run())


def test_parser_visibility_and_accessible_name_expectations_execute() -> None:
    async def run() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content('<button aria-label="Shared to Portal"></button>')
            loc = page.get_by_role("button", name="Shared to Portal", exact=True)
            assert await _execute_authored_assertion(
                page, loc, {"visibility": "visible", "accessible_name": "Shared to Portal"}
            )
            await browser.close()
    asyncio.run(run())


@pytest.mark.parametrize("fails", [True, False])
def test_captured_flash_does_not_override_authored_assertion(monkeypatch, fails) -> None:
    """The real refine loop must serialize failed authored checks as ungrounded."""
    import qa_pipeline.refine_plan as module

    page = AsyncMock()
    page.url = "http://localhost/login"
    browser, context = AsyncMock(), AsyncMock()
    runtime = AsyncMock()
    monkeypatch.setattr(module, "async_playwright", lambda: runtime)
    monkeypatch.setattr(module, "launch_browser_page", AsyncMock(return_value=(browser, context, page)))
    monkeypatch.setattr(module, "build_refiner", lambda *_: object())
    monkeypatch.setattr(module, "wait_for_app_page", AsyncMock())
    monkeypatch.setattr(module, "snapshot_interactive_dom", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "wait_for_ui_settle", AsyncMock(return_value="Saved"))

    async def ground(_chain, fuzzy, _dom, _history):
        return RefinedStep(step_number=fuzzy.step_number, action=fuzzy.action,
                           locator_strategy="css", locator_value="body",
                           confidence=1.0, expected_result="Saved")

    async def execute(_page, step, _base_url, outcome):
        if step.action == "assert" and fails:
            raise AssertionError("Authored URL did not match")

    monkeypatch.setattr(module, "ground_step", ground)
    monkeypatch.setattr(module, "execute_step", execute)
    plan = {"workflow": {"title": "Save", "base_url": "http://localhost"},
            "metadata": {}, "steps": [
                {"step": 1, "action": "wait", "description": "Wait for save"},
                {"step": 2, "action": "assert", "description": "Verify toast Saved",
                 "expected_outcome": {"url_contains": "/dashboard"}},
            ]}
    result = asyncio.run(module.refine(plan, None, "openai", None, True, 0, 0.5))
    assertion = result["steps"][1]["refinement"]
    assert assertion["grounded"] is (not fails)
    if fails:
        assert "Authored URL did not match" in assertion["notes"]


class _FixedChain:
    """Return one predetermined refinement without invoking an external model."""

    def __init__(self, result: RefinedStep) -> None:
        self.result = result

    async def ainvoke(self, _payload: dict) -> RefinedStep:
        return self.result.model_copy(deep=True)


def test_ground_step_discards_spoofed_title_only_model_claim() -> None:
    fuzzy = FuzzyStep(step_number=1, step_id="private", action="assert", target="Private is visible")
    claimed = RefinedStep(
        step_number=1, action="assert", element_index=0, locator_strategy="role",
        locator_value="Private", role_name="button", title_only_name="Private",
        expected_result="Private is visible", confidence=1.0, assert_values=["Private"],
    )
    dom = [{"index": 0, "tag": "button", "role": "button", "text": "Private", "title": None}]
    grounded = asyncio.run(ground_step(_FixedChain(claimed), fuzzy, dom, []))
    assert grounded.title_only_name is None


def test_ground_step_keeps_exact_parser_input_value() -> None:
    """A model paraphrase must not replace the literal value authored upstream."""

    fuzzy = FuzzyStep(
        step_number=2,
        step_id="enter_email",
        action="type",
        target="Type the QA sandbox email into the Email field",
        value="qa.sandbox@example.com",
    )
    model_result = RefinedStep(
        step_number=2,
        action="type",
        element_index=4,
        locator_strategy="label",
        locator_value="Email",
        value="qa-sandbox@example.com",
        expected_result="The email field contains the QA address",
        confidence=0.96,
    )
    dom = [
        {
            "index": 4,
            "tag": "input",
            "type": "email",
            "role": "textbox",
            "label": "Email",
            "text": "",
            "placeholder": "",
            "testid": None,
            "box": {"x": 10, "y": 20, "width": 200, "height": 40},
        }
    ]

    grounded = asyncio.run(ground_step(_FixedChain(model_result), fuzzy, dom, []))

    assert grounded.value == "qa.sandbox@example.com"


def test_serialize_preserves_parser_expected_outcomes() -> None:
    """Refinement may add DOM evidence but must retain non-DOM expectations."""

    original = {
        "step": 7,
        "id": "verify_dashboard",
        "action": "assert",
        "description": "Verify the URL and heading after login.",
        "expected_outcome": {
            "url_contains": "/dashboard",
            "visible_text": "Dashboard",
            "visible_text_absent": "This page couldn't load",
            "field_value": "Ada Testwell",
            "checked": "true",
        },
    }
    fuzzy = FuzzyStep(
        step_number=7,
        step_id="verify_dashboard",
        action="assert",
        target="Verify the URL and heading after login",
    )
    refined = RefinedStep(
        step_number=7,
        action="assert",
        element_index=8,
        locator_strategy="role",
        locator_value="Dashboard",
        role_name="heading",
        expected_result="Dashboard is visible",
        confidence=0.94,
        assert_values=["Dashboard"],
    )
    plan = {
        "workflow": {"title": "Login", "base_url": "http://localhost:3020"},
        "steps": [original],
        "metadata": {},
    }

    result = _serialize(
        plan,
        [fuzzy],
        [refined],
        {7: original},
        [],
        "http://localhost:3020",
    )

    expected = result["steps"][0]["expected_outcome"]
    assert expected["url_contains"] == "/dashboard"
    assert expected["visible_text"] == "Dashboard"
    assert expected["visible_text_absent"] == "This page couldn't load"
    assert expected["field_value"] == "Ada Testwell"
    assert expected["checked"] == "true"
    assert expected["assertion"] == "Dashboard is visible"
    assert expected["assert_values"] == ["Dashboard"]


def test_locator_expression_preserves_unicode_accessible_name() -> None:
    """Curly punctuation must remain the real character the browser exposes."""

    refined = RefinedStep(
        step_number=3,
        action="click",
        element_index=2,
        locator_strategy="role",
        locator_value="I don’t have a fixed location",
        role_name="button",
        expected_result="Location fields change",
        confidence=0.95,
    )

    expression = locator_expr(refined)

    assert "I don’t have a fixed location" in expression
    assert r"\u2019" not in expression


def test_snapshot_name_still_matches_when_css_changes_visual_case() -> None:
    """CSS capitalization must not make a grounded role locator miss its control."""

    async def run() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(
                    """
                    <style>button { text-transform: uppercase; }</style>
                    <button onclick="this.dataset.clicked = 'yes'">Insights</button>
                    """
                )

                dom = await snapshot_interactive_dom(page)
                button = next(element for element in dom if element["role"] == "button")
                step = RefinedStep(
                    step_number=1,
                    action="click",
                    element_index=button["index"],
                    locator_strategy="role",
                    locator_value=button["text"],
                    role_name="button",
                    expected_result="Insights opens",
                    confidence=0.95,
                )

                await to_locator(page, step).click()

                assert button["text"] == "Insights"
                assert await page.locator("button").get_attribute("data-clicked") == "yes"
            finally:
                await browser.close()

    asyncio.run(run())


def test_adapter_preserves_parser_values_before_grounding() -> None:
    plan = {
        "workflow": {"base_url": "http://localhost:3000"},
        "steps": [
            {
                "step": 1,
                "action": "navigate",
                "description": "Open the login page.",
                "input_value": "http://localhost:3000/login",
                "target": {},
                "expected_outcome": {"url_contains": "/login"},
            },
            {
                "step": 2,
                "action": "type",
                "description": "Type the QA sandbox email address.",
                "input_value": "qa.sandbox@example.com",
                "target": {"aria_label": "Email"},
                "expected_outcome": {"field_value": "qa.sandbox@example.com"},
            },
        ],
    }

    steps, _, _ = adapt_plan(plan)

    assert steps[0].value == "http://localhost:3000/login"
    assert steps[1].value == "qa.sandbox@example.com"
    assert steps[0].expected_outcome == {"url_contains": "/login"}


class _ExpectationRecorder:
    def __init__(self, calls: list[tuple], subject) -> None:
        self.calls = calls
        self.subject = subject

    async def to_have_url(self, value, **kwargs) -> None:
        self.calls.append(("url", getattr(value, "pattern", value)))

    async def not_to_have_url(self, value, **kwargs) -> None:
        self.calls.append(("not_url", getattr(value, "pattern", value)))

    async def to_have_count(self, value, **kwargs) -> None:
        self.calls.append(("count", self.subject, value))

    async def to_have_value(self, value, **kwargs) -> None:
        self.calls.append(("value", self.subject, value))

    async def to_be_checked(self, **kwargs) -> None:
        self.calls.append(("checked", self.subject, kwargs.get("checked")))

    async def to_contain_text(self, value, **kwargs) -> None:
        self.calls.append(("text", self.subject, value))


class _AssertionPage:
    def get_by_text(self, value, **kwargs):
        return ("page_text", value, kwargs)


def test_live_refinement_executes_authored_assertion_types(monkeypatch) -> None:
    calls: list[tuple] = []
    page = _AssertionPage()
    locator = "grounded_locator"

    def fake_expect(subject):
        return _ExpectationRecorder(calls, subject)

    monkeypatch.setattr("qa_pipeline.refine_plan.expect", fake_expect)

    async def run() -> None:
        await _execute_authored_assertion(page, locator, {"url_contains": "/dashboard"})
        await _execute_authored_assertion(page, locator, {"visible_text_absent": "TypeError"})
        await _execute_authored_assertion(page, locator, {"field_value": "Antigua"})
        await _execute_authored_assertion(page, locator, {"field_value": ""})
        await _execute_authored_assertion(
            page,
            locator,
            {"checked": "false", "field_value": "unchecked"},
        )
        await _execute_authored_assertion(
            page,
            locator,
            {"visible_text_absent": "Loading...", "element_count": "1 matching item"},
        )

    asyncio.run(run())

    assert calls == [
        ("url", "/dashboard"),
        ("count", ("page_text", "TypeError", {"exact": False}), 0),
        ("value", locator, "Antigua"),
        ("value", locator, ""),
        ("checked", locator, False),
        ("count", ("page_text", "Loading...", {"exact": False}), 0),
        ("count", locator, 1),
    ]
