"""Contract tests for subscription-backed Claude and Codex chat models."""

from __future__ import annotations

import json
import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from qa_pipeline.cli_chat import (
    ClaudeCLIChat,
    CodexCLIChat,
    extract_json,
    flatten_messages,
)


class _Answer(BaseModel):
    action: str
    confidence: float


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('prefix {"nested": {"value": "brace } in text"}} suffix', {"nested": {"value": "brace } in text"}}),
        ('noise [1, {"a": 2}] trailing', [1, {"a": 2}]),
    ],
)
def test_extract_json_recovers_supported_reply_shapes(reply: str, expected) -> None:
    assert extract_json(reply) == expected


def test_extract_json_rejects_non_json_reply() -> None:
    with pytest.raises(ValueError, match="no JSON found"):
        extract_json("nothing structured here")


def test_flatten_messages_preserves_roles() -> None:
    assert flatten_messages([HumanMessage("hello")]) == "hello"
    assert flatten_messages([SystemMessage("rules"), HumanMessage("hello")]) == (
        "SYSTEM:\nrules\n\nUSER:\nhello"
    )


def test_cli_commands_are_headless_and_read_prompt_from_stdin() -> None:
    claude = ClaudeCLIChat(model="sonnet")._command()
    codex = CodexCLIChat(model="gpt-5.6-sol")._command()

    assert claude[0] == "claude"
    assert "-p" in claude
    assert claude[claude.index("--output-format") + 1] == "json"
    assert codex[:2] == ["codex", "exec"]
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    assert codex[-1] == "-"


def test_cli_commands_limit_tools_and_disable_session_storage() -> None:
    claude = ClaudeCLIChat()._command()
    codex = CodexCLIChat()._command()
    assert claude[claude.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in claude
    assert "--no-session-persistence" in claude
    assert "--ephemeral" in codex


@pytest.mark.parametrize("cancel", [True, False])
def test_async_runner_reaps_child_on_cancellation_or_timeout(monkeypatch, cancel):
    async def exercise():
        started = asyncio.Event()
        class Process:
            returncode = None
            killed = False
            waited = False
            async def communicate(self, prompt):
                started.set()
                await asyncio.Event().wait()
            def kill(self):
                self.killed = True
            async def wait(self):
                self.waited = True
                self.returncode = -9
        child = Process()
        async def create(*args, **kwargs):
            return child
        monkeypatch.setattr("qa_pipeline.cli_chat.shutil.which", lambda _: "/fake/cli")
        monkeypatch.setattr("qa_pipeline.cli_chat.asyncio.create_subprocess_exec", create)
        task = asyncio.create_task(CodexCLIChat(timeout=1)._arun("test"))
        await started.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
        assert child.killed
        assert child.waited
    asyncio.run(exercise())


@pytest.mark.parametrize("model_type", [ClaudeCLIChat, CodexCLIChat])
def test_sync_runner_sends_prompt_via_stdin(monkeypatch, model_type) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout="PONG\n", stderr="")

    monkeypatch.setattr("qa_pipeline.cli_chat.shutil.which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr("qa_pipeline.cli_chat.subprocess.run", fake_run)

    result = model_type().invoke("PING")

    assert result.content == "PONG"
    assert captured["input"] == "PING"
    assert "shell" not in captured or captured["shell"] is False
    assert "PING" not in captured["command"]


def test_claude_json_envelope_is_unwrapped(monkeypatch) -> None:
    envelope = json.dumps({"type": "result", "result": "PONG", "total_cost_usd": 0})
    monkeypatch.setattr("qa_pipeline.cli_chat.ClaudeCLIChat._run", lambda _self, _prompt: envelope)

    assert ClaudeCLIChat().invoke("PING").content == "PONG"


def test_json_answer_with_result_field_is_not_a_cli_envelope(monkeypatch) -> None:
    class Answer(BaseModel):
        result: str
        confidence: float

    reply = json.dumps({"result": "ready", "confidence": 0.9})
    monkeypatch.setattr(CodexCLIChat, "_run", lambda self, prompt: reply)
    result = CodexCLIChat().with_structured_output(Answer, max_retries=0).invoke("Check")
    assert result == Answer(result="ready", confidence=0.9)


def test_claude_error_envelope_is_not_an_answer(monkeypatch) -> None:
    reply = json.dumps(
        {"type": "result", "is_error": True, "result": "Usage limit reached"}
    )
    monkeypatch.setattr(ClaudeCLIChat, "_run", lambda self, prompt: reply)
    with pytest.raises(RuntimeError, match="Usage limit reached"):
        ClaudeCLIChat().invoke("Check")


def test_nonzero_claude_json_error_uses_sanitized_stdout_detail(monkeypatch) -> None:
    envelope = json.dumps({
        "type": "result",
        "is_error": True,
        "result": "Usage limit reached",
        "prompt": "secret prompt that must not be echoed",
    })
    monkeypatch.setattr("qa_pipeline.cli_chat.shutil.which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        "qa_pipeline.cli_chat.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=envelope, stderr=""),
    )

    with pytest.raises(RuntimeError, match=r"claude exited 1: Usage limit reached") as error:
        ClaudeCLIChat().invoke("secret prompt that must not be echoed")
    assert "secret prompt" not in str(error.value)


def test_structured_output_validates_and_does_not_mutate_input(monkeypatch) -> None:
    replies = iter(['{"action":"click","confidence":0.98}'])
    monkeypatch.setattr(
        "qa_pipeline.cli_chat.ClaudeCLIChat._run",
        lambda _self, _prompt: next(replies),
    )
    messages = [SystemMessage("Ground this step."), HumanMessage("Click Sign in")]

    result = ClaudeCLIChat().with_structured_output(_Answer).invoke(messages)

    assert result == _Answer(action="click", confidence=0.98)
    assert messages[-1].content == "Click Sign in"


def test_structured_output_accepts_plain_string_input(monkeypatch) -> None:
    monkeypatch.setattr(
        "qa_pipeline.cli_chat.CodexCLIChat._run",
        lambda _self, _prompt: '{"action":"click","confidence":0.98}',
    )

    result = CodexCLIChat().with_structured_output(_Answer).invoke("Click Sign in")

    assert result == _Answer(action="click", confidence=0.98)


def test_structured_output_retries_once_after_validation_error(monkeypatch) -> None:
    prompts: list[str] = []
    replies = iter(['{"action":"click"}', '{"action":"click","confidence":0.91}'])

    def fake_run(_self, prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr("qa_pipeline.cli_chat.ClaudeCLIChat._run", fake_run)

    result = ClaudeCLIChat().with_structured_output(_Answer).invoke(
        [HumanMessage("Click Sign in")]
    )

    assert result.confidence == 0.91
    assert len(prompts) == 2
    assert "did not validate" in prompts[1]
