"""Unit tests for src.permissions.

Covers AC-1 (default whitelist), AC-3.1 (bash normalization + audit category +
multiline detection), AC-3.2 (MCP tool full-name granularity), AC-8.1 (in-memory
policy reset), and AC-9 (audit content rendering).
"""

from __future__ import annotations

import pytest

from src.permissions import (
    DEFAULT_WHITELIST_NAMES,
    DEFAULT_WHITELIST_PREFIXES,
    PERMISSION_AUDIT_PREFIX,
    PermissionDecision,
    SessionPermissionPolicy,
    categorize_bash_command,
    deny,
    is_audit_content,
    make_audit_content,
    normalize_bash_command,
    policy_mode_from_env,
)


# -- AC-1: default whitelist composition --------------------------------------


def test_default_policy_only_admits_read_only_tools_silently() -> None:
    policy = SessionPermissionPolicy.with_defaults()

    assert policy.is_whitelisted("read_file", {})
    assert policy.is_whitelisted("Glob", {})
    assert policy.is_whitelisted("Grep", {})
    assert policy.is_whitelisted("skill__python-best-practices", {})

    assert not policy.is_whitelisted("bash", {"command": "ls"})
    assert not policy.is_whitelisted("write_file", {})
    assert not policy.is_whitelisted("StrReplaceFile", {})
    assert not policy.is_whitelisted("fetch_url", {})
    assert not policy.is_whitelisted("mcp__filesystem__read_file", {})


def test_default_whitelist_constants_are_exactly_three_names_one_prefix() -> None:
    assert DEFAULT_WHITELIST_NAMES == frozenset({"read_file", "Glob", "Grep"})
    assert DEFAULT_WHITELIST_PREFIXES == ("skill__",)


# -- AC-3.1: bash normalization and audit category ----------------------------


def test_bash_normalization_collapses_top_level_whitespace() -> None:
    assert normalize_bash_command("  git   status\t") == ("git status", False)
    assert normalize_bash_command("git\t\tstatus") == ("git status", False)
    assert normalize_bash_command("git status\n") == ("git status", False)


def test_bash_normalization_preserves_quoted_whitespace() -> None:
    assert normalize_bash_command("git commit -m 'fix:  bar baz'") == (
        "git commit -m 'fix:  bar baz'",
        False,
    )
    assert normalize_bash_command('echo "a   b"') == ('echo "a   b"', False)


def test_bash_normalization_preserves_escaped_whitespace() -> None:
    normalized, multiline = normalize_bash_command(r"ls foo\ bar")
    assert normalized == r"ls foo\ bar"
    assert multiline is False


def test_bash_normalization_preserves_heredoc_body() -> None:
    cmd = "cat <<EOF\nhello world\nEOF\n"
    normalized, multiline = normalize_bash_command(cmd)
    assert "hello world" in normalized
    assert multiline is True


def test_bash_normalization_preserves_command_separator_newlines() -> None:
    normalized, multiline = normalize_bash_command("git status\nls -la")
    assert "\n" in normalized
    assert multiline is True


def test_categorize_bash_recognizes_simple_commands() -> None:
    assert categorize_bash_command("git status") == "git"
    assert categorize_bash_command("ls -la") == "ls"
    assert categorize_bash_command("echo hi") == "builtin"
    assert categorize_bash_command("pwd") == "builtin"


def test_categorize_bash_recognizes_env_prefix_and_cd_chain() -> None:
    assert categorize_bash_command("VAR=1 git status") == "git"
    assert categorize_bash_command("FOO=bar BAZ=qux git log") == "git"
    assert categorize_bash_command("cd /tmp && git log") == "git"
    assert categorize_bash_command("cd /tmp && VAR=1 git log") == "git"


def test_categorize_bash_normalizes_wrappers() -> None:
    assert categorize_bash_command("uv run pytest") == "pytest"
    assert categorize_bash_command("python -m pytest tests/") == "pytest"
    assert categorize_bash_command("python3 -m pytest") == "pytest"
    assert categorize_bash_command("npx tsc --noEmit") == "tsc"


def test_categorize_bash_returns_unknown_for_control_operators() -> None:
    assert categorize_bash_command("git status | grep foo") == "unknown"
    assert categorize_bash_command("git status; ls") == "unknown"
    assert categorize_bash_command("git status && rm -rf x") == "unknown"
    assert categorize_bash_command("cd /tmp && git status && rm -rf x") == "unknown"
    assert categorize_bash_command("git status || echo done") == "unknown"
    assert categorize_bash_command("sleep 5 &") == "unknown"


