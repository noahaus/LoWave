"""Shared LangChain chat-model factory for the QA pipeline stages.

Imports are lazy so only the backend you actually use needs to be installed
(see the anthropic / openai / google / ollama extras in pyproject.toml).
"""
from __future__ import annotations

from importlib import import_module
from typing import Optional

from qa_pipeline import config


def _load_optional_model(backend: str, module_name: str, class_name: str):
    """Load a provider adapter or explain the exact extra the user needs."""
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        raise RuntimeError(
            f"The {backend} backend is not installed. "
            f'Run `pip install -e ".[{backend}]"` in this repository.'
        ) from exc
    return getattr(module, class_name)


def build_llm(
    backend: str,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
):
    """Construct a chat model for the chosen backend.

    Defaults come from ``config.DEFAULT_MODELS``. Override with ``--model`` or
    ``QA_MODEL`` when provider names drift. Text models are enough for both
    steps parsing and DOM grounding.

    ``max_tokens`` caps the *completion*. Parse needs several thousand tokens
    for a long action plan; grounding a single step can use a smaller cap.
    """
    resolved = model or config.DEFAULT_MODELS.get(backend)

    if backend == "claude-cli":
        from qa_pipeline.cli_chat import ClaudeCLIChat

        return ClaudeCLIChat(
            model=resolved,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if backend == "codex-cli":
        from qa_pipeline.cli_chat import CodexCLIChat

        return CodexCLIChat(
            model=resolved,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if backend == "ollama":
        ChatOllama = _load_optional_model(
            "ollama", "langchain_ollama", "ChatOllama"
        )
        return ChatOllama(
            model=resolved,
            temperature=temperature,
            format="json",
            num_ctx=64000,
        )
    if backend == "anthropic":
        ChatAnthropic = _load_optional_model(
            "anthropic", "langchain_anthropic", "ChatAnthropic"
        )
        return ChatAnthropic(
            model=resolved,
            temperature=temperature,
            max_tokens=max_tokens or 8000,
        )
    if backend == "openai":
        ChatOpenAI = _load_optional_model(
            "openai", "langchain_openai", "ChatOpenAI"
        )
        kwargs = {"model": resolved, "temperature": temperature}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return ChatOpenAI(**kwargs)
    if backend == "google":
        ChatGoogleGenerativeAI = _load_optional_model(
            "google", "langchain_google_genai", "ChatGoogleGenerativeAI"
        )
        return ChatGoogleGenerativeAI(model=resolved, temperature=temperature)

    raise SystemExit(
        f"unknown backend '{backend}'. "
        f"Choose one of: {', '.join(config.DEFAULT_MODELS)}"
    )
