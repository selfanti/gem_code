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

    async def fake_dispatch(name: str, args: Dict[str, Any], workdir: str) -> str:
        # In the test the only allowed call is `read_file`; it just records.
        captured_calls.append(args.get("path", ""))
        return f"result-for-{args.get('path', '')}"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

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

    async def fake_dispatch(name: str, args: Dict[str, Any], workdir: str) -> str:
        return "ran"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

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

    async def fake_dispatch(name: str, args: Dict[str, Any], workdir: str) -> str:
        invocations.append(args.get("command", ""))
        return "ok"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

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

    async def fake_dispatch(name, args, workdir):
        return "ran"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

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

    async def fake_dispatch(name, args, workdir):
        return "should-not-happen"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

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

    async def fake_dispatch(name, args, workdir):
        return f"ran:{name}"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

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


# -- Round 1: gate chokepoint must live INSIDE run_tool ----------------------


def test_run_tool_invokes_gate_directly(tmp_path: Path) -> None:
    """AC-2 / AC-3.3: any direct caller of `Session.run_tool` MUST flow through
    the permission gate. The gate-call side-effect (calling _check_permission)
    happens before dispatch.
    """
    session = _make_session(tmp_path)

    dispatch_calls: List[str] = []

    async def fake_dispatch(name: str, args: Dict[str, Any], workdir: str) -> str:
        dispatch_calls.append(name)
        return "dispatched"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

    callback_calls: List[str] = []

    async def callback(tool_name, args, ctx):
        callback_calls.append(tool_name)
        return PermissionDecision(
            decision="allow_once",
            reason="user_choice",
            approval_key=ctx["approval_key"],
        )

    # Calling `run_tool` directly (not via `_handle_model_turn`) must still
    # consult the gate.
    result = asyncio.run(
        session.run_tool(
            "bash",
            {"command": "ls"},
            session.workdir,
            request_tool_approval=callback,
        )
    )
    assert result == "dispatched"
    assert callback_calls == ["bash"]
    assert dispatch_calls == ["bash"]


def test_run_tool_denial_raises_permission_denied_sentinel(tmp_path: Path) -> None:
    """AC-4: `run_tool` raises `_PermissionDenied` (caught by callers) when
    the user denies the call. Tests that direct callers see the sentinel
    rather than a substituted plain return string.
    """
    from src.session import _PermissionDenied

    session = _make_session(tmp_path)

    async def fake_dispatch(name, args, workdir):
        return "should_not_reach_here"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

    async def callback(tool_name, args, ctx):
        return PermissionDecision(
            decision="deny",
            reason="user_choice",
            approval_key=ctx["approval_key"],
        )

    with pytest.raises(_PermissionDenied) as caught:
        asyncio.run(
            session.run_tool(
                "bash",
                {"command": "rm -rf /"},
                session.workdir,
                request_tool_approval=callback,
            )
        )
    assert caught.value.decision.decision == "deny"
    assert caught.value.decision.reason == "user_choice"


# -- Round 1: cancellation in the approval path -> deny with reason=shutdown -


def test_callback_cancellation_resolves_to_shutdown_deny(tmp_path: Path) -> None:
    """AC-2: if the approval task is cancelled (TUI shutdown, app teardown,
    parent task cancellation), the gate synthesizes a deny with
    `reason='shutdown'` instead of letting CancelledError bubble out.
    """
    from src.session import _PermissionDenied

    session = _make_session(tmp_path)

    async def fake_dispatch(name, args, workdir):
        return "should_not_reach_here"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

    async def cancelled_callback(tool_name, args, ctx):
        raise asyncio.CancelledError()

    with pytest.raises(_PermissionDenied) as caught:
        asyncio.run(
            session.run_tool(
                "bash",
                {"command": "git status"},
                session.workdir,
                request_tool_approval=cancelled_callback,
            )
        )
    assert caught.value.decision.decision == "deny"
    assert caught.value.decision.reason == "shutdown"


# -- Round 1: multiline bash allow_session is rejected at the gate -----------


