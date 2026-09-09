"""Regression tests for preserving the parser's contract during refinement."""

from __future__ import annotations

import asyncio

from qa_pipeline.refine_plan import FuzzyStep, RefinedStep, _serialize, ground_step


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
