"""LangChain chat models backed by signed-in Claude Code and Codex CLIs.

Prompts are sent through stdin, never shell interpolation or command arguments.
That keeps large DOM snapshots safe from quoting and argument-length problems.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from typing import Any, Optional, Sequence, Type

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, ValidationError


DEFAULT_TIMEOUT = 300
_ROLE_PREFIX = {"system": "SYSTEM", "human": "USER", "ai": "ASSISTANT"}
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def flatten_messages(messages: Sequence[BaseMessage]) -> str:
    """Flatten LangChain messages into one role-labelled CLI prompt."""
    if len(messages) == 1:
        return str(messages[0].content)
    return "\n\n".join(
        f"{_ROLE_PREFIX.get(message.type, message.type.upper())}:\n{message.content}"
        for message in messages
    )


def extract_json(text: str) -> Any:
    """Recover the first complete JSON value from a plain or decorated reply."""
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fenced = _FENCE.search(stripped)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(stripped[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError(f"no JSON found in model reply:\n{stripped[:500]}")


def _unwrap_cli_envelope(raw: str) -> str:
    """Extract assistant text from a Claude JSON envelope or return bare text."""
    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            envelope = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        if isinstance(envelope, dict) and envelope.get("type") == "result":
            if envelope.get("is_error") is True:
                detail = _cli_failure_detail("", stripped) or "Provider reported an error"
                raise RuntimeError(f"CLI response failed: {detail}")
            value = envelope.get("result")
            if isinstance(value, str):
                return value.strip()
    return stripped


def _cli_failure_detail(stderr: str, stdout: str) -> str:
    """Return bounded diagnostics without echoing arbitrary model output."""
    detail = stderr.strip()
    if detail:
        return detail[:500]
    try:
        envelope = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return ""
    if not isinstance(envelope, dict):
        return ""
    for key in ("error", "result", "message"):
        value = envelope.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
        if isinstance(value, dict):
            message = value.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:500]
    return ""


class CLIChatModel(BaseChatModel):
    """Base chat model that invokes a provider CLI without a shell."""

    model: Optional[str] = None
    timeout: int = DEFAULT_TIMEOUT
    temperature: float = 0.0
    max_tokens: Optional[int] = None

    @property
    def _llm_type(self) -> str:
        return "cli-chat"

    def _command(self) -> list[str]:
        raise NotImplementedError

    def _check_binary(self) -> None:
        executable = self._command()[0]
        if shutil.which(executable) is None:
            raise RuntimeError(
                f"'{executable}' is not on PATH. Install and sign in to it, "
                "or choose another LLM backend."
            )

    def _run(self, prompt: str) -> str:
        self._check_binary()
        command = self._command()
        try:
            process = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"{command[0]} timed out after {self.timeout}s"
            ) from None
        if process.returncode != 0:
            detail = _cli_failure_detail(process.stderr, process.stdout)
            raise RuntimeError(f"{command[0]} exited {process.returncode}: {detail}")
        return process.stdout

    async def _arun(self, prompt: str) -> str:
        self._check_binary()
        command = self._command()
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode()), timeout=self.timeout
            )
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            try:
                process.kill()
            except ProcessLookupError:
                pass  # The process may have exited just before cancellation.
            await process.wait()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise RuntimeError(
                f"{command[0]} timed out after {self.timeout}s"
            ) from None
        if process.returncode != 0:
            detail = _cli_failure_detail(
                stderr.decode(errors="replace"), stdout.decode(errors="replace")
            )
            raise RuntimeError(f"{command[0]} exited {process.returncode}: {detail}")
        return stdout.decode(errors="replace")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt = flatten_messages(messages)
        text = _unwrap_cli_envelope(self._run(prompt))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt = flatten_messages(messages)
        text = _unwrap_cli_envelope(await self._arun(prompt))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def with_structured_output(
        self,
        schema: Type[BaseModel],
        *,
        include_raw: bool = False,
        max_retries: int = 1,
        **kwargs: Any,
    ) -> Runnable:
        """Validate prompt-based JSON output against a Pydantic schema."""
        if include_raw:
            raise NotImplementedError("include_raw=True is not supported by CLI backends")
        if max_retries < 0:
            raise ValueError("max_retries must be zero or greater")

        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        instruction = (
            "\n\nRespond with one JSON object and nothing else. No prose or "
            "markdown fences. It must validate against this JSON Schema:\n\n"
            f"{schema_json}"
        )

        def prepared_messages(value: Any) -> list[BaseMessage]:
            if hasattr(value, "to_messages"):
                source = list(value.to_messages())
            elif isinstance(value, BaseMessage):
                source = [value]
            elif isinstance(value, str):
                source = [HumanMessage(content=value)]
            else:
                source = list(value)
            messages = [message.model_copy(deep=True) for message in source]
            if not messages:
                raise ValueError("structured output requires at least one message")
            messages[-1].content = f"{messages[-1].content}{instruction}"
            return messages

        def parse(text: str) -> BaseModel:
            return schema.model_validate(extract_json(text))

        def invoke(value: Any) -> BaseModel:
            messages = prepared_messages(value)
            last_error: Exception | None = None
            for attempt in range(max_retries + 1):
                text = _unwrap_cli_envelope(self._run(flatten_messages(messages)))
                try:
                    return parse(text)
                except (ValidationError, ValueError) as error:
                    last_error = error
                    if attempt == max_retries:
                        raise
                    messages.extend(
                        [
                            AIMessage(content=text),
                            HumanMessage(
                                content=(
                                    f"That reply did not validate: {error}\n"
                                    "Return only the corrected JSON object."
                                )
                            ),
                        ]
                    )
            raise last_error or RuntimeError("structured output failed")

        async def ainvoke(value: Any) -> BaseModel:
            messages = prepared_messages(value)
            last_error: Exception | None = None
            for attempt in range(max_retries + 1):
                text = _unwrap_cli_envelope(await self._arun(flatten_messages(messages)))
                try:
                    return parse(text)
                except (ValidationError, ValueError) as error:
                    last_error = error
                    if attempt == max_retries:
                        raise
                    messages.extend(
                        [
                            AIMessage(content=text),
                            HumanMessage(
                                content=(
                                    f"That reply did not validate: {error}\n"
                                    "Return only the corrected JSON object."
                                )
                            ),
                        ]
                    )
            raise last_error or RuntimeError("structured output failed")

        return RunnableLambda(invoke, afunc=ainvoke)


class ClaudeCLIChat(CLIChatModel):
    """Claude Code headless mode using the signed-in Claude subscription."""

    @property
    def _llm_type(self) -> str:
        return "claude-cli"

    def _command(self) -> list[str]:
        # DOM and workflow text are model inputs, not permission to use tools.
        command = [
            "claude", "-p", "--output-format", "json", "--tools", "",
            "--strict-mcp-config", "--no-session-persistence",
        ]
        if self.model:
            command.extend(["--model", self.model])
        return command


class CodexCLIChat(CLIChatModel):
    """Codex headless mode using the signed-in ChatGPT subscription."""

    @property
    def _llm_type(self) -> str:
        return "codex-cli"

    def _command(self) -> list[str]:
        command = ["codex", "exec", "--sandbox", "read-only", "--ephemeral"]
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")
        return command
