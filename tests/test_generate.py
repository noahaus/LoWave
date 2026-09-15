"""Behavioral tests for trustworthy Playwright generation."""

from __future__ import annotations

import pytest

from qa_pipeline.generate import _compile_step, _refined_locator_expr, emit_assert, emit_step, q


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


@pytest.mark.parametrize("key,matcher", [("field_value", "toHaveValue"), ("visible_text", "toBeVisible"), ("text_contains", "toContainText")])
def test_explicit_literal_expectations_are_not_discarded_as_prose(key, matcher):
    value = "Noah pipeline local journey. The golden thread appeared exactly when I needed it. " * 2
    result = emit_assert(_step({key: value}), {"grounded": True})
    assert "TODO" not in result
    assert q(value) in result
    assert matcher in result


@pytest.mark.parametrize("visible", [True, "true"])
def test_explicit_title_and_visibility_generate_both_checks(visible):
    result = emit_assert(_step({"title": "Private", "visible": visible}), {"grounded": True})
    assert "TODO" not in result
    assert ".toHaveAttribute('title', 'Private')" in result
    assert ".toBeVisible()" in result


def test_title_only_grounded_name_generates_visibility_not_text_assertion() -> None:
    target = {
        "playwright_locator": 'get_by_role("button", name="Private", exact=True)',
        "strategy": "role", "locator_value": "Private", "role": "button",
        "title_only_name": "Private",
    }
    result = emit_assert(_step({"assert_values": ["Private"]}, target), {"grounded": True})
    assert result == "await expect(page.getByRole('button', { name: 'Private', exact: true })).toBeVisible();"


def test_rendered_text_grounding_keeps_text_assertion() -> None:
    target = {
        "playwright_locator": 'get_by_role("button", name="Private", exact=True)',
        "strategy": "role", "locator_value": "Private", "role": "button",
    }
    result = emit_assert(_step({"assert_values": ["Private"]}, target), {"grounded": True})
    assert result.endswith(".toContainText('Private');")


def test_spoofed_title_metadata_with_role_name_mismatch_keeps_text_assertion() -> None:
    target = {
        "playwright_locator": 'get_by_role("button", name="Other", exact=True)',
        "strategy": "role", "locator_value": "Other", "role": "button",
        "title_only_name": "Private",
    }
    result = emit_assert(_step({"assert_values": ["Private"]}, target), {"grounded": True})
    assert result.endswith(".toContainText('Private');")


def test_visible_text_generation_is_pagewide_while_text_contains_is_scoped() -> None:
    target = {
        "playwright_locator": 'get_by_role("heading", name="my journey", exact=True)',
        "strategy": "role", "locator_value": "my journey", "role": "heading",
    }
    pagewide = emit_assert(_step({"visible_text": "Golden thread"}, target), {"grounded": True})
    scoped = emit_assert(_step({"text_contains": "Golden thread"}, target), {"grounded": True})
    assert pagewide == "await expect(page.getByText('Golden thread', { exact: false }).filter({ visible: true }).first()).toBeVisible();"
    assert "getByRole('heading'" in scoped and ".toContainText('Golden thread');" in scoped


def test_parser_exact_text_and_heading_expectations_compile_directly() -> None:
    exact = emit_assert(_step({"visible_text_exact": "Golden thread"}), {"grounded": True})
    heading = emit_assert(_step({"visible_heading": "Welcome to the Portal"}), {"grounded": True})
    assert exact == "await expect(page.getByText('Golden thread', { exact: true }).filter({ visible: true }).first()).toBeVisible();"
    assert heading == "await expect(page.getByRole('heading', { name: 'Welcome to the Portal', exact: true }).filter({ visible: true }).first()).toBeVisible();"


def test_parser_visibility_and_accessible_name_expectations_compile() -> None:
    target = {
        "playwright_locator": 'get_by_role("button", name="Shared to Portal", exact=True)',
        "strategy": "role", "locator_value": "Shared to Portal", "role": "button",
    }
    result = emit_assert(
        _step({"visibility": "visible", "accessible_name": "Shared to Portal"}, target),
        {"grounded": True},
    )
    assert "TODO" not in result
    assert result == (
        "await expect(page.getByRole('button', { name: 'Shared to Portal', exact: true })).toHaveAccessibleName('Shared to Portal');\n"
        "  await expect(page.getByRole('button', { name: 'Shared to Portal', exact: true })).toBeVisible();"
    )


