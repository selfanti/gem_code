"""Per-session tool permission policy.

This module is the single source of truth for the gating semantics described in
the v1 plan. It intentionally has no I/O, no Textual dependency, and no Session
import so it can be unit-tested in isolation.

Three pieces live here:

* `PermissionDecision` is the structured result returned by the approval
  callback (or synthesized for non-interactive modes / failures).
* `SessionPermissionPolicy` owns the in-memory whitelist for a single session
  and decides whether a given tool invocation needs human approval.
* `normalize_bash_command` and `categorize_bash_command` produce the approval
  key and the audit-only category metadata for `bash` invocations.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Literal, Optional, Set


PermissionVerdict = Literal["allow_once", "allow_session", "deny"]
PermissionMode = Literal["strict", "auto_deny", "auto_allow_safe"]


PERMISSION_AUDIT_PREFIX = "[permission]"
"""Audit-log prefix used for every `Memory_Unit` written by the gate.

Anything that needs to filter permission entries out of model-visible context
must use this exact prefix. It lives here as a constant so the rehydration
filter and the gate cannot drift apart.
"""


DEFAULT_WHITELIST_NAMES: frozenset[str] = frozenset({"read_file", "Glob", "Grep"})
# Harbor benchmark override: uncomment this line and comment the line above to
# allow every built-in tool without interactive approval during non-interactive
# evaluation runs.
# DEFAULT_WHITELIST_NAMES: frozenset[str] = frozenset({
#     "bash",
#     "read_file",
#     "write_file",
#     "StrReplaceFile",
#     "fetch_url",
#     "Glob",
#     "Grep",
# })
"""Initial AC-1 whitelist for fresh sessions.

Read-only tools only. The `skill__*` prefix is allowed via
`DEFAULT_WHITELIST_PREFIXES`.
"""


DEFAULT_WHITELIST_PREFIXES: tuple[str, ...] = ("skill__",)


@dataclass(frozen=True)
class PermissionDecision:
    """Structured result of a tool-approval interaction.

    The dataclass is frozen so callers cannot mutate a decision after the audit
    log has captured it.
    """

    decision: PermissionVerdict
    reason: str
    approval_key: str
    visible_summary: Optional[str] = None
    audit_category: Optional[str] = None


@dataclass
class SessionPermissionPolicy:
    """Per-session in-memory permission state.

    The policy carries:

    * `whitelist_names`: tool names that pass silently (built-ins like
      `read_file`, plus exact MCP names like `mcp__filesystem__read_file` once
      they have been session-approved).
    * `whitelist_prefixes`: prefix rules (today only `skill__` lives here).
    * `bash_session_allows`: normalized bash command strings that the user
      session-approved during the current session.
    * `permission_mode`: drives behavior when no human is available. v1
      defaults to `strict` for interactive runs and `auto_deny` for `--once`
      and the evaluation runner.

    Persistence is intentionally out of scope per AC-8.1 — the policy lives in
    memory only and a `resume()` / `fork()` / `clear_history()` produces a
    fresh policy with the AC-1 defaults.
    """

    permission_mode: PermissionMode = "strict"
    whitelist_names: Set[str] = field(default_factory=lambda: set(DEFAULT_WHITELIST_NAMES))
    whitelist_prefixes: tuple[str, ...] = DEFAULT_WHITELIST_PREFIXES
    bash_session_allows: Set[str] = field(default_factory=set)

    @classmethod
    def with_defaults(cls, *, permission_mode: PermissionMode = "strict") -> "SessionPermissionPolicy":
        return cls(permission_mode=permission_mode)

    def is_whitelisted(self, tool_name: str, args: Dict[str, Any]) -> bool:
        """Return True when the tool can execute silently, no callback needed."""
        if self.permission_mode == "auto_allow_safe":
            return self._is_default_safe(tool_name)

        if tool_name == "bash":
            command = args.get("command", "") or ""
            normalized, _ = normalize_bash_command(command)
            return normalized in self.bash_session_allows

        if tool_name in self.whitelist_names:
            return True

        for prefix in self.whitelist_prefixes:
            if tool_name.startswith(prefix):
                return True

        return False

    def approval_key_for(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Compute the key used for `allow_session` registration."""
        if tool_name == "bash":
            command = args.get("command", "") or ""
            normalized, _ = normalize_bash_command(command)
            return normalized
        return tool_name

    def record(self, tool_name: str, decision: PermissionDecision) -> None:
        """Apply an `allow_session` decision to the session whitelist.

        `allow_once` and `deny` are no-ops here; only `allow_session` mutates
        the policy.
        """
        if decision.decision != "allow_session":
            return
        if tool_name == "bash":
            # Multiline bash commands are allow-once-only per DEC-7. The
            # approval surfaces enforce this, but record() defends in depth so
            # an upstream bug cannot accidentally persist a multiline allow.
            normalized, is_multiline = normalize_bash_command(decision.approval_key)
            if is_multiline:
                return
            self.bash_session_allows.add(normalized)
            return
        self.whitelist_names.add(tool_name)

    def reset_to_defaults(self) -> None:
        """Reset the policy to AC-1 defaults; called on fork/resume/clear."""
        self.whitelist_names = set(DEFAULT_WHITELIST_NAMES)
        self.whitelist_prefixes = DEFAULT_WHITELIST_PREFIXES
        self.bash_session_allows = set()

    def _is_default_safe(self, tool_name: str) -> bool:
        if tool_name in DEFAULT_WHITELIST_NAMES:
            return True
        for prefix in DEFAULT_WHITELIST_PREFIXES:
            if tool_name.startswith(prefix):
                return True
        return False


