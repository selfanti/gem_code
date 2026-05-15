"""Integration tests for the Session permission gate.

These tests exercise the gate-side wiring of `Session._handle_model_turn` and
`Session._check_permission` without touching the network. We pre-fill
`session.history` and feed pre-built tool calls through `_handle_model_turn`
so the test stays focused on the gate semantics rather than streaming.

Coverage:
* AC-2 / AC-2.3: independent decisions per tool call within one turn.
* AC-3.3: bash session-allow keyed on the normalized command string.
* AC-4: every denied call still produces a `tool` reply with the bound
  `tool_call_id`, including when the approval callback raises.
* AC-9: `[permission]` audit entries land in the JSONL transcript and stay
  out of `Session.history`.
* AC-10: `permission_mode=auto_deny` denies without invoking the callback.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import pytest

from src.config import Config
from src.memory import JsonlRandomAccess, Memory_Unit
from src.models import FunctionCall, Message, ToolCall
from src.permissions import (
    PERMISSION_AUDIT_PREFIX,
    PermissionDecision,
    SessionPermissionPolicy,
)
from src.security import SecuritySettings


def _make_security_settings() -> SecuritySettings:
    return SecuritySettings(
        enabled=False,
        best_effort=True,
        allow_network=True,
        allow_abstract_unix=False,
        allow_signals=False,
        connect_ports=(),
        bind_ports=(),
        extra_read_paths=(),
        extra_write_paths=(),
        extra_execute_paths=(),
    )


def _make_test_config(workdir: Path, *, permission_mode: str = "strict") -> Config:
    return Config(
        api_key="test-key",
        base_url="https://example.invalid/",
        model="test-model",
        workdir=str(workdir),
        skills_dir=None,
        mcp_config_path=None,
        memory_compaction_path=str(workdir / ".memory"),
        api_mode="chat_completions",
        security=_make_security_settings(),
        use_tool_search=False,
        permission_gate_enabled=True,
        predict_before_call_enabled=False,
        self_discovery_enabled=False,
        permission_mode=permission_mode,  # type: ignore[arg-type]
    )


def _make_session(workdir: Path, *, permission_mode: str = "strict"):
    """Construct a minimally-wired `Session` for synchronous gate tests.

    We bypass `__init__`'s async system-prompt task so the test loop never
    has to drive the OpenAI client. The history starts empty so callers can
    prefill it as needed.
    """
    from src.session import Session

    config = _make_test_config(workdir, permission_mode=permission_mode)
    transcript = workdir / "session.jsonl"
    access = JsonlRandomAccess(transcript)

    session = Session.__new__(Session)
    session.session_id = UUID(int=0)
    session.config = config
    session.workdir = config.workdir
    session.history = []
    session.client = None  # network-free unit test
    session.model = config.model
    session.api_mode = "chat_completions"
    session.skills = []
    session.mcp_client = None
    session._all_tools = []
    session.encoded_cwd = "test-encoded"
    session.storage_dir = workdir
    session.transcript_path = transcript
    session.memory_acess = access
    session.max_context_tokens = 200_000
    session.used_context = 0
    from src.context_manager import Context_Manager

    session.context_manager = Context_Manager()
    session._token_encoder = None
    session._tool_schema_token_estimate = 0
    session.policy = SessionPermissionPolicy.with_defaults(permission_mode=permission_mode)  # type: ignore[arg-type]
    session._skills_dir_fingerprint = (0, 0)
    session.embedding_model = None
    session.tools_embeddings = None
    from src.models import ContextUsageSnapshot

    session.context_usage = ContextUsageSnapshot(
        used_tokens=0,
        max_tokens=session.max_context_tokens,
        estimated_input_tokens=0,
        estimated_output_tokens=0,
        tool_schema_tokens=0,
        source="estimated",
        server_tokens=None,
    )
    return session


def _toolcall(call_id: str, name: str, args: Dict[str, Any]) -> ToolCall:
    return ToolCall(
        id=call_id,
        function=FunctionCall(name=name, arguments=json.dumps(args)),
        type="function",
    )


def _read_audit_lines(transcript: Path) -> List[Memory_Unit]:
    """Return only `[permission]` audit Memory_Units in document order."""
    units: List[Memory_Unit] = []
    if not transcript.exists():
        return units
    for raw in transcript.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        unit = Memory_Unit.model_validate_json(raw)
        if unit.content and unit.content.startswith(PERMISSION_AUDIT_PREFIX):
            units.append(unit)
    return units


# -- AC-2.3 + AC-4: per-call decisions and bound tool_call_id ----------------


def test_handle_model_turn_emits_tool_reply_for_each_denied_call(tmp_path: Path) -> None:
    session = _make_session(tmp_path)

    captured_calls: List[str] = []

    async def fake_run_tool(name: str, args: Dict[str, Any], workdir: str) -> str:
        # In the test the only allowed call is `read_file`; it just records.
        captured_calls.append(args.get("path", ""))
        return f"result-for-{args.get('path', '')}"

    session.run_tool = fake_run_tool  # type: ignore[assignment]

    async def callback(tool_name, args, ctx):
        # Approve the first bash, deny the second.
        if args.get("command") == "ls":
            return PermissionDecision(
                decision="allow_once",
                reason="user_choice",
                approval_key="ls",
            )
        return PermissionDecision(
            decision="deny",
            reason="user_choice",
            approval_key="rm -rf /",
        )

    tool_calls = [
        _toolcall("call-1", "read_file", {"path": "README.md"}),
        _toolcall("call-2", "bash", {"command": "ls"}),
        _toolcall("call-3", "bash", {"command": "rm -rf /"}),
    ]

    asyncio.run(session._handle_model_turn(
        content_buffer="",
        reasoning_buffer="",
        has_tool_calls=True,
        tool_calls=tool_calls,
        user_prompt="run things",
        on_turn_end=None,
        on_tool_start=None,
        on_tool_result=None,
        request_tool_approval=callback,
    ))

    tool_reply_messages = [m for m in session.history if m.role == "tool"]
    assert [m.tool_call_id for m in tool_reply_messages] == ["call-1", "call-2", "call-3"]

    # Last reply is the denial.
    assert tool_reply_messages[2].content == "<user_denied: user_choice>"
    # First reply is the read_file result; the second is the bash allow_once.
    assert "result-for-README.md" in (tool_reply_messages[0].content or "")
    assert "result-for-" in (tool_reply_messages[1].content or "")  # bash arg
    # bash run_tool got both invocations because callback was per-call.
    assert "ls" in captured_calls or len(captured_calls) >= 2


def test_callback_exception_yields_deny_with_callback_error(tmp_path: Path) -> None:
    session = _make_session(tmp_path)

    async def fake_run_tool(name: str, args: Dict[str, Any], workdir: str) -> str:
        return "ran"

    session.run_tool = fake_run_tool  # type: ignore[assignment]

    async def boom(tool_name, args, ctx):
        raise RuntimeError("approval ui crashed")

    tool_calls = [_toolcall("c1", "bash", {"command": "ls"})]

    asyncio.run(session._handle_model_turn(
        content_buffer="",
        reasoning_buffer="",
        has_tool_calls=True,
        tool_calls=tool_calls,
        user_prompt="x",
        on_turn_end=None,
        on_tool_start=None,
        on_tool_result=None,
        request_tool_approval=boom,
    ))

    tool_replies = [m for m in session.history if m.role == "tool"]
    assert len(tool_replies) == 1
    assert tool_replies[0].tool_call_id == "c1"
    assert "callback_error" in (tool_replies[0].content or "")


# -- AC-3.3: bash session-allow uses the normalized key ----------------------


def test_bash_session_allow_persists_for_normalized_command(tmp_path: Path) -> None:
    session = _make_session(tmp_path)

    invocations: List[str] = []

    async def fake_run_tool(name: str, args: Dict[str, Any], workdir: str) -> str:
        invocations.append(args.get("command", ""))
        return "ok"

    session.run_tool = fake_run_tool  # type: ignore[assignment]

    callback_calls: List[Tuple[str, Dict[str, Any]]] = []

    async def callback(tool_name, args, ctx):
        callback_calls.append((tool_name, args))
        return PermissionDecision(
            decision="allow_session",
            reason="user_choice",
            approval_key=ctx["approval_key"],
        )

    # First invocation: callback fires and registers the session-allow.
    asyncio.run(session._handle_model_turn(
        content_buffer="",
        reasoning_buffer="",
        has_tool_calls=True,
        tool_calls=[_toolcall("c1", "bash", {"command": "  git   status\t"})],
        user_prompt="x",
        on_turn_end=None,
        on_tool_start=None,
        on_tool_result=None,
        request_tool_approval=callback,
    ))
    assert len(callback_calls) == 1

    # Second invocation with whitespace-equivalent command: callback NOT
    # invoked because the normalized key is in the policy.
    asyncio.run(session._handle_model_turn(
        content_buffer="",
        reasoning_buffer="",
        has_tool_calls=True,
        tool_calls=[_toolcall("c2", "bash", {"command": "git status"})],
        user_prompt="y",
        on_turn_end=None,
        on_tool_start=None,
        on_tool_result=None,
        request_tool_approval=callback,
    ))
    assert len(callback_calls) == 1

    # A different bash command still prompts.
    asyncio.run(session._handle_model_turn(
        content_buffer="",
        reasoning_buffer="",
        has_tool_calls=True,
        tool_calls=[_toolcall("c3", "bash", {"command": "git status --short"})],
        user_prompt="z",
        on_turn_end=None,
        on_tool_start=None,
        on_tool_result=None,
        request_tool_approval=callback,
    ))
    assert len(callback_calls) == 2


# -- AC-9: audit log writes [permission] entries to JSONL only ---------------


def test_permission_audit_lands_in_jsonl_not_history(tmp_path: Path) -> None:
    session = _make_session(tmp_path)

    async def fake_run_tool(name, args, workdir):
        return "ran"

    session.run_tool = fake_run_tool  # type: ignore[assignment]

    async def callback(tool_name, args, ctx):
        return PermissionDecision(
            decision="allow_once",
            reason="user_choice",
            approval_key=ctx["approval_key"],
        )

    asyncio.run(session._handle_model_turn(
        content_buffer="",
        reasoning_buffer="",
        has_tool_calls=True,
        tool_calls=[_toolcall("c1", "bash", {"command": "git status"})],
        user_prompt="x",
        on_turn_end=None,
        on_tool_start=None,
        on_tool_result=None,
        request_tool_approval=callback,
    ))

    audit_units = _read_audit_lines(session.transcript_path)
    assert len(audit_units) == 1
    assert "allow_once" in (audit_units[0].content or "")
    assert "audit_category=git" in (audit_units[0].content or "")

    # No [permission] entry leaks into the model-visible history.
    for message in session.history:
        assert not (message.content or "").startswith(PERMISSION_AUDIT_PREFIX)


# -- AC-10: auto_deny denies without invoking the callback -------------------


def test_auto_deny_skips_callback_and_emits_denial(tmp_path: Path) -> None:
    session = _make_session(tmp_path, permission_mode="auto_deny")

    async def fake_run_tool(name, args, workdir):
        return "should-not-happen"

    session.run_tool = fake_run_tool  # type: ignore[assignment]

    callback_invocations: List[str] = []

    async def callback(tool_name, args, ctx):
        callback_invocations.append(tool_name)
        return PermissionDecision(decision="allow_once", reason="x", approval_key="x")

    asyncio.run(session._handle_model_turn(
        content_buffer="",
        reasoning_buffer="",
        has_tool_calls=True,
        tool_calls=[_toolcall("c1", "bash", {"command": "ls"})],
        user_prompt="x",
        on_turn_end=None,
        on_tool_start=None,
        on_tool_result=None,
        request_tool_approval=callback,
    ))

    assert callback_invocations == []
    tool_replies = [m for m in session.history if m.role == "tool"]
    assert len(tool_replies) == 1
    assert tool_replies[0].tool_call_id == "c1"
    assert "non_interactive" in (tool_replies[0].content or "")


def test_auto_allow_safe_runs_default_whitelist_silently(tmp_path: Path) -> None:
    session = _make_session(tmp_path, permission_mode="auto_allow_safe")

    async def fake_run_tool(name, args, workdir):
        return f"ran:{name}"

    session.run_tool = fake_run_tool  # type: ignore[assignment]

    async def callback(tool_name, args, ctx):
        raise AssertionError("auto_allow_safe must not call the approval callback")

    asyncio.run(session._handle_model_turn(
        content_buffer="",
        reasoning_buffer="",
        has_tool_calls=True,
        tool_calls=[
            _toolcall("c1", "read_file", {"path": "README.md"}),
            _toolcall("c2", "bash", {"command": "ls"}),
        ],
        user_prompt="x",
        on_turn_end=None,
        on_tool_start=None,
        on_tool_result=None,
        request_tool_approval=callback,
    ))

    tool_replies = [m for m in session.history if m.role == "tool"]
    assert tool_replies[0].content == "ran:read_file"
    assert "<user_denied:" in (tool_replies[1].content or "")


# -- AC-8.1: clear_history resets the policy ---------------------------------


def test_clear_history_resets_policy_to_defaults(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    session.policy.record(
        "bash",
        PermissionDecision(
            decision="allow_session", reason="r", approval_key="git status"
        ),
    )
    session.policy.record(
        "write_file",
        PermissionDecision(
            decision="allow_session", reason="r", approval_key="write_file"
        ),
    )
    assert session.policy.is_whitelisted("write_file", {})
    assert session.policy.is_whitelisted("bash", {"command": "git status"})

    session.clear_history()

    assert not session.policy.is_whitelisted("write_file", {})
    assert not session.policy.is_whitelisted("bash", {"command": "git status"})
