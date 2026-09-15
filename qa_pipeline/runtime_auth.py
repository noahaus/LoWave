from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse


def _cookie_applies(domain: str, hostname: str) -> bool:
    domain = (domain or "").lstrip(".").strip("[]").lower()
    hostname = hostname.strip("[]").lower()
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
        creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0),
    )

    def stop_tree(force: bool = False) -> None:
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            if os.name != "nt":
                os.killpg(proc.pid, sig)
            else:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=2,
                )
        except ProcessLookupError:
            pass
        except (OSError, subprocess.SubprocessError):
            if proc.poll() is None:
                proc.kill() if force else proc.terminate()

    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(request).encode())
        proc.stdin.close()
    except BaseException:
        stop_tree(force=True)
        proc.wait(timeout=2)
        raise RuntimeError("runtime authentication helper setup failed") from None

    chunks: list[bytes] = []
    reader_failure: list[BaseException] = []
    reader_done = threading.Event()

    def read_output() -> None:
        size = 0
        try:
            assert proc.stdout is not None
            while chunk := proc.stdout.read(65536):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("runtime authentication exceeded output limit")
                chunks.append(chunk)
        except BaseException as exc:
            reader_failure.append(exc)
        finally:
            reader_done.set()

    reader = threading.Thread(target=read_output, name="qa-runtime-auth-output", daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout_s
    old_handlers = {}
    def relay(signum, _frame):
        stop_tree()
        raise KeyboardInterrupt(f"runtime authentication interrupted by signal {signum}")
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, relay)
    try:
        while not reader_done.wait(0.05):
            if reader_failure:
                raise reader_failure[0]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("runtime authentication timed out")
            if proc.poll() is not None:
                # A hook descendant may still hold stdout. Kill the helper tree
                # so the reader can observe EOF instead of hanging indefinitely.
                stop_tree(force=True)
        if reader_failure:
            raise reader_failure[0]
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
        if proc.poll() is None:
            stop_tree(force=True)
        reader.join(timeout=2)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