def make_audit_content(decision: PermissionDecision, tool_name: str) -> str:
    """Render a `PermissionDecision` into the audit-log string format.

    Format (see plan AC-9):
      `[permission] <decision> <tool>[ command=<json-quoted-key>] reason=<json-quoted-reason>[ audit_category=<cat>]`

    `approval_key` and `reason` are JSON-encoded so commands containing
    quotes, backslashes, newlines, or tabs round-trip safely. Downstream
    parsers can `json.loads` the value of each `key=...` field. The
    `[permission]` prefix and the audit-only `audit_category` token are
    plain strings because they are constrained to a small bash-token-like
    alphabet (the parser refuses anything containing shell control chars
    by classifying it as `unknown`).
    """
    parts = [PERMISSION_AUDIT_PREFIX, decision.decision, tool_name]
    if tool_name == "bash" and decision.approval_key:
        parts.append(f"command={json.dumps(decision.approval_key, ensure_ascii=False)}")
    parts.append(f"reason={json.dumps(decision.reason, ensure_ascii=False)}")
    if decision.audit_category:
        parts.append(f"audit_category={decision.audit_category}")
    return " ".join(parts)


def is_audit_content(content: Optional[str]) -> bool:
    """True when the given message content was emitted by the gate."""
    return bool(content) and content.lstrip().startswith(PERMISSION_AUDIT_PREFIX)


# ---------------------------------------------------------------------------
# bash command normalization and audit-only category parsing
# ---------------------------------------------------------------------------

# Categories are AUDIT-ONLY. They never affect approval matching.
_BASH_BUILTINS: frozenset[str] = frozenset({
    "cd", "echo", "pwd", "export", ":", "true", "false", "[", "test",
})

# Wrapper commands whose first argument names the "real" category.
_WRAPPERS_TWO_TOKEN: frozenset[str] = frozenset({"npx"})
"""Wrappers where the first remaining token after the wrapper is the category.

E.g. `npx tsc` → `tsc`.
"""

_WRAPPERS_AFTER_RUN: frozenset[str] = frozenset({"uv"})
"""Wrappers that follow `<wrapper> run <category> ...`."""

_CONTROL_OPERATORS: tuple[str, ...] = ("|", ";", "&&", "||", "&")
"""Top-level operators that always mark the command as `unknown`."""


