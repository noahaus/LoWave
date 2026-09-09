"""Behavioral tests for trustworthy Playwright generation."""

from __future__ import annotations

import pytest

from qa_pipeline.generate import emit_assert


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