def test_failed_pagewide_assertion_remains_incomplete() -> None:
    step = _step({"visible_text_exact": "Golden thread"})
    step["refinement"] = {"grounded": False, "confidence": 0.2, "notes": "wrong target"}
    line, needs_review = _compile_step(step, "http://localhost")
    assert "TODO" in line
    assert needs_review


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

    assert result == "await expect(page.getByText('Email Campaigns', { exact: false }).filter({ visible: true }).first()).toBeVisible();"


def test_field_value_uses_playwright_value_matcher() -> None:
    result = emit_assert(
        _step({"field_value": "Antigua"}),
        {"grounded": True},
    )

    assert result == "await expect(page.getByLabel('City', { exact: true })).toHaveValue('Antigua');"


def test_authored_text_survives_a_substitute_text_locator() -> None:
    result = emit_assert(
        _step({"visible_text": "Email Campaigns"},
              {"playwright_locator": 'get_by_text("Sign in")'}),
        {"grounded": True},
    )
    assert result == "await expect(page.getByText('Email Campaigns', { exact: false }).filter({ visible: true }).first()).toBeVisible();"


def test_empty_field_value_is_a_real_assertion() -> None:
    result = emit_assert(
        _step({"field_value": ""}),
        {"grounded": True},
    )

    assert result == "await expect(page.getByLabel('City', { exact: true })).toHaveValue('');"


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


def test_checked_state_does_not_also_assert_checkbox_value() -> None:
    result = emit_assert(
        _step({"checked": "false", "field_value": "unchecked"}),
        {"grounded": True},
    )

    assert result == (
        "await expect(page.getByLabel('City', { exact: true }))"
        ".toBeChecked({ checked: false });"
    )


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


def test_lucide_data_selector_has_a_react_class_fallback() -> None:
    target = {
        "strategy": "css",
        "locator_value": 'button:has([data-lucide="settings"])',
    }

    assert _refined_locator_expr(target) == (
        "page.locator('button:has(svg[data-lucide=\"settings\"]), "
        "button:has(svg.lucide-settings)')"
    )


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


def test_raw_sentinel_locator_becomes_todo() -> None:
    step = {
        "step": 6,
        "action": "click",
        "description": "Click a control that was not found.",
        "target": {"playwright_locator": 'locator("n/a")'},
        "refinement": {"grounded": True},
    }

    result, is_todo = emit_step(step, "http://localhost:3000")

    assert is_todo is True
    assert result.startswith("// TODO: step 6 click has no locatable target")


def test_refined_text_locator_calls_first_method() -> None:
    step = {
        "step": 7,
        "action": "assert",
        "description": "Confirm the welcome message.",
        "target": {
            "playwright_locator": 'get_by_text("Welcome")',
            "text_content": "Welcome",
        },
        "expected_outcome": {},
    }

    result = emit_assert(step, {"grounded": True})

    assert result == "await expect(page.getByText('Welcome').first()).toBeVisible();"


def test_partial_assertion_surfaces_unhandled_semantics() -> None:
    result = emit_assert(
        _step(
            {
                "visible_text": "Draft saved",
                "field_value": 'Full name still shows "Ada", Email still shows "ada@example.com"',
                "no_error": "No validation errors are displayed",
            },
            {"playwright_locator": 'get_by_text("Draft saved")'},
        ),
        {"grounded": True},
    )

    assert "toBeVisible()" in result
    assert "// TODO: assert step 7 cannot safely express field_value, no_error" in result


def test_low_confidence_grounding_is_visible_in_generated_step() -> None:
    step = {
        "step": 8,
        "action": "click",
        "description": "Open the client.",
        "target": {"playwright_locator": 'get_by_role("link", name="Client")'},
        "refinement": {"grounded": True, "confidence": 0.35},
    }

    result, needs_review = _compile_step(step, "http://localhost:3000")

    assert needs_review is True
    assert result.startswith("// REVIEW: low-confidence grounding (0.35); verify this locator.\n")
    assert "getByRole('link', { name: 'Client'" in result


def test_url_path_is_safe_inside_javascript_regex_literal() -> None:
    result = emit_assert(
        _step({"url_contains": "/marketplace-terms"}),
        {"grounded": True},
    )

    assert result == r"await expect(page).toHaveURL(/\/marketplace-terms/);"


def test_js_string_escapes_carriage_returns() -> None:
    """CR must not be emitted literally, where it terminates a JS string."""
    assert q("line one\rline two") == "'line one\\rline two'"


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