def _has_unquoted_nonspace_remaining(command: str, start: int) -> bool:
    """True when the rest of `command` contains any non-whitespace at top level.

    Used to distinguish a trailing newline (just whitespace) from a real
    command-separator newline. We rescan quoting from `start` because a
    trailing block of whitespace may include carriage returns or further
    newlines.
    """
    n = len(command)
    in_single = False
    in_double = False
    in_dollar_single = False
    in_backtick = False
    paren_depth = 0
    j = start
    while j < n:
        ch = command[j]
        if in_single:
            if ch == "'":
                in_single = False
            j += 1
            continue
        if in_dollar_single:
            if ch == "\\" and j + 1 < n:
                j += 2
                continue
            if ch == "'":
                in_dollar_single = False
            j += 1
            continue
        if in_double:
            if ch == "\\" and j + 1 < n:
                j += 2
                continue
            if ch == "$" and j + 1 < n and command[j + 1] == "(":
                paren_depth += 1
                j += 2
                continue
            if ch == "`":
                in_backtick = True
                j += 1
                continue
            if ch == '"':
                in_double = False
            j += 1
            continue
        if in_backtick:
            if ch == "\\" and j + 1 < n:
                j += 2
                continue
            if ch == "`":
                in_backtick = False
            j += 1
            continue
        if paren_depth > 0:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
            j += 1
            continue
        if ch in (" ", "\t", "\n", "\r"):
            j += 1
            continue
        if ch == "\\" and j + 1 < n:
            return True
        if ch == "'":
            in_single = True
            j += 1
            continue
        if ch == '"':
            in_double = True
            j += 1
            continue
        if ch == "$" and j + 1 < n and command[j + 1] == "'":
            in_dollar_single = True
            j += 2
            continue
        if ch == "$" and j + 1 < n and command[j + 1] == "(":
            paren_depth += 1
            j += 2
            continue
        if ch == "`":
            in_backtick = True
            j += 1
            continue
        if ch == "(":
            paren_depth += 1
            j += 1
            continue
        return True
    return False


