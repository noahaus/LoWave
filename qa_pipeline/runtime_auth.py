from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse


def _cookie_applies(domain: str, hostname: str) -> bool:
    domain = (domain or "").lstrip(".").lower()
    hostname = hostname.lower()
    return bool(domain) and (hostname == domain or hostname.endswith("." + domain))


def canonical_origin(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid bound origin")
    if not parsed.hostname.isascii():
        raise ValueError("runtime authentication hostname must be ASCII or punycode")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port in ({"http": 80, "https": 443}[parsed.scheme], None):
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{port}"


def validate_storage_state(state: dict, base_url: str) -> dict:
    bound = urlparse(base_url)
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins"), list):
        raise ValueError("invalid authentication state")
    expected = canonical_origin(base_url)
    for item in state["origins"]:
        if canonical_origin(item.get("origin", "")) != expected:
            raise ValueError("cross-origin authentication state rejected")
    for cookie in state["cookies"]:
        if not _cookie_applies(cookie.get("domain", ""), bound.hostname or ""):
            raise ValueError("cookie domain is not applicable to the bound origin")
    return state


def decode_storage_state(raw: bytes, base_url: str) -> dict:
    try:
        return validate_storage_state(json.loads(raw), base_url)
    except Exception as exc:
        raise ValueError("invalid authentication state from runtime hook") from exc


def acquire_storage_state(hook_path: str, base_url: str, *, timeout_s: float = 30, max_bytes: int = 1024 * 1024) -> dict:
    hook = Path(hook_path).resolve(strict=True)
    runner = Path(__file__).resolve().parent.parent / "runtime" / "auth-hook-runner.cjs"
    request = {"version": 1, "baseURL": base_url, "origin": canonical_origin(base_url)}
    proc = subprocess.Popen(
        ["node", str(runner), str(hook)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        # A dedicated group permits bounded TERM/KILL cleanup of hook children.
        start_new_session=os.name != "nt",
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(request).encode())
        proc.stdin.close()
        selected = selectors.DefaultSelector()
        selected.register(proc.stdout, selectors.EVENT_READ)
    except BaseException:
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)
        raise RuntimeError("runtime authentication helper setup failed") from None
    chunks: list[bytes] = []
    size = 0
    deadline = time.monotonic() + timeout_s
    old_handlers = {}
    def stop_tree(force: bool = False) -> None:
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            if os.name != "nt":
                os.killpg(proc.pid, sig)
            elif proc.poll() is None:
                proc.kill() if force else proc.terminate()
        except ProcessLookupError:
            pass
    def relay(signum, _frame):
        stop_tree()
        raise KeyboardInterrupt(f"runtime authentication interrupted by signal {signum}")
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, relay)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("runtime authentication timed out")
            events = selected.select(min(remaining, 0.25))
            if events:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("runtime authentication exceeded output limit")
                chunks.append(chunk)
            elif proc.poll() is not None:
                break
        if proc.wait(timeout=1) != 0:
            raise RuntimeError("runtime authentication hook failed")
        state = decode_storage_state(b"".join(chunks), base_url)
        stop_tree(force=True)
        return state
    except BaseException:
        stop_tree()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        stop_tree(force=True)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        selected.close()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