def test_allow_session_for_multiline_bash_is_downgraded_at_gate(tmp_path: Path) -> None:
    """AC-3.1: even if the front-end returns `allow_session` for a multiline
    bash command, the gate downgrades to `allow_once` and never persists the
    multiline approval into the session whitelist (DEC-7).
    """
    session = _make_session(tmp_path)

    async def fake_dispatch(name, args, workdir):
        return "ran"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

    async def misbehaving_callback(tool_name, args, ctx):
        # Return `allow_session` for a multiline command — the front-end
        # surfaces should have prevented this, but we test gate's defense
        # in depth.
        return PermissionDecision(
            decision="allow_session",
            reason="user_choice",
            approval_key=ctx["approval_key"],
        )

    asyncio.run(
        session.run_tool(
            "bash",
            {"command": "git status\nls -la"},
            session.workdir,
            request_tool_approval=misbehaving_callback,
        )
    )

    # The session whitelist must NOT contain the multiline command.
    assert session.policy.bash_session_allows == set()
    # And the audit log records the gate's downgrade reason.
    audit = _read_audit_lines(session.transcript_path)
    assert len(audit) == 1
    assert "allow_once" in (audit[0].content or "")
    assert "multiline_bash_session_allow_downgraded" in (audit[0].content or "")


# -- Round 2: gate is always-on in v1 (config field is reserved) -------------


def test_gate_runs_even_when_config_field_says_disabled(tmp_path: Path) -> None:
    """AC-1 / AC-2: `permission_gate_enabled` is RESERVED in v1; even a
    hand-constructed Config with the field set to False must NOT bypass the
    gate. _check_permission ignores the field; load_config() forces it True
    so .env / shell env cannot override.
    """
    from src.session import _PermissionDenied

    session = _make_session(tmp_path)
    # Forcibly flip the field on an already-constructed session — proves
    # _check_permission does not consult it.
    session.config.permission_gate_enabled = False  # type: ignore[misc]

    callback_calls: List[str] = []

    async def callback(tool_name, args, ctx):
        callback_calls.append(tool_name)
        return PermissionDecision(
            decision="allow_once",
            reason="user_choice",
            approval_key=ctx["approval_key"],
        )

    async def fake_dispatch(name, args, workdir):
        return "ran"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

    asyncio.run(
        session.run_tool(
            "bash",
            {"command": "ls"},
            session.workdir,
            request_tool_approval=callback,
        )
    )
    # The callback was still consulted — the field did not bypass the gate.
    assert callback_calls == ["bash"]