def normalize_bash_command(command: str) -> tuple[str, bool]:
    """Return `(approval_key, is_multiline)` for a bash command.

    Rules (see AC-3.1):
    * Strip leading and trailing whitespace from the unquoted top level.
    * Collapse runs of ordinary spaces and tabs between top-level shell words
      to a single ASCII space.
    * Preserve everything inside single quotes, double quotes, ANSI-C `$'...'`
      quotes, here-doc bodies, and command substitutions.
    * Preserve escaped whitespace verbatim.
    * Preserve real newlines; flag the command as multiline so callers can
      disable `allow_session` per DEC-7.

    The function never raises; malformed quoting falls through to the literal
    text so callers can still classify it as `unknown` and prompt the user.
    """
    if command is None:
        return "", False

    # We walk the command character by character, tracking quote / escape /
    # heredoc state and copying input through to the output.
    out: list[str] = []
    i = 0
    n = len(command)
    in_single = False
    in_double = False
    in_dollar_single = False  # ANSI-C $'...'
    in_backtick = False
    paren_depth = 0  # $( ... ) and ( ... ) subshells
    pending_space = False
    leading_ws_consumed = False
    is_multiline = False

    # Heredoc state: when we see `<<` (or `<<-`), capture the delimiter and
    # absorb the body verbatim until a line equal to the delimiter is seen.
    heredoc_delim: Optional[str] = None
    heredoc_strip_tabs: bool = False
    heredoc_buf: list[str] = []
    in_heredoc = False

    def flush_pending_space() -> None:
        nonlocal pending_space
        if pending_space and out and out[-1] not in (" ", "\n"):
            out.append(" ")
        pending_space = False

    def in_quote() -> bool:
        return in_single or in_double or in_dollar_single or in_backtick or paren_depth > 0

    while i < n:
        ch = command[i]

        if in_heredoc:
            # Absorb body until we see a line starting (after optional tabs if
            # `<<-`) with the delimiter alone.
            heredoc_buf.append(ch)
            if ch == "\n":
                # Check whether the next line completes the heredoc.
                rest = command[i + 1 :]
                line_end = rest.find("\n")
                line = rest if line_end == -1 else rest[:line_end]
                test_line = line.lstrip("\t") if heredoc_strip_tabs else line
                if test_line == heredoc_delim:
                    consume = (line_end + 1) if line_end != -1 else len(rest)
                    heredoc_buf.append(rest[:consume])
                    out.append("".join(heredoc_buf))
                    heredoc_buf = []
                    heredoc_delim = None
                    in_heredoc = False
                    i += 1 + consume
                    continue
            i += 1
            continue

        if in_single:
            out.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_dollar_single:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(command[i + 1])
                i += 2
                continue
            if ch == "'":
                in_dollar_single = False
            i += 1
            continue

        if in_double:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(command[i + 1])
                i += 2
                continue
            if ch == "$" and i + 1 < n and command[i + 1] == "(":
                # Enter a command substitution; preserve verbatim.
                out.append("(")
                paren_depth += 1
                i += 2
                continue
            if ch == "`":
                in_backtick = True
                i += 1
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if in_backtick:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(command[i + 1])
                i += 2
                continue
            if ch == "`":
                in_backtick = False
            i += 1
            continue

        if paren_depth > 0:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(command[i + 1])
                i += 2
                continue
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
            i += 1
            continue

        # Top level: handle whitespace collapsing.
        if ch == " " or ch == "\t":
            if leading_ws_consumed:
                pending_space = True
            i += 1
            continue

        if ch == "\n":
            # Top-level real newline. If there is non-whitespace content
            # remaining outside of all quotes/heredocs, this is a command
            # separator and the command is multiline (per DEC-7). If the
            # newline only precedes trailing whitespace, treat it as trailing
            # whitespace and skip it.
            if _has_unquoted_nonspace_remaining(command, i + 1):
                is_multiline = True
                pending_space = False
                out.append("\n")
            i += 1
            continue

        if ch == "\r":
            # Treat CR similarly to LF.
            if _has_unquoted_nonspace_remaining(command, i + 1):
                is_multiline = True
                pending_space = False
                out.append(ch)
            i += 1
            continue

        leading_ws_consumed = True
        flush_pending_space()

        if ch == "\\" and i + 1 < n:
            # Backslash escape: copy the escaped character verbatim, including
            # `\\\n` (line continuation) which we want to preserve as part of
            # the approval key rather than collapsing.
            out.append(ch)
            out.append(command[i + 1])
            i += 2
            continue

        if ch == "'":
            in_single = True
            out.append(ch)
            i += 1
            continue

        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue

        if ch == "$" and i + 1 < n and command[i + 1] == "'":
            in_dollar_single = True
            out.append("$'")
            i += 2
            continue

        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            paren_depth += 1
            out.append("$(")
            i += 2
            continue

        if ch == "`":
            in_backtick = True
            out.append(ch)
            i += 1
            continue

        if ch == "(":
            paren_depth += 1
            out.append(ch)
            i += 1
            continue

        # Detect heredoc start: `<<` or `<<-`, possibly followed by quoting.
        if ch == "<" and i + 1 < n and command[i + 1] == "<":
            j = i + 2
            strip_tabs = False
            if j < n and command[j] == "-":
                strip_tabs = True
                j += 1
            # Skip optional whitespace between `<<` and the delimiter.
            while j < n and command[j] in (" ", "\t"):
                j += 1
            # Capture the delimiter token, stripping any quotes.
            delim_chars: list[str] = []
            quote_char: Optional[str] = None
            while j < n:
                c = command[j]
                if quote_char is None:
                    if c in ("'", '"'):
                        quote_char = c
                        j += 1
                        continue
                    if c.isalnum() or c in ("_", "-", "."):
                        delim_chars.append(c)
                        j += 1
                        continue
                    break
                else:
                    if c == quote_char:
                        quote_char = None
                        j += 1
                        continue
                    delim_chars.append(c)
                    j += 1
                    continue
            if delim_chars:
                # Include `<<` (and optional `-`) and the captured delimiter
                # text in the output, then absorb the body.
                out.append(command[i:j])
                heredoc_delim = "".join(delim_chars)
                heredoc_strip_tabs = strip_tabs
                # Find the end-of-line; everything until and including the
                # newline goes through verbatim before we enter heredoc body.
                eol = command.find("\n", j)
                if eol == -1:
                    out.append(command[j:])
                    i = n
                else:
                    out.append(command[j : eol + 1])
                    is_multiline = True
                    in_heredoc = True
                    i = eol + 1
                continue
            # Not a heredoc, fall through.

        out.append(ch)
        i += 1

    # Drain a pending heredoc body that ran to EOF without a closing line.
    if heredoc_buf:
        out.append("".join(heredoc_buf))

    normalized = "".join(out).rstrip()
    return normalized, is_multiline


