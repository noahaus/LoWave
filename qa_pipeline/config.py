"""Central configuration for the QA pipeline.

Every setting can be provided three ways, in increasing order of priority:

    1. built-in default (works out of the box against the bundled demo app)
    2. environment variable, optionally loaded from a local .env file
    3. an explicit command-line flag

Nothing here is specific to any one application — point the variables at your
own app and the whole pipeline follows. See .env.example for the full list.
"""
from __future__ import annotations

import os
from pathlib import Path

# Load a .env file from the current working directory if python-dotenv is
# installed. It is an optional dependency; without it we simply read the real
# process environment.
try:  # pragma: no cover - trivial import guard
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


# ── defaults ────────────────────────────────────────────────────────────────
DEFAULT_BASE_URL = "http://localhost:3000"
DEFAULT_BACKEND = "anthropic"

# Runtime artifacts land under outputs/ (gitignored except .gitkeep).
OUTPUTS_DIR = Path("outputs")
DEFAULT_ACTION_PLAN = OUTPUTS_DIR / "plans" / "action_plan.json"
DEFAULT_REFINED_PLAN = OUTPUTS_DIR / "plans" / "refined_action_plan.json"
DEFAULT_SPEC = OUTPUTS_DIR / "tests" / "generated.spec.ts"

# Per-backend default model. Model names drift over time; override with --model
# or the QA_MODEL environment variable whenever these go stale.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "google": "gemini-1.5-pro",
    "ollama": "qwen2.5:7b",
    "claude-cli": None,
    "codex-cli": None,
}
BACKENDS = tuple(DEFAULT_MODELS)


def _resolve(cli_value, env_key, default=None):
    """CLI flag wins, then environment variable, then the built-in default."""
    if cli_value is not None:
        return cli_value
    env_value = os.environ.get(env_key)
    if env_value is not None and env_value != "":
        return env_value
    return default


def base_url(cli_value: str | None = None) -> str:
    """URL of the app under test, e.g. http://localhost:3000."""
    return _resolve(cli_value, "QA_BASE_URL", DEFAULT_BASE_URL)


def username(cli_value: str | None = None) -> str | None:
    """Login username, if the workflow needs one. None means 'not configured'."""
    return _resolve(cli_value, "QA_USERNAME", None)


def password(cli_value: str | None = None) -> str | None:
    """Login password, if the workflow needs one. None means 'not configured'."""
    return _resolve(cli_value, "QA_PASSWORD", None)


def backend(cli_value: str | None = None) -> str:
    """Resolve an API, local, or signed-in subscription CLI backend."""
    return _resolve(cli_value, "LLM_BACKEND", DEFAULT_BACKEND)


def model(cli_value: str | None = None, backend_name: str | None = None) -> str | None:
    """Model id for the chosen backend. Returns None to let the backend default apply."""
    resolved = _resolve(cli_value, "QA_MODEL", None)
    if resolved:
        return resolved
    if backend_name:
        return DEFAULT_MODELS.get(backend_name)
    return None
