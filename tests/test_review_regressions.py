"""Boundary cases identified during independent PR review."""
import subprocess
import sys

from qa_pipeline.generate import INCOMPLETE_EXIT_CODE, _refined_locator_expr, emit_assert


def test_accessible_name_parentheses_survive_translation():
    assert _refined_locator_expr({"playwright_locator": 'get_by_role("button", name="Save (draft)", exact=True)'}) == "page.getByRole('button', { name: 'Save (draft)', exact: true })"


def test_accessible_name_escaped_quotes_survive_translation():
    assert _refined_locator_expr({"playwright_locator": 'get_by_role("button", name="Save \\"draft\\"")'}) == "page.getByRole('button', { name: 'Save \"draft\"' })"


def test_grounded_text_visibility_invokes_first_locator():
    step = {"step": 1, "action": "assert", "target": {"playwright_locator": 'get_by_text("Ready")'}, "expected_outcome": {}}
    assert emit_assert(step, {"grounded": True}) == "await expect(page.getByText('Ready').first()).toBeVisible();"


def test_usage_error_is_not_incomplete_generation():
    result = subprocess.run([sys.executable, "-m", "qa_pipeline.generate", "--unknown-option"], capture_output=True, text=True)
    assert result.returncode != 0
    assert result.returncode != INCOMPLETE_EXIT_CODE
