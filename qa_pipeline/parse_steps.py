#!/usr/bin/env python3
"""parse_steps.py — turn numbered English instructions into an action_plan.json.

Pipeline:  steps.txt --(text LLM)--> structured plan matching generate.py's schema

    python -m qa_pipeline.parse_steps workflow_steps.txt action_plan.json
    python -m qa_pipeline.generate    action_plan.json tests/generated.spec.ts

The workflow is described entirely by the steps file plus a few settings (the app
URL and, optionally, login credentials) from the environment or CLI — nothing
about any particular app is baked into this file.

Requirements:
  - pip install: langchain-core pydantic  + ONE backend package (see README)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from langchain_core.messages import HumanMessage, SystemMessage

from qa_pipeline import config
from qa_pipeline.llm import build_llm

GREEN, BLUE, YELLOW, DIM, BOLD, RESET = (
    "\033[92m", "\033[94m", "\033[93m", "\033[2m", "\033[1m", "\033[0m"
)

# Numbered line: "1. Do something" or "1) Do something"
_NUMBERED_STEP = re.compile(r"^\s*\d+[\.\)]\s+.+")


# ───────────────────────── schema (matches generate.py) ─────────────────────

class Target(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Optional[str] = None
    value: Optional[str] = None
    css_selector: Optional[str] = None
    aria_label: Optional[str] = None
    text_content: Optional[str] = None
    placeholder: Optional[str] = None
    fallback_description: Optional[str] = None


class Step(BaseModel):
    model_config = ConfigDict(extra="allow")
    step: int
    id: Optional[str] = None
    action: str
    description: str = ""
    target: Optional[Target] = None
    input_value: Optional[str] = None
    expected_outcome: dict = Field(default_factory=dict)


class Workflow(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = "workflow"
    application: Optional[str] = None
    base_url: Optional[str] = None
    summary: Optional[str] = None


class ActionPlan(BaseModel):
    model_config = ConfigDict(extra="allow")
    workflow: Workflow
    steps: List[Step]
    metadata: dict = Field(default_factory=dict)


# ───────────────────────────── the prompt ───────────────────────────────────

_SYSTEM_TEMPLATE = """You convert numbered English workflow instructions into a
precise, reproducible action plan for an automated web test.

You are given an ORDERED list of human-written steps. Expand each instruction into
one or more concrete UI actions. Preserve the author's intent; do not invent
extra product features that are not implied by the steps.

Output ONE JSON object and nothing else — no prose, no markdown fences. Schema:

{{
  "workflow": {{ "title", "application", "base_url", "summary" }},
  "steps": [
    {{
      "step": <int, 1-based, in order>,
      "id": "<short_snake_case_id>",
      "action": "navigate|type|click|select|scroll|hover|drag|wait|assert|press",
      "description": "<what the user should do / verify>",
      "target": {{
        "css_selector": "<best-guess selector if inferable>",
        "aria_label": "<accessible label if mentioned or implied>",
        "text_content": "<button/link text if mentioned>",
        "fallback_description": "<position, colour, context when no label>"
      }},
      "input_value": "<text typed or option selected, else null>",
      "expected_outcome": {{ "<key>": "<expected result, e.g. visible_text / url_contains / field_value>" }}
    }}
  ],
  "metadata": {{
    "total_steps": <int>,
    "known_ambiguities": [ {{ "id", "steps_affected": [..], "description" }} ],
    "recommended_edge_cases": [ "<string>", ... ]
  }}
}}

Rules:
- Be precise and unambiguous; every step must be reproducible by someone who has
  never seen the app. Never skip implied waits for pages to load when navigation
  or submit is involved.
- The first step is ALWAYS to navigate to the app under test. Use base_url
  "{base_url}" as the navigation target for that first step (add it if the
  instructions omit an explicit open/go-to).
- Set workflow.base_url to "{base_url}".
{credentials_rule}
- Prefer locating elements by visible text / labels named in the instructions.
- Do not guess a tag-specific CSS selector for search boxes or text fields
  (they may be <input>, <textarea>, or role=combobox). Prefer aria_label /
  accessible name. Treat css_selector as a last resort, and if you include
  name='q' use a tag-agnostic selector such as [name='q'].
- Record validation moments (success toast, confirmation, "assert that…",
  "you should see…") as "assert" steps.
- "Press Enter", "hit return", "press Tab" and similar keystrokes are action
  "press" with input_value set to the key name (Enter, Tab, Escape). Do not
  encode Enter as type with a newline.
- Do NOT invent credentials, URLs, or field values that are not in the steps or
  the rules above.