def test_categorize_bash_returns_unknown_for_subshells_and_substitution() -> None:
    assert categorize_bash_command("(git status)") == "unknown"
    assert categorize_bash_command("git $(date)") == "unknown"
    assert categorize_bash_command("git `date`") == "unknown"
    assert categorize_bash_command("cat <<EOF\nbody\nEOF") == "unknown"


def test_categorize_bash_returns_unknown_for_bash_dash_c() -> None:
    # `bash -c "..."` is a wrapper around an arbitrary inner command, so we
    # cannot bucket the outer call safely. AUDIT category for the outer is
    # `bash` (not `unknown`) because the OUTER command is `bash`. Defense in
    # depth: the user still sees the full command text in the modal.
    assert categorize_bash_command("bash -c 'rm -rf /'") == "bash"


def test_categorize_bash_handles_empty_or_whitespace() -> None:
    assert categorize_bash_command("") == "unknown"
    assert categorize_bash_command("   ") == "unknown"


# -- AC-3.2: MCP and non-bash granularity is by exact tool name ---------------


def test_session_allow_on_mcp_tool_is_exact_name() -> None:
    policy = SessionPermissionPolicy.with_defaults()

    decision = PermissionDecision(
        decision="allow_session",
        reason="user_choice",
        approval_key="mcp__filesystem__read_file",
    )
    policy.record("mcp__filesystem__read_file", decision)

    assert policy.is_whitelisted("mcp__filesystem__read_file", {})
    assert not policy.is_whitelisted("mcp__filesystem__write_file", {})
    assert not policy.is_whitelisted("mcp__playwright__navigate", {})


def test_session_allow_on_bash_uses_normalized_command_string() -> None:
    policy = SessionPermissionPolicy.with_defaults()

    decision = PermissionDecision(
        decision="allow_session",
        reason="user_choice",
        approval_key="git status",
    )
    policy.record("bash", decision)

    assert policy.is_whitelisted("bash", {"command": "  git   status\t"})
    assert policy.is_whitelisted("bash", {"command": "git status"})
    # Different commands still prompt.
    assert not policy.is_whitelisted("bash", {"command": "git status --short"})
    assert not policy.is_whitelisted("bash", {"command": "git log"})


def test_record_skips_multiline_bash_session_allow() -> None:
    """Defense in depth: a multiline allow_session must NEVER persist."""
    policy = SessionPermissionPolicy.with_defaults()

    decision = PermissionDecision(
        decision="allow_session",
        reason="should_not_persist",
        approval_key="git status\nls",
    )
    policy.record("bash", decision)

    assert policy.bash_session_allows == set()


def test_record_ignores_allow_once_and_deny() -> None:
    policy = SessionPermissionPolicy.with_defaults()
    snapshot = (set(policy.whitelist_names), set(policy.bash_session_allows))

    policy.record("bash", PermissionDecision(decision="allow_once", reason="r", approval_key="git status"))
    policy.record("write_file", PermissionDecision(decision="deny", reason="r", approval_key="write_file"))

    assert (set(policy.whitelist_names), set(policy.bash_session_allows)) == snapshot


# -- auto_allow_safe behavior (AC-10) -----------------------------------------


def test_auto_allow_safe_only_allows_default_whitelist() -> None:
    policy = SessionPermissionPolicy.with_defaults(permission_mode="auto_allow_safe")

    assert policy.is_whitelisted("read_file", {})
    assert policy.is_whitelisted("Glob", {})
    assert policy.is_whitelisted("skill__anything", {})

    assert not policy.is_whitelisted("bash", {"command": "ls"})
    assert not policy.is_whitelisted("write_file", {})
    assert not policy.is_whitelisted("mcp__filesystem__read_file", {})


# -- AC-8.1: reset_to_defaults clears state -----------------------------------


def test_reset_to_defaults_clears_session_overrides() -> None:
    policy = SessionPermissionPolicy.with_defaults()
    policy.record(
        "bash",
        PermissionDecision(decision="allow_session", reason="r", approval_key="git status"),
    )
    policy.record(
        "write_file",
        PermissionDecision(decision="allow_session", reason="r", approval_key="write_file"),
    )
    assert policy.is_whitelisted("write_file", {})
    assert policy.is_whitelisted("bash", {"command": "git status"})

    policy.reset_to_defaults()

    assert policy.bash_session_allows == set()
    assert policy.whitelist_names == set(DEFAULT_WHITELIST_NAMES)
    assert not policy.is_whitelisted("write_file", {})


# -- AC-9: audit-log content format -------------------------------------------


