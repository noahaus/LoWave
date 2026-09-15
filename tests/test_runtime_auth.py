import pytest
import json
import os
import signal
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from qa_pipeline.runtime_auth import acquire_storage_state, canonical_origin, validate_storage_state
from qa_pipeline.refine_plan import refine, redact_authenticated_dom
from qa_pipeline import config
from qa_pipeline import parse_steps
import asyncio
from qa_pipeline.generate import generate


@pytest.fixture
def local_origin():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        def log_message(self, *_args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


def test_python_rejects_cross_origin_state():
    with pytest.raises(ValueError, match="cross-origin"):
        validate_storage_state(
            {"cookies": [], "origins": [{"origin": "https://evil.test", "localStorage": []}]},
            "https://app.test/path",
        )


def test_python_rejects_inapplicable_cookie_domain():
    with pytest.raises(ValueError, match="cookie domain"):
        validate_storage_state(
            {"cookies": [{"name": "sid", "value": "LEAK_SENTINEL", "domain": "evil.test", "path": "/"}], "origins": []},
            "https://app.test",
        )


def test_canonical_origin_normalizes_case_default_port_and_idna():
    assert canonical_origin("HTTPS://EXAMPLE.COM:443/path") == "https://example.com"
    assert canonical_origin("http://[::1]:80/path") == "http://[::1]"
    with pytest.raises(ValueError, match="ASCII"):
        canonical_origin("https://bücher.example")
    with pytest.raises(ValueError, match="invalid bound origin"):
        canonical_origin("https://user:pass@app.test")


def test_refine_rejects_auth_origin_override_before_model_or_browser():
    plan = {"workflow": {"base_url": "https://app.test"}, "steps": []}
    with pytest.raises(ValueError, match="must match"):
        asyncio.run(refine(plan, "https://other.test", "ollama", None, True, 0, .5, "/hook.cjs"))


def test_refine_runtime_auth_rejects_userinfo_before_logging(tmp_path, capsys):
    hook = tmp_path / "hook.cjs"
    hook.write_text("exports.authenticate=async()=>{};")
    plan = {
        "workflow": {"base_url": "https://alice:LEAK_SENTINEL@app.test"},
        "steps": [],
    }
    with pytest.raises(ValueError, match="invalid bound origin"):
        asyncio.run(refine(plan, None, "ollama", None, True, 0, .5, str(hook)))
    captured = capsys.readouterr()
    assert "LEAK_SENTINEL" not in captured.out
    assert "LEAK_SENTINEL" not in captured.err


def test_refine_authenticated_preflight_checks_hook_path_without_model_or_browser(tmp_path):
    plan = {"workflow": {"base_url": "https://app.test"}, "steps": []}
    missing = tmp_path / "missing-hook.cjs"
    with pytest.raises(FileNotFoundError):
        asyncio.run(refine(plan, "https://app.test/path", "ollama", None, True, 0, .5, str(missing)))


def test_parse_runtime_auth_blocks_environment_credentials_from_prompt_and_plan(tmp_path, monkeypatch):
    steps = tmp_path / "steps.txt"
    output = tmp_path / "plan.json"
    steps.write_text("1. Open the app")
    monkeypatch.setenv("QA_AUTH_HOOK", "/trusted/hook.cjs")
    monkeypatch.setenv("QA_USERNAME", "LEAK_SENTINEL_USER")
    monkeypatch.setenv("QA_PASSWORD", "LEAK_SENTINEL_PASSWORD")
    captured = {}

    class FakeResponse:
        content = json.dumps({"workflow": {"title": "safe", "base_url": "https://app.test"}, "steps": [], "metadata": {}})

    class FakeLLM:
        def invoke(self, messages):
            captured["messages"] = messages
            return FakeResponse()

    monkeypatch.setattr(parse_steps, "build_llm", lambda *_args, **_kwargs: FakeLLM())
    monkeypatch.setattr(sys, "argv", ["qa-parse", str(steps), str(output), "--base-url", "https://app.test", "--runtime-auth"])
    parse_steps.main()
    prompt = "\n".join(str(message.content) for message in captured["messages"])
    assert "LEAK_SENTINEL" not in prompt
    assert "LEAK_SENTINEL" not in output.read_text()


def test_parse_runtime_auth_rejects_userinfo_url_before_print_or_model(tmp_path, monkeypatch, capsys):
    steps = tmp_path / "steps.txt"
    output = tmp_path / "plan.json"
    steps.write_text("1. Open the app")
    monkeypatch.setenv("QA_BASE_URL", "https://alice:LEAK_SENTINEL@app.test")
    monkeypatch.setattr(
        parse_steps,
        "build_llm",
        lambda *_args, **_kwargs: pytest.fail("model must not be called"),
    )
    monkeypatch.setattr(sys, "argv", ["qa-parse", str(steps), str(output), "--runtime-auth"])
    with pytest.raises(SystemExit):
        parse_steps.main()
    assert "LEAK_SENTINEL" not in capsys.readouterr().out
    assert not output.exists()


def test_ambient_auth_hook_does_not_override_explicit_legacy_credentials(monkeypatch):
    monkeypatch.setenv("QA_AUTH_HOOK", "/trusted/hook.cjs")
    assert config.username("demo@example.test") == "demo@example.test"
    assert config.password("test1234") == "test1234"


def test_generator_uses_runtime_fixture_only_when_explicit(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"workflow": {"title": "auth", "base_url": "https://app.test"}, "steps": []}))
    plain = tmp_path / "plain.spec.ts"
    auth = tmp_path / "auth.spec.ts"
    monkeypatch.setenv("QA_AUTH_HOOK", "/ambient/must-not-enable.cjs")
    generate(plan, plain)
    generate(plan, auth, runtime_auth=True)
    assert plain.read_text().startswith("import { test, expect } from '@playwright/test';")
    assert "runtime/auth-fixture" not in plain.read_text()
    assert "runtime/auth-fixture" in auth.read_text().splitlines()[0]
    assert "test.use({ baseURL: 'https://app.test' });" in auth.read_text()
    assert "/trusted/hook.cjs" not in auth.read_text()