def categorize_bash_command(command: str) -> str:
    """Return an audit-only category for the bash command.

    The category is shown alongside the command in the approval modal and
    recorded in the audit log; it does not affect approval matching. The
    parser is conservative: anything it cannot prove safe to bucket is
    classified as `unknown`.
    """
    if not command:
        return "unknown"

    text = command.strip()
    if not text:
        return "unknown"

    # Strip a top-level `# comment` tail BEFORE we scan for control operators
    # so `git status  # check` does not get classified by the comment.
    text = _strip_top_level_comment(text)
    if not text:
        return "unknown"

    # Quick reject: any control operator or substitution at the top level
    # forces `unknown`. We re-use the normalizer to identify quoted regions.
    if _has_top_level_control_operator(text):
        return "unknown"

    # Skip leading `VAR=value` env-prefix segments.
    tokens = _safe_split(text)
    if tokens is None:
        return "unknown"

    idx = _skip_env_assignments(tokens, 0)

    # Skip up to one `cd <path> &&` segment.
    if idx + 2 < len(tokens) and tokens[idx] == "cd" and tokens[idx + 2] == "&&":
        idx += 3
        idx = _skip_env_assignments(tokens, idx)

    if idx >= len(tokens):
        return "unknown"

    head = tokens[idx]

    # Detect any control-operator token leaking through tokenization (defense
    # in depth — the regex check above should already have rejected these).
    for op in _CONTROL_OPERATORS:
        if op in tokens[idx:]:
            return "unknown"

    # Heads containing unquoted expansion syntax can do almost anything; bucket
    # them as `unknown` rather than letting the literal `${VAR}=value` text
    # become the audit category.
    if "$" in head:
        return "unknown"

    # Sourced scripts execute arbitrary shell in the current context.
    if head in {"source", "."}:
        return "unknown"

    # `command [opts] <name>` is a shell builtin that bypasses functions and
    # supports option-sensitive behavior. We do not try to unwrap it.
    if head == "command":
        return "unknown"

    # Redirections without a leading executable.
    if head in {">", ">>", "<", "<<", "<<-", "<<<", "|"}:
        return "unknown"

    if head in _BASH_BUILTINS:
        return "builtin"

    # Wrappers: `uv run X` → X
    if head in _WRAPPERS_AFTER_RUN and idx + 2 < len(tokens) and tokens[idx + 1] == "run":
        return _strip_extension(tokens[idx + 2])

    # `python -m X` → X (python and python3 are equivalent for this rule).
    if head in {"python", "python3"} and idx + 2 < len(tokens) and tokens[idx + 1] == "-m":
        return _strip_extension(tokens[idx + 2])

    if head in _WRAPPERS_TWO_TOKEN and idx + 1 < len(tokens):
        return _strip_extension(tokens[idx + 1])

    return _strip_extension(head)


def _strip_top_level_comment(text: str) -> str:
    """Strip a trailing `# comment` from a top-level bash command.

    Stops at the first unquoted `#` whose preceding character is whitespace
    (or string start). Inside quoted regions, `#` is literal text.
    """
    in_single = False
    in_double = False
    in_dollar_single = False
    in_backtick = False
    paren_depth = 0
    n = len(text)
    out_end = n
    prev = " "
    i = 0
    while i < n:
        ch = text[i]
        if in_single:
            if ch == "'":
                in_single = False
        elif in_dollar_single:
            if ch == "\\" and i + 1 < n:
                i += 2
                prev = ch
                continue
            if ch == "'":
                in_dollar_single = False
        elif in_double:
            if ch == "\\" and i + 1 < n:
                i += 2
                prev = ch
                continue
            if ch == "$" and i + 1 < n and text[i + 1] == "(":
                paren_depth += 1
                i += 2
                prev = "("
                continue
            if ch == "`":
                in_backtick = True
            elif ch == '"':
                in_double = False
        elif in_backtick:
            if ch == "\\" and i + 1 < n:
                i += 2
                prev = ch
                continue
            if ch == "`":
                in_backtick = False
        elif paren_depth > 0:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
        else:
            if ch == "\\" and i + 1 < n:
                i += 2
                prev = ch
                continue
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == "$" and i + 1 < n and text[i + 1] == "'":
                in_dollar_single = True
                i += 2
                prev = "'"
                continue
            elif ch == "$" and i + 1 < n and text[i + 1] == "(":
                paren_depth += 1
                i += 2
                prev = "("
                continue
            elif ch == "`":
                in_backtick = True
            elif ch == "(":
                paren_depth += 1
            elif ch == "#" and prev in (" ", "\t"):
                out_end = i
                break
        prev = ch
        i += 1
    return text[:out_end].rstrip()