def test_audit_content_format_for_bash_includes_command_and_category() -> None:
    decision = PermissionDecision(
        decision="allow_once",
        reason="user_choice",
        approval_key="git status",
        audit_category="git",
    )
    line = make_audit_content(decision, "bash")
    assert line.startswith(PERMISSION_AUDIT_PREFIX)
    assert "allow_once" in line
    assert "bash" in line
    assert 'command="git status"' in line
    assert 'reason="user_choice"' in line
    assert "audit_category=git" in line


def test_audit_content_format_for_non_bash_omits_command() -> None:
    decision = PermissionDecision(
        decision="deny",
        reason="callback_error: Boom",
        approval_key="write_file",
    )
    line = make_audit_content(decision, "write_file")
    assert "command=" not in line
    assert 'reason="callback_error: Boom"' in line


def test_audit_content_escapes_quotes_and_newlines() -> None:
    """AC-9: audit lines round-trip through `json.loads` for command/reason fields.

    Codex round-0 review flagged that quoted bash commands or multiline
    payloads broke the audit JSONL line because the values were embedded
    raw. JSON-encoding the fields preserves them faithfully.
    """
    decision = PermissionDecision(
        decision="allow_once",
        reason='quote " inside',
        approval_key='echo "hi"\nls -la',
        audit_category="echo",
    )
    line = make_audit_content(decision, "bash")
    # The line should still parse: extract the value between `command=` and
    # the next ` reason=` segment and decode it.
    assert "command=" in line
    cmd_chunk = line.split("command=", 1)[1].split(" reason=", 1)[0]
    import json as _json
    assert _json.loads(cmd_chunk) == 'echo "hi"\nls -la'
    reason_chunk = line.split("reason=", 1)[1].split(" audit_category=", 1)[0]
    assert _json.loads(reason_chunk) == 'quote " inside'


def test_is_audit_content_recognizes_prefix() -> None:
    assert is_audit_content("[permission] allow_once read_file reason=user_choice")
    assert is_audit_content("   [permission] deny bash reason=cli_interrupt")
    assert not is_audit_content("hello")
    assert not is_audit_content("")
    assert not is_audit_content(None)


# -- policy_mode_from_env ------------------------------------------------------


def test_policy_mode_from_env_recognizes_valid_values(monkeypatch) -> None:
    monkeypatch.setenv("GEM_CODE_PERMISSION_MODE", "auto_deny")
    assert policy_mode_from_env() == "auto_deny"

    monkeypatch.setenv("GEM_CODE_PERMISSION_MODE", "AUTO_ALLOW_SAFE")
    assert policy_mode_from_env() == "auto_allow_safe"

    monkeypatch.setenv("GEM_CODE_PERMISSION_MODE", "  strict  ")
    assert policy_mode_from_env() == "strict"


def test_policy_mode_from_env_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.delenv("GEM_CODE_PERMISSION_MODE", raising=False)
    assert policy_mode_from_env() == "strict"
    assert policy_mode_from_env(default="auto_deny") == "auto_deny"

    monkeypatch.setenv("GEM_CODE_PERMISSION_MODE", "garbage")
    assert policy_mode_from_env() == "strict"


# -- deny() helper -------------------------------------------------------------


def test_deny_helper_produces_deny_decision() -> None:
    d = deny("non_interactive", approval_key="bash")
    assert d.decision == "deny"
    assert d.reason == "non_interactive"
    assert d.approval_key == "bash"
    assert d.visible_summary is None


def test_categorize_bash_strips_top_level_comment() -> None:
    assert categorize_bash_command("git status  # check status") == "git"


def test_categorize_bash_classifies_source_as_unknown() -> None:
    """Sourced scripts execute arbitrary shell — defense in depth → unknown."""
    assert categorize_bash_command("source venv/bin/activate") == "unknown"
    assert categorize_bash_command(". ./activate") == "unknown"


def test_categorize_bash_classifies_command_builtin_as_unknown() -> None:
    """`command` has option-sensitive behavior; do not unwrap to its argument."""
    assert categorize_bash_command("command git status") == "unknown"
    assert categorize_bash_command("command -v git") == "unknown"


def test_categorize_bash_classifies_redirection_only_as_unknown() -> None:
    assert categorize_bash_command("> file.txt") == "unknown"
    assert categorize_bash_command(">> file.txt") == "unknown"


def test_categorize_bash_classifies_dollar_expansion_head_as_unknown() -> None:
    assert categorize_bash_command("${VAR}=value cmd") == "unknown"
    assert categorize_bash_command("$CMD foo") == "unknown"
