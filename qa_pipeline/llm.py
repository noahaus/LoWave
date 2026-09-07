"""Shared LangChain chat-model factory for the QA pipeline stages.

Imports are lazy so only the backend you actually use needs to be installed
(see the anthropic / openai / google / ollama extras in pyproject.toml).
"""
from __future__ import annotations

from typing import Optional

from qa_pipeline import config


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

    if backend == "ollama":
        from langchain_ollama import ChatOllama  # local, free, weaker on dense UI
        return ChatOllama(
            model=resolved,
            temperature=temperature,
            format="json",
            num_ctx=64000,
        )
    if backend == "anthropic":
        from langchain_anthropic import ChatAnthropic  # strong on screenshots
        return ChatAnthropic(
            model=resolved,
            temperature=temperature,
            max_tokens=max_tokens or 8000,
        )
    if backend == "openai":
        from langchain_openai import ChatOpenAI
        kwargs = {"model": resolved, "temperature": temperature}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return ChatOpenAI(**kwargs)
    if backend == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=resolved, temperature=temperature)

    raise SystemExit(
        f"unknown backend '{backend}'. "
        f"Choose one of: {', '.join(config.DEFAULT_MODELS)}"
    )