def test_generator_fails_before_writing_when_runtime_fixture_is_missing(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    out = tmp_path / "auth.spec.ts"
    plan.write_text(json.dumps({"workflow": {"title": "auth", "base_url": "https://app.test"}, "steps": []}))
    original_exists = type(plan).exists

    def selective_exists(path):
        if path.name in {"auth-fixture", "auth-fixture.ts"}:
            return False
        return original_exists(path)

    monkeypatch.setattr(type(plan), "exists", selective_exists)
    with pytest.raises(RuntimeError, match="runtime authentication fixture"):
        generate(plan, out, runtime_auth=True)
    assert not out.exists()


def test_generator_runtime_auth_rejects_userinfo_url_before_print_or_write(tmp_path, capsys):
    plan = tmp_path / "plan.json"
    out = tmp_path / "auth.spec.ts"
    plan.write_text(json.dumps({
        "workflow": {"title": "auth", "base_url": "https://alice:LEAK_SENTINEL@app.test"},
        "steps": [],
    }))
    with pytest.raises(ValueError, match="invalid bound origin"):
        generate(plan, out, runtime_auth=True)
    assert "LEAK_SENTINEL" not in capsys.readouterr().out
    assert not out.exists()


def test_authenticated_dom_redacts_values_and_storage_secrets_before_logs_or_model():
    elements = [
        {"index": 0, "tag": "input", "type": "text", "text": "typed-secret", "label": "Search"},
        {"index": 1, "tag": "button", "role": "button", "title": "signed-in@example.test", "text": "Profile"},
        {"index": 2, "tag": "button", "role": "button", "text": "Use token COOKIE_SENTINEL now"},
    ]
    state = {
        "cookies": [{"name": "sid", "value": "COOKIE_SENTINEL", "domain": "app.test", "path": "/"}],
        "origins": [],
    }
    redacted = redact_authenticated_dom(elements, state)
    serialized = json.dumps(redacted)
    assert "typed-secret" not in serialized
    assert "signed-in@example.test" not in serialized
    assert "COOKIE_SENTINEL" not in serialized
    assert redacted[0]["label"] == "Search"
    assert redacted[0]["index"] == 0


def test_python_bridge_returns_memory_only_state_for_protected_origin(tmp_path, monkeypatch, local_origin):
    secret = "LEAK_SENTINEL_PYTHON_BRIDGE"
    hook = tmp_path / "hook.cjs"
    hook.write_text("module.exports.authenticate = async ({page,baseURL}) => { await page.context().addCookies([{name:'sid',value:process.env.RUNTIME_TEST_SECRET,url:baseURL}]); await page.goto(baseURL); };")
    monkeypatch.setenv("RUNTIME_TEST_SECRET", secret)
    state = acquire_storage_state(str(hook), local_origin)
    assert state["cookies"][0]["value"] == secret
    assert not list(tmp_path.glob("*state*"))


def test_python_bridge_timeout_kills_and_reaps(tmp_path, local_origin):
    hook = tmp_path / "hook.cjs"
    hook.write_text("module.exports.authenticate = async () => new Promise(() => {});")
    with pytest.raises(TimeoutError, match="timed out"):
        acquire_storage_state(str(hook), local_origin, timeout_s=0.5)


def test_python_bridge_limits_real_helper_output(tmp_path, local_origin):
    hook = tmp_path / "hook.cjs"
    hook.write_text("module.exports.authenticate = async ({page,baseURL}) => page.goto(baseURL);")
    with pytest.raises(ValueError, match="output limit"):
        acquire_storage_state(str(hook), local_origin, max_bytes=8)


def test_python_bridge_sanitizes_hook_failure(tmp_path, local_origin):
    hook = tmp_path / "hook.cjs"
    hook.write_text("module.exports.authenticate = async () => { throw new Error('LEAK_SENTINEL_ERROR'); };")
    with pytest.raises(RuntimeError) as caught:
        acquire_storage_state(str(hook), local_origin)
    assert "LEAK_SENTINEL_ERROR" not in str(caught.value)


def test_python_bridge_rejects_hook_ending_on_wrong_origin(tmp_path, local_origin):
    hook = tmp_path / "hook.cjs"
    hook.write_text("module.exports.authenticate = async ({page}) => page.goto('about:blank');")
    with pytest.raises(RuntimeError, match="hook failed"):
        acquire_storage_state(str(hook), local_origin)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group lifecycle")
def test_python_bridge_kills_descendant_after_helper_exits(tmp_path, local_origin):
    pid_file = tmp_path / "child.pid"
    hook = tmp_path / "hook.cjs"
    hook.write_text(
        "const {spawn}=require('node:child_process');const fs=require('node:fs');"
        f"module.exports.authenticate=async()=>{{const c=spawn('sh',['-c',\"trap '' TERM; sleep 60\"],{{stdio:'ignore'}});fs.writeFileSync({json.dumps(str(pid_file))},String(c.pid));throw new Error('stop');}};"
    )
    with pytest.raises(RuntimeError):
        acquire_storage_state(str(hook), local_origin)
    pid = int(pid_file.read_text())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(.05)
    else:
        os.kill(pid, signal.SIGKILL)
        pytest.fail("authentication hook descendant survived cleanup")