def test_gate_runs_even_when_env_says_disabled(monkeypatch, tmp_path) -> None:
    """AC-1 / AC-2: GEM_CODE_PERMISSION_GATE_ENABLED=false in env MUST NOT
    bypass the gate. load_config() ignores the env value and pins the field
    to True.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("GEM_CODE_PERMISSION_GATE_ENABLED", "false")
    monkeypatch.chdir(tmp_path)

    from src.config import load_config

    config = load_config()
    assert config.permission_gate_enabled is True


def test_direct_run_tool_denial_writes_exactly_one_audit_entry(tmp_path: Path) -> None:
    """AC-9: a denied direct `Session.run_tool()` call must emit exactly one
    `[permission] deny ...` JSONL audit entry. The chokepoint owns the audit
    write so direct callers are not silently un-audited.
    """
    from src.session import _PermissionDenied

    session = _make_session(tmp_path)

    async def fake_dispatch(name, args, workdir):
        return "should_not_reach_here"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

    async def deny_callback(tool_name, args, ctx):
        return PermissionDecision(
            decision="deny",
            reason="user_choice",
            approval_key=ctx["approval_key"],
        )

    with pytest.raises(_PermissionDenied) as caught:
        asyncio.run(
            session.run_tool(
                "bash",
                {"command": "rm -rf /"},
                session.workdir,
                request_tool_approval=deny_callback,
            )
        )
    assert caught.value.decision.decision == "deny"
    assert caught.value.audit_emitted is True

    audit = _read_audit_lines(session.transcript_path)
    assert len(audit) == 1
    assert "deny" in (audit[0].content or "")


def test_handle_model_turn_denial_writes_exactly_one_audit_entry(tmp_path: Path) -> None:
    """AC-9 dedupe: when the denial flows through both run_tool and
    _handle_model_turn, the chokepoint emits the audit entry and the outer
    handler skips its emit. Exactly one row, not two.
    """
    session = _make_session(tmp_path)

    async def fake_dispatch(name, args, workdir):
        return "should_not_reach_here"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

    async def callback(tool_name, args, ctx):
        return PermissionDecision(
            decision="deny",
            reason="user_choice",
            approval_key=ctx["approval_key"],
        )

    asyncio.run(session._handle_model_turn(
        content_buffer="",
        reasoning_buffer="",
        has_tool_calls=True,
        tool_calls=[_toolcall("c1", "bash", {"command": "rm -rf /"})],
        user_prompt="x",
        on_turn_end=None,
        on_tool_start=None,
        on_tool_result=None,
        request_tool_approval=callback,
    ))

    audit = _read_audit_lines(session.transcript_path)
    assert len(audit) == 1, f"expected exactly one audit row, got {len(audit)}"
    assert "deny" in (audit[0].content or "")

    # The bound tool reply is still emitted on Session.history (AC-4).
    tool_replies = [m for m in session.history if m.role == "tool"]
    assert len(tool_replies) == 1
    assert tool_replies[0].tool_call_id == "c1"
    assert "<user_denied:" in (tool_replies[0].content or "")


# -- Round 3: gate canonicalizes approval_key + audit_category --------------


def test_callback_supplied_approval_key_is_overridden_by_gate(tmp_path: Path) -> None:
    """AC-3.1 / AC-9: a callback returning a wrong `approval_key` MUST NOT
    persist a different command than the one being approved.

    Codex round-2 review found that a misbehaving callback could approve
    `bash {"command": "git status"}` with `approval_key="git status --short"`
    and the gate would whitelist `git status --short` while leaving the
    actual command unapproved. The gate now rebuilds the decision with the
    gate-computed `approval_key` so callbacks cannot mis-route allow-session
    decisions.
    """
    session = _make_session(tmp_path)

    async def fake_dispatch(name, args, workdir):
        return "ran"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

    async def misbehaving_callback(tool_name, args, ctx):
        # The callback returns a DIFFERENT command than the one being
        # approved. The gate must override `approval_key` with the
        # gate-computed value (`git status`).
        return PermissionDecision(
            decision="allow_session",
            reason="user_choice",
            approval_key="git status --short",
        )

    asyncio.run(
        session.run_tool(
            "bash",
            {"command": "git status"},
            session.workdir,
            request_tool_approval=misbehaving_callback,
        )
    )

    # The gate canonicalized the key — the actual command is now whitelisted.
    assert session.policy.is_whitelisted("bash", {"command": "git status"})
    # The callback's wrong key did NOT make it into the whitelist.
    assert not session.policy.is_whitelisted(
        "bash", {"command": "git status --short"}
    )


def test_callback_wrong_approval_key_is_overridden_in_audit(tmp_path: Path) -> None:
    """AC-9: even when the callback returns a wrong `approval_key`, the
    audit row records the gate-computed (correct) command.
    """
    session = _make_session(tmp_path)

    async def fake_dispatch(name, args, workdir):
        return "ran"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

    async def misbehaving_callback(tool_name, args, ctx):
        return PermissionDecision(
            decision="allow_once",
            reason="user_choice",
            approval_key="rm -rf /",  # bogus / dangerous-looking key
        )

    asyncio.run(
        session.run_tool(
            "bash",
            {"command": "git status"},
            session.workdir,
            request_tool_approval=misbehaving_callback,
        )
    )

    audit = _read_audit_lines(session.transcript_path)
    assert len(audit) == 1
    content = audit[0].content or ""
    assert 'command="git status"' in content
    # The misbehaving callback's bogus key MUST NOT appear in the audit row.
    assert "rm -rf /" not in content


def test_denied_callback_with_wrong_approval_key_audits_actual_command(
    tmp_path: Path,
) -> None:
    """AC-9 dedupe + canonicalization: the deny path also reads the
    gate-computed key, not whatever the callback returned.
    """
    from src.session import _PermissionDenied

    session = _make_session(tmp_path)

    async def fake_dispatch(name, args, workdir):
        return "should_not_reach_here"

    session._dispatch_tool = fake_dispatch  # type: ignore[assignment]

    async def deny_with_wrong_key(tool_name, args, ctx):
        return PermissionDecision(
            decision="deny",
            reason="user_choice",
            approval_key="something_else_entirely",
        )

    with pytest.raises(_PermissionDenied):
        asyncio.run(
            session.run_tool(
                "bash",
                {"command": "git push origin main"},
                session.workdir,
                request_tool_approval=deny_with_wrong_key,
            )
        )

    audit = _read_audit_lines(session.transcript_path)
    assert len(audit) == 1
    content = audit[0].content or ""
    assert 'command="git push origin main"' in content
    assert "something_else_entirely" not in content
