"""Behavioral tests for trustworthy Playwright generation."""

from __future__ import annotations

import pytest

from qa_pipeline.generate import _refined_locator_expr, emit_assert, emit_step


def _step(expected_outcome: dict, target: dict | None = None) -> dict:
    return {
        "step": 7,
        "action": "assert",
        "description": "Verify the requested outcome.",
        "target": target or {
            "playwright_locator": 'get_by_label("City", exact=True)',
            "strategy": "label",
            "locator_value": "City",
        },
        "expected_outcome": expected_outcome,
    }


@pytest.mark.parametrize("key", ["visible_text_absent", "not_visible_text"])
def test_absence_assertion_cannot_be_replaced_by_unrelated_grounding(key: str) -> None:
    """Removing this branch would make an absent-error check pass on a login button."""

    target = {
        "playwright_locator": 'get_by_role("button", name="Send me a magic link", exact=True)',
        "strategy": "role",
        "locator_value": "Send me a magic link",
        "role": "button",
    }
    result = emit_assert(
        _step(
            {
                key: "This page couldn't load",
                "assert_values": ["Send me a magic link"],
            },
            target,
        ),
        {"grounded": True},
    )

    assert result == (
        "await expect(page.getByText('This page couldn\\'t load', "
        "{ exact: false })).toHaveCount(0);"
    )


def test_visible_text_keeps_authored_expectation_over_substitute_text() -> None:
    """A substitute element may fail, but it must never redefine success."""

    target = {
        "playwright_locator": 'get_by_role("button", name="Sign in", exact=True)',
        "strategy": "role",
        "locator_value": "Sign in",
        "role": "button",
    }
    result = emit_assert(
        _step(
            {"visible_text": "Email Campaigns", "assert_values": ["Sign in"]},
            target,
        ),
        {"grounded": True},
    )

    assert result == (
        "await expect(page.getByRole('button', { name: 'Sign in', exact: true }))"
        ".toContainText('Email Campaigns');"
    )


def test_field_value_uses_playwright_value_matcher() -> None:
    result = emit_assert(
        _step({"field_value": "Antigua"}),
        {"grounded": True},
    )

    assert result == "await expect(page.getByLabel('City', { exact: true })).toHaveValue('Antigua');"


@pytest.mark.parametrize(
    ("checked", "expected"),
    [
        ("true", "await expect(page.getByLabel('City', { exact: true })).toBeChecked({ checked: true });"),
        ("false", "await expect(page.getByLabel('City', { exact: true })).toBeChecked({ checked: false });"),
    ],
)
def test_checkbox_state_uses_playwright_checked_matcher(checked: str, expected: str) -> None:
    result = emit_assert(
        _step({"checked": checked}),
        {"grounded": True},
    )

    assert result == expected


def test_element_count_uses_playwright_count_matcher() -> None:
    result = emit_assert(
        _step({"element_count": "0 elements matching the City field"}),
        {"grounded": True},
    )

    assert result == "await expect(page.getByLabel('City', { exact: true })).toHaveCount(0);"


def test_unsupported_assertion_is_a_visible_todo() -> None:
    result = emit_assert(
        _step({"new_tab_opened": "A second tab exists"}),
        {"grounded": True},
    )

    assert result.startswith("// TODO: assert step 7 cannot safely express")


def test_css_locator_keeps_parentheses_and_quotes_intact() -> None:
    """Regex parsing must not corrupt valid CSS containing nested punctuation."""

    target = {
        "playwright_locator": 'locator("input:not([type=\\"hidden\\"])")',
        "strategy": "css",
        "locator_value": 'input:not([type="hidden"])',
    }

    assert _refined_locator_expr(target) == "page.locator('input:not([type=\"hidden\"])')"


def test_multiline_failure_note_stays_inside_comment() -> None:
    step = {
        "step": 4,
        "action": "click",
        "description": "Click the missing control.",
        "target": {"playwright_locator": 'locator("n/a")'},
        "refinement": {
            "grounded": False,
            "confidence": 0.2,
            "notes": "Target missing\nSecond line must remain a comment",
        },
    }

    result, is_todo = emit_step(step, "http://localhost:3000")

    assert is_todo is True
    assert "Target missing Second line must remain a comment" in result
    assert result.count("\n") == 1


def test_sentinel_locator_becomes_todo_instead_of_invalid_css() -> None:
    step = {
        "step": 5,
        "action": "click",
        "description": "Click a control that was not found.",
        "target": {
            "playwright_locator": 'locator("n/a")',
            "strategy": "css",
            "locator_value": "n/a",
            "css_selector": "n/a",
        },
        "refinement": {"grounded": True},
    }

    result, is_todo = emit_step(step, "http://localhost:3000")

    assert is_todo is True
    assert result.startswith("// TODO: step 5 click has no locatable target")


def test_url_path_is_safe_inside_javascript_regex_literal() -> None:
    result = emit_assert(
        _step({"url_contains": "/marketplace-terms"}),
        {"grounded": True},
    )

    assert result == r"await expect(page).toHaveURL(/\/marketplace-terms/);"


def test_navigation_waits_for_load_not_network_idle() -> None:
    step = {
        "step": 1,
        "action": "navigate",
        "description": "Open the dashboard.",
        "value": "http://localhost:3000/dashboard",
        "target": {},
    }

    result, is_todo = emit_step(step, "http://localhost:3000")

    assert is_todo is False
    assert result == "await page.goto('http://localhost:3000/dashboard', { waitUntil: 'load' });"


def test_terminate_marker_is_an_intentional_noop() -> None:
    step = {
        "step": 9,
        "action": "terminate",
        "description": "End the workflow here.",
        "target": {},
    }

    result, is_todo = emit_step(step, "http://localhost:3000")

    assert is_todo is False
    assert result == "// Flow terminated intentionally at step 9."