def _strip_extension(name: str) -> str:
    base = os.path.basename(name)
    if "." in base:
        base = base.split(".", 1)[0]
    return base or "unknown"


def _safe_split(text: str) -> Optional[list[str]]:
    """Best-effort token split that gives up on malformed quoting."""
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return None


def _skip_env_assignments(tokens: list[str], start: int) -> int:
    idx = start
    while idx < len(tokens):
        tok = tokens[idx]
        if "=" not in tok:
            return idx
        head, _, _ = tok.partition("=")
        if not head or not head.replace("_", "").isalnum() or head[0].isdigit():
            return idx
        idx += 1
    return idx


def _has_top_level_control_operator(text: str) -> bool:
    """True if the command contains a control operator at the unquoted top level.

    We allow exactly one leading `cd <path> &&` prefix; any later `&&` is
    treated as control flow and forces `unknown`.
    """
    state_in_single = False
    state_in_double = False
    state_in_backtick = False
    paren_depth = 0
    saw_leading_cd_chain = False
    leading_idx = 0

    # Detect leading `cd <path> &&` and remember its end position.
    leading_match = _LEADING_CD_CHAIN.match(text)
    if leading_match:
        saw_leading_cd_chain = True
        leading_idx = leading_match.end()

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if state_in_single:
            if ch == "'":
                state_in_single = False
            i += 1
            continue
        if state_in_double:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "`":
                state_in_backtick = True
            elif ch == '"':
                state_in_double = False
            i += 1
            continue
        if state_in_backtick:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "`":
                state_in_backtick = False
            i += 1
            continue
        if paren_depth > 0:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
            i += 1
            continue

        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "'":
            state_in_single = True
            i += 1
            continue
        if ch == '"':
            state_in_double = True
            i += 1
            continue
        if ch == "`":
            return True
        if ch == "(":
            return True
        if ch == "$" and i + 1 < n and text[i + 1] == "(":
            return True
        if ch == "<" and i + 1 < n and text[i + 1] == "<":
            return True
        if ch == "|" or ch == ";" or ch == "\n":
            return True
        if ch == "&":
            # `&&` and `||` are two-char; lone `&` is also a control operator
            # (background). Both go to unknown unless `&&` is the leading
            # `cd ... &&` we already accounted for.
            if i + 1 < n and text[i + 1] == "&":
                if saw_leading_cd_chain and i + 2 <= leading_idx:
                    i += 2
                    continue
                return True
            return True
        if ch == "|" and i + 1 < n and text[i + 1] == "|":
            return True
        i += 1
    return False


_LEADING_CD_CHAIN = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*cd\s+\S+\s+&&\s+")

def policy_mode_from_env(default: PermissionMode = "strict") -> PermissionMode:
    """Resolve `permission_mode` from environment for non-interactive entry points.

    Returns the requested mode after validation, or the supplied default when
    the environment variable is missing or invalid. Centralizing this keeps
    `Config.load_config()` and the CLI/eval entry points in agreement.
    """
    value = os.getenv("GEM_CODE_PERMISSION_MODE", "").strip().lower()
    if value in {"strict", "auto_deny", "auto_allow_safe"}:
        return value  # type: ignore[return-value]
    return default


def deny(reason: str, *, approval_key: str = "", audit_category: Optional[str] = None) -> PermissionDecision:
    """Convenience constructor for synthesized denial decisions."""
    return PermissionDecision(
        decision="deny",
        reason=reason,
        approval_key=approval_key,
        audit_category=audit_category,
    )


__all__ = [
    "PERMISSION_AUDIT_PREFIX",
    "DEFAULT_WHITELIST_NAMES",
    "DEFAULT_WHITELIST_PREFIXES",
    "PermissionDecision",
    "PermissionMode",
    "PermissionVerdict",
    "SessionPermissionPolicy",
    "categorize_bash_command",
    "deny",
    "is_audit_content",
    "make_audit_content",
    "normalize_bash_command",
    "policy_mode_from_env",
]
