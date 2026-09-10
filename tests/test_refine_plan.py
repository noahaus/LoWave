"""Regression tests for preserving the parser's contract during refinement."""

from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from qa_pipeline.refine_plan import (
    FuzzyStep,
    RefinedStep,
    _execute_authored_assertion,
    _serialize,
    adapt_plan,
    ground_step,
    locator_expr,
    snapshot_interactive_dom,
    to_locator,
)


class _FixedChain:
    """Return one predetermined refinement without invoking an external model."""

    def __init__(self, result: RefinedStep) -> None:
        self.result = result

    async def ainvoke(self, _payload: dict) -> RefinedStep:
        return self.result.model_copy(deep=True)


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