- Note significant ambiguities in metadata.known_ambiguities.
- Output valid JSON only."""

_CREDENTIALS_WITH = (
    '- If the workflow includes a login, use the username "{username}" and the '
    'password "{password}" for the credential fields.'
)
_CREDENTIALS_WITHOUT = (
    "- If the workflow includes a login, use the credentials exactly as written "
    "in the steps file; do not invent values."
)

INTRO = ("Here are the numbered English workflow instructions. "
         "Expand them into the JSON action plan.")
CLOSING = "Return ONLY the JSON object for the full workflow."


def build_system_prompt(base_url: str, username: Optional[str], password: Optional[str]) -> str:
    """Assemble the system prompt for the specific app under test.

    Credentials are woven in only when both are supplied; otherwise the model is
    told to use whatever credentials appear in the steps file.
    """
    if username and password:
        credentials_rule = _CREDENTIALS_WITH.format(username=username, password=password)
    else:
        credentials_rule = _CREDENTIALS_WITHOUT
    return _SYSTEM_TEMPLATE.format(base_url=base_url, credentials_rule=credentials_rule)


# ───────────────────────────── steps file I/O ───────────────────────────────

def load_steps_text(path: Path) -> str:
    """Read a steps.txt, strip blank/# comment lines, require numbered instructions."""
    raw = path.read_text(encoding="utf-8")
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kept.append(line.rstrip())

    numbered = [ln for ln in kept if _NUMBERED_STEP.match(ln)]
    if not numbered:
        sys.exit(
            f"{YELLOW}ERROR: no numbered steps found in {path}.{RESET}\n"
            f"  Expected lines like:  1. Click Sign in"
        )
    return "\n".join(kept)


def build_messages(system_prompt: str, steps_text: str) -> list:
    content = f"{INTRO}\n\n{steps_text}\n\n{CLOSING}"
    return [SystemMessage(content=system_prompt), HumanMessage(content=content)]


# ───────────────────────────── output parsing ───────────────────────────────

def extract_json(text) -> str:
    if isinstance(text, list):  # some providers return content as block list
        text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output")
    return text[start:end + 1]


# ──────────────────────────────── main ──────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Numbered English steps.txt -> action_plan.json"
    )
    ap.add_argument("steps", type=Path, help="Path to a .txt file of numbered instructions")
    ap.add_argument("output", type=Path, nargs="?", default=Path("action_plan.json"))
    ap.add_argument("--backend", default=None,
                    choices=["ollama", "anthropic", "openai", "google"],
                    help="LLM provider (default: $LLM_BACKEND or anthropic)")
    ap.add_argument("--model", default=None, help="override the backend's default model")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--base-url", default=None,
                    help="URL of the app under test (default: $QA_BASE_URL)")
    ap.add_argument("--username", default=None,
                    help="login username for the workflow (default: $QA_USERNAME)")
    ap.add_argument("--password", default=None,
                    help="login password for the workflow (default: $QA_PASSWORD)")
    args = ap.parse_args()

    backend = config.backend(args.backend)
    base_url = config.base_url(args.base_url)
    username = config.username(args.username)
    password = config.password(args.password)

    if not args.steps.exists():
        sys.exit(f"{YELLOW}ERROR: steps file not found at {args.steps}{RESET}")

    steps_text = load_steps_text(args.steps)
    step_count = sum(1 for ln in steps_text.splitlines() if _NUMBERED_STEP.match(ln))

    print(f"\n{BOLD}━━━ Steps → Action Plan ━━━{RESET}")
    print(f"  Steps:    {args.steps}  ({step_count} numbered instruction(s))")
    print(f"  App URL:  {base_url}")
    print(f"  Login:    {'configured (' + username + ')' if username else 'from steps file'}")
    print(f"  Backend:  {backend}  (model: {args.model or 'default'})\n")

    system_prompt = build_system_prompt(base_url, username, password)

    print(f"{BOLD}━━━ Analysing with {backend} ━━━{RESET}")
    llm = build_llm(backend, args.model, args.temperature)
    raw = llm.invoke(build_messages(system_prompt, steps_text)).content

    try:
        plan = ActionPlan.model_validate_json(extract_json(raw))
    except (ValueError, ValidationError) as e:
        print(f"  {YELLOW}❌ model output did not match the schema:{RESET}\n  {e}")
        print(f"\n  {DIM}Raw output (first 800 chars):{RESET}\n{str(raw)[:800]}")
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(plan.model_dump_json(indent=2, exclude_none=True))

    print(f"  ✓ {len(plan.steps)} steps parsed and validated\n")
    print(f"{BOLD}━━━ Steps ━━━{RESET}")
    for s in plan.steps:
        print(f"  {BLUE}[{s.step:>2}]{RESET} {YELLOW}{s.action.upper():<8}{RESET} {s.description}")

    print(f"\n{BOLD}━━━ Done ━━━{RESET}")
    print(f"  ✓ Written to {GREEN}{args.output}{RESET}")
    print(f"\n  Next:  python -m qa_pipeline.refine_plan --plan {args.output} --out refined_action_plan.json\n")


if __name__ == "__main__":
    main()
