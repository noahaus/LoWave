"""Incomplete generation must stay inspectable without becoming a passing test."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from qa_pipeline.generate import generate, q


REPO = Path(__file__).resolve().parents[1]


def _write_plan(path: Path, steps: list[dict], title: str = "workflow", refined: bool = False) -> Path:
    path.write_text(
        json.dumps(
            {
                "workflow": {"title": title, "base_url": "http://localhost:3000"},
                "steps": steps,
                "metadata": {"refined": refined},
            }
        )
    )
    return path


def _cli(plan: Path, output: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "qa_pipeline.generate", str(plan), str(output), *flags],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def _complete_navigate() -> dict:
    return {
        "step": 1,
        "action": "navigate",
        "description": "Open the dashboard.",
        "value": "http://localhost:3000/dashboard",
        "target": {},
    }


def _todo_click() -> dict:
    return {
        "step": 2,
        "action": "click",
        "description": "Click a control that was not found.",
        "target": {"playwright_locator": 'locator("n/a")', "css_selector": "n/a"},
        "refinement": {"grounded": True},
    }


def test_complete_plan_writes_executable_spec_without_incomplete_guard(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json", [_complete_navigate()], title="complete flow")
    out = tmp_path / "complete.spec.ts"

    warnings = generate(plan, out)

    spec = out.read_text()
    assert warnings == 0
    assert "throw new Error" not in spec
    assert "await page.goto('http://localhost:3000/dashboard'" in spec
    assert "test.skip" not in spec


def test_todo_step_writes_inspectable_spec_that_fails_before_page_actions(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json", [_complete_navigate(), _todo_click()])
    out = tmp_path / "incomplete.spec.ts"

    warnings = generate(plan, out)

    spec = out.read_text()
    assert warnings >= 1
    assert "// TODO:" in spec
    assert "await page.goto(" in spec
    throw_at = spec.index("throw new Error")
    goto_at = spec.index("await page.goto(")
    assert throw_at < goto_at
    assert "incomplete" in spec[throw_at : throw_at + 200].lower()
    assert "test.skip" not in spec


def test_low_confidence_step_is_incomplete_coverage(tmp_path: Path) -> None:
    plan = _write_plan(
        tmp_path / "plan.json",
        [
            {
                "step": 1,
                "action": "click",
                "description": "Open the client.",
                "target": {"playwright_locator": 'get_by_role("link", name="Client")'},
                "refinement": {"grounded": True, "confidence": 0.35},
            }
        ],
        refined=True,
    )
    out = tmp_path / "low.conf.spec.ts"

    warnings = generate(plan, out)
    spec = out.read_text()

    assert warnings >= 1
    assert "// REVIEW: low-confidence grounding (0.35)" in spec
    assert spec.index("throw new Error") < spec.index("getByRole")
    assert "incomplete" in spec.lower()


def test_complete_terminate_title_does_not_trigger_incomplete_cli_status(tmp_path: Path) -> None:
    plan = _write_plan(
        tmp_path / "plan.json",
        [
            {
                "step": 1,
                "action": "terminate",
                "description": "End the workflow here.",
                "target": {},
            }
        ],
        title="GENERATED_SPEC_INCOMPLETE",
    )
    out = tmp_path / "named.spec.ts"

    warnings = generate(plan, out)
    result = _cli(plan, out)

    spec = out.read_text()
    assert warnings == 0
    assert "throw new Error" not in spec
    assert "Flow terminated intentionally at step 1." in spec
    assert result.returncode == 0, result.stderr + result.stdout


def test_explicit_failed_grounding_without_notes_is_unresolved(tmp_path: Path) -> None:
    plan = _write_plan(
        tmp_path / "plan.json",
        [
            {
                "step": 1,
                "action": "click",
                "description": "Save the record.",
                "target": {
                    "css_selector": "button",
                    "text_content": "Save",
                    "playwright_locator": 'get_by_role("button", name="Save")',
                },
                "refinement": {"grounded": False, "confidence": 0.8},
            }
        ],
        refined=True,
    )
    out = tmp_path / "ungrounded.spec.ts"

    warnings = generate(plan, out)
    spec = out.read_text()

    assert warnings >= 1
    assert spec.index("throw new Error") < spec.index("Save")
    assert ".click()" not in "\n".join(
        ln for ln in spec.splitlines() if not ln.lstrip().startswith("//")
    )
    assert _cli(plan, out).returncode == 2


def test_empty_workflow_is_incomplete_not_a_successful_noop(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json", [], title="empty workflow")
    out = tmp_path / "empty.spec.ts"

    warnings = generate(plan, out)
    spec = out.read_text()

    assert warnings == 1
    assert "throw new Error" in spec
    assert "incomplete" in spec.lower()
    assert "test.skip" not in spec


def test_terminate_only_workflow_remains_complete(tmp_path: Path) -> None:
    plan = _write_plan(
        tmp_path / "plan.json",
        [
            {
                "step": 1,
                "action": "terminate",
                "description": "End the workflow here.",
                "target": {},
            }
        ],
        title="done on purpose",
    )
    out = tmp_path / "terminate.spec.ts"

    warnings = generate(plan, out)
    spec = out.read_text()

    assert warnings == 0
    assert "throw new Error" not in spec
    assert "Flow terminated intentionally at step 1." in spec


def test_partial_unsupported_assertion_is_incomplete(tmp_path: Path) -> None:
    plan = _write_plan(
        tmp_path / "plan.json",
        [
            {
                "step": 1,
                "action": "assert",
                "description": "Verify save and the absence of errors.",
                "target": {"playwright_locator": 'get_by_text("Draft saved")'},
                "expected_outcome": {
                    "visible_text": "Draft saved",
                    "no_error": "No validation errors are displayed",
                },
                "refinement": {"grounded": True, "confidence": 1.0},
            }
        ],
        refined=True,
    )
    out = tmp_path / "partial.spec.ts"

    warnings = generate(plan, out)
    spec = out.read_text()

    assert warnings >= 1
    assert "toBeVisible()" in spec
    assert "// TODO:" in spec
    assert spec.index("throw new Error") < spec.index("toBeVisible()")


def test_allow_incomplete_exports_draft_with_runtime_guard(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json", [_todo_click()])
    out = tmp_path / "draft.spec.ts"

    result = _cli(plan, out, "--allow-incomplete")

    assert result.returncode == 0, result.stderr
    spec = out.read_text()
    assert "throw new Error" in spec
    assert "// TODO:" in spec


def test_default_cli_exit_is_nonzero_for_incomplete_output(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json", [_todo_click()])
    out = tmp_path / "todo.spec.ts"

    result = _cli(plan, out)

    assert result.returncode != 0
    assert result.returncode == 2
    assert out.exists()
    assert "throw new Error" in out.read_text()


def test_complete_cli_exit_is_zero(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json", [_complete_navigate()], title="complete flow")
    out = tmp_path / "ok.spec.ts"

    result = _cli(plan, out)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "throw new Error" not in out.read_text()


def test_empty_cli_exit_is_nonzero(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json", [])
    out = tmp_path / "empty.spec.ts"

    result = _cli(plan, out)

    assert result.returncode == 2
    assert "throw new Error" in out.read_text()


def test_multiline_step_description_cannot_inject_executable_spec_lines(tmp_path: Path) -> None:
    injected = "End the flow.\n  await page.evaluate('pwned()');\n  // trailing"
    plan = _write_plan(
        tmp_path / "plan.json",
        [
            {
                "step": 1,
                "action": "terminate",
                "description": injected,
                "target": {},
            }
        ],
    )
    out = tmp_path / "comment.spec.ts"

    generate(plan, out)
    spec = out.read_text()

    executable = [
        ln
        for ln in spec.splitlines()
        if "page.evaluate" in ln and not ln.lstrip().startswith("//")
    ]
    assert executable == []
    assert "pwned()" in spec


def test_authored_assertion_newlines_stay_in_string_literal(tmp_path: Path) -> None:
    plan = _write_plan(
        tmp_path / "plan.json",
        [
            {
                "step": 1,
                "action": "assert",
                "description": "Keep the visible copy.",
                "target": {"playwright_locator": 'get_by_text("line1")'},
                "expected_outcome": {"visible_text": "line1\nline2"},
                "refinement": {"grounded": True, "confidence": 1.0},
            }
        ],
        refined=True,
    )
    out = tmp_path / "assert.spec.ts"

    generate(plan, out)
    spec = out.read_text()

    assert q("line1\nline2") in spec
    assert "toContainText(" in spec


def test_missing_refinement_confidence_does_not_crash_generate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = _write_plan(
        tmp_path / "plan.json",
        [
            {
                "step": 1,
                "action": "click",
                "description": "Open the client.",
                "target": {"playwright_locator": 'get_by_role("link", name="Client")'},
                "refinement": {"grounded": True, "confidence": None},
            }
        ],
        refined=True,
    )
    out = tmp_path / "noconf.spec.ts"

    warnings = generate(plan, out)

    captured = capsys.readouterr()
    assert out.exists()
    assert warnings == 0
    assert "conf=" in captured.out
    assert "throw new Error" not in out.read_text()
