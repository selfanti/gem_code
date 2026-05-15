"""Tests for the CLI approval callback (AC-2.2, AC-3.1, AC-10)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from src.cli import make_cli_approval_callback
from src.config import Config
from src.permissions import PermissionDecision
from src.security import SecuritySettings


def _config(tmp_path) -> Config:
    return Config(
        api_key="k",
        base_url="https://example.invalid/",
        model="m",
        workdir=str(tmp_path),
        skills_dir=None,
        mcp_config_path=None,
        memory_compaction_path=str(tmp_path / ".memory"),
        api_mode="chat_completions",
        security=SecuritySettings(
            enabled=False,
            best_effort=True,
            allow_network=False,
            allow_abstract_unix=False,
            allow_signals=False,
            connect_ports=(),
            bind_ports=(),
            extra_read_paths=(),
            extra_write_paths=(),
            extra_execute_paths=(),
        ),
        use_tool_search=False,
    )


def _ctx(tool_name: str, *, multiline: bool = False) -> Dict[str, Any]:
    return {
        "tool_name": tool_name,
        "args": {},
        "approval_key": tool_name,
        "audit_category": None,
        "is_multiline_bash": multiline,
        "workdir": "/tmp",
    }


def _run(coro):
    return asyncio.run(coro)


def test_cli_callback_accepts_a_for_allow_once(tmp_path) -> None:
    callback = make_cli_approval_callback(_config(tmp_path))
    with patch("src.cli.async_input", side_effect=[lambda *a, **k: "a"]):
        pass
    with patch("src.cli.async_input", new=_async_input("a")):
        decision = _run(callback("bash", {"command": "ls"}, _ctx("bash")))
    assert decision is not None
    assert decision.decision == "allow_once"


def test_cli_callback_accepts_s_for_allow_session(tmp_path) -> None:
    callback = make_cli_approval_callback(_config(tmp_path))
    with patch("src.cli.async_input", new=_async_input("s")):
        decision = _run(callback("bash", {"command": "ls"}, _ctx("bash")))
    assert decision is not None
    assert decision.decision == "allow_session"


def test_cli_callback_accepts_d_for_deny(tmp_path) -> None:
    callback = make_cli_approval_callback(_config(tmp_path))
    with patch("src.cli.async_input", new=_async_input("d")):
        decision = _run(callback("bash", {"command": "ls"}, _ctx("bash")))
    assert decision is not None
    assert decision.decision == "deny"


def test_cli_callback_eof_resolves_to_deny(tmp_path) -> None:
    callback = make_cli_approval_callback(_config(tmp_path))

    async def raise_eof(*args, **kwargs):
        raise EOFError()

    with patch("src.cli.async_input", new=raise_eof):
        decision = _run(callback("bash", {"command": "ls"}, _ctx("bash")))
    assert decision is not None
    assert decision.decision == "deny"
    assert decision.reason == "cli_interrupt"


def test_cli_callback_keyboard_interrupt_resolves_to_deny(tmp_path) -> None:
    callback = make_cli_approval_callback(_config(tmp_path))

    async def raise_kbd(*args, **kwargs):
        raise KeyboardInterrupt()

    with patch("src.cli.async_input", new=raise_kbd):
        decision = _run(callback("bash", {"command": "ls"}, _ctx("bash")))
    assert decision is not None
    assert decision.decision == "deny"
    assert decision.reason == "cli_interrupt"


def test_cli_callback_multiline_disables_session_allow(tmp_path) -> None:
    callback = make_cli_approval_callback(_config(tmp_path))

    # `s` (allow_session) is rejected for multiline commands.
    with patch("src.cli.async_input", new=_async_input("s")):
        decision = _run(callback("bash", {"command": "git status\nls"}, _ctx("bash", multiline=True)))
    assert decision is not None
    assert decision.decision == "deny"
    assert decision.reason == "unrecognized_cli_choice"


def test_cli_callback_unrecognized_input_falls_to_deny(tmp_path) -> None:
    callback = make_cli_approval_callback(_config(tmp_path))
    with patch("src.cli.async_input", new=_async_input("maybe")):
        decision = _run(callback("bash", {"command": "ls"}, _ctx("bash")))
    assert decision is not None
    assert decision.decision == "deny"


def _async_input(reply: str):
    """Build a stub async_input that returns the configured reply once."""

    async def _stub(prompt: str = "") -> str:
        return reply

    return _stub
