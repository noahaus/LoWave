"""The subscription CLIs are first-class optional backends."""

from __future__ import annotations

from importlib import import_module
import re

import pytest

from qa_pipeline import config
from qa_pipeline.cli_chat import ClaudeCLIChat, CodexCLIChat
from qa_pipeline.llm import build_llm


def test_backend_catalog_includes_subscription_clis() -> None:
    assert "claude-cli" in config.BACKENDS
    assert "codex-cli" in config.BACKENDS
    assert config.DEFAULT_MODELS["claude-cli"] is None
    assert config.DEFAULT_MODELS["codex-cli"] is None


@pytest.mark.parametrize(
    ("backend", "expected_type"),
    [("claude-cli", ClaudeCLIChat), ("codex-cli", CodexCLIChat)],
)
def test_model_factory_builds_subscription_cli_backends(backend, expected_type) -> None:
    assert isinstance(build_llm(backend), expected_type)


def test_missing_optional_backend_explains_how_to_install_it(monkeypatch) -> None:
    def import_without_ollama(name):
        if name == "langchain_ollama":
            raise ModuleNotFoundError(
                "No module named 'langchain_ollama'", name="langchain_ollama"
            )
        return import_module(name)

    monkeypatch.setattr("qa_pipeline.llm.import_module", import_without_ollama)

    install_command = 'pip install -e ".[ollama]"'
    with pytest.raises(RuntimeError, match=re.escape(install_command)):
        build_llm("ollama")
