# Session-Scoped Tool Permission Gate With Predict Before Call

## Goal Description

Add three behaviors to the gem-code agent so that the user retains visible, per-call control over what tools the model executes. The behaviors are layered behind a single permission chokepoint at `Session.run_tool()`:

1. A per-session permission gate. A starting whitelist (defaults to `read_file`, `Glob`, `Grep`, plus the `skill__*` prefix) executes silently. Every other tool — `bash`, `write_file`, `StrReplaceFile`, `fetch_url`, and any `mcp__*` tool — is intercepted before execution and the user picks one of three actions: allow once, allow this session, or deny. Allow-this-session adds the tool (or, for `bash`, the exact normalized command string) to the per-session whitelist; deny ends only that one tool call. The gate enforces execution control only — the model still sees the full advertised tool list.

2. An optional predict-before-call mode, gated by env flag `GEM_CODE_PREDICT_BEFORE_CALL`. When enabled, the system prompt is augmented with an instruction asking the main LLM to write a brief prediction of observable side effects in its reasoning/content stream BEFORE emitting any tool call, so that its own reasoning incorporates the prediction. There is no second isolated LLM call; the prediction is part of the assistant turn that is already streamed to the TUI/CLI. The approval modal is unchanged by this flag — the prediction lands in the model's visible output, not in a modal field.

3. A tool-set rebuild before each user message, so newly added skills or, in the future, accepted self-discovered tools become callable on the next turn. The rebuild is lazy — it re-runs only when the relevant fingerprint changes, and it rebuilds the `USE_TOOL_SEARCH` embedding map at the same time so similarity ranking never sees stale embeddings.

The original draft also requested a tool self-learning feature (counter over the last 100 bash invocations, LLM-generated JSON schema, git patch staged against `src/tool.py`, conversation paused for user review). That feature is **deferred to a follow-up release** per DEC-1; this plan keeps the env flag and audit hooks in place so it can drop in without architectural change.

The implementation must preserve OpenAI tool-call protocol invariants (every assistant tool_call must be followed by a `tool` reply with the matching `tool_call_id`), must not bypass the existing Landlock sandbox in `src/security.py`, and must not regress non-interactive runs (`--once`, evaluation runner).

## Acceptance Criteria

Following TDD philosophy, each criterion includes positive and negative tests for deterministic verification.

- AC-1: Initial default whitelist contains exactly four entries: the tool names `read_file`, `Glob`, `Grep`, and any tool whose name starts with the `skill__` prefix. All other tools — including `bash`, `write_file`, `StrReplaceFile`, `fetch_url`, and every `mcp__*` tool — are gated by default. Gating affects execution only; the advertised tool list passed to the model is unchanged.
  - Positive Tests (expected to PASS):
    - A fresh `Session` constructed with no overrides reports a whitelist exactly equal to `{"read_file", "Glob", "Grep"}` plus a `skill__` prefix rule.
    - The model's request payload (Chat Completions or Responses path) advertises every tool in `Session._all_tools`, regardless of which entries are gated.
    - A model-emitted call to `read_file` runs without invoking the approval callback.
  - Negative Tests (expected to FAIL):
    - A model-emitted call to `bash` in a fresh session bypassing the approval callback. The gate must invoke the callback before any bash execution.
    - A model-emitted call to a `mcp__filesystem__write_file` tool succeeding silently in a fresh session. The gate must intercept it.

- AC-2: Three-choice approval flow. The gate exposes an awaitable `request_tool_approval(tool_name, args, context) -> PermissionDecision` callback. In TUI mode the callback opens a Textual `ToolApprovalScreen` ModalScreen via `push_screen_wait` that returns the decision. In CLI mode the callback prompts via `async_input` with `[A]llow once / [S]ession-allow / [D]eny`. All non-affirmative outcomes resolve to deny:
  - Modal dismissed without choice → deny with reason `modal_dismissed`.
  - App shutdown / cancellation → deny with reason `shutdown`.
  - Approval callback raises an exception → deny with reason `callback_error: <exc>`.
  - CLI EOF or KeyboardInterrupt during the prompt → deny with reason `cli_interrupt`.
  Multiple tool calls in a single assistant turn each receive an independent decision; one denial does not skip subsequent calls. The approval modal/prompt has no inactivity timeout (DEC-6); it blocks until the user responds.
  - AC-2.1: TUI flow returns a structured `PermissionDecision` from the modal.
    - Positive: a TUI modal with the user pressing the "Allow Once" button returns `PermissionDecision(decision="allow_once", ...)` and chat resumes.
    - Negative: pressing the modal's escape binding returns deny rather than silently approving.
  - AC-2.2: CLI flow accepts `[A/S/D]` and resolves all non-affirmative inputs to deny.
    - Positive: typing `s` followed by Enter returns `PermissionDecision(decision="allow_session", ...)`.
    - Negative: pressing Ctrl-C during the CLI prompt resolves to deny, not approve.
  - AC-2.3: Concurrent tool calls in one turn each get their own decision.
    - Positive: a model turn with calls `[read_file(a), bash(b), bash(c)]` produces one approval-callback invocation each for `bash(b)` and `bash(c)` (the read_file is silent), and a deny on `bash(b)` does not skip the prompt for `bash(c)`.
    - Negative: a single approval decision applied to multiple bash calls in the same turn.

- AC-3: Permission granularity.
  - AC-3.1: bash is gated by the EXACT normalized command string (DEC-3). The approval key for a bash invocation is the `command` argument after a deterministic shell-aware whitespace normalization with these rules:
    - Strip leading and trailing whitespace from the unquoted top level.
    - Collapse runs of ordinary spaces and tabs between top-level shell words to a single ASCII space.
    - Do NOT normalize whitespace inside single quotes, double quotes, ANSI-C `$'...'` quotes, here-doc bodies, command substitutions `$(...)` or backtick form, or after a backslash-escape.
    - Do NOT collapse newlines that act as command separators. Multiline commands (any unquoted/un-escaped real newline outside a here-doc) are eligible for `allow_once` ONLY; the modal greys out / disables the "allow session" choice for them, and the CLI prompt accepts only `A` or `D` (DEC-7).
    - "Allow this session" registers the normalized command string verbatim; future calls whose normalized command string matches byte-for-byte run silently. The originally submitted (non-normalized) command is what gets executed; only the approval key is normalized.
    - A bash command-category parser still runs but produces only AUDIT metadata. Categorization rules: skip leading `VAR=value` env-prefix segments and up to one `cd <path> &&` segment; for the next remaining token, normalize wrappers (`uv run X` → `X`, `python -m X` → `X`, `npx X` → `X`); shell builtins (`cd`, `echo`, `pwd`, `export`, `:`, `true`, `false`, `[`, `test`) → `builtin`. Any shell control operator outside the allowed leading `cd <path> &&` prefix — `|`, `;`, `&&`, `||`, `&`, backticks, `$(...)`, here-doc start tokens, subshells `(...)` — classifies the entire command as `unknown`. Quoted shell metacharacters that the parser cannot prove are quoted data classify as `unknown`. The category is shown alongside the command in the approval modal and recorded in the audit log; it does NOT affect approval matching.
    - Positive: `"  git   status\t"` and `"git status"` produce the same approval_key; after `allow_session` on the first, the second runs silently. `"VAR=1 git status"` carries audit category `git`. `"cd /tmp && git log"` carries audit category `git`.
    - Negative: `"git status"` allow_session does NOT silence `"git status --short"` (different normalized key). `"git status | grep foo"` carries audit category `unknown` and the modal surfaces this. A bash call containing a real newline does not present "allow session" as an option.
  - AC-3.2: Non-bash built-in tools are gated by exact tool name; MCP tools are gated by full `mcp__{server}__{tool}` name. Server-level approval is out of scope for v1.
    - Positive: `allow_session` on `mcp__filesystem__read_file` does not silently approve `mcp__filesystem__write_file`.
    - Negative: granting "allow session" to `mcp__filesystem__read_file` accidentally approving any other `mcp__filesystem__*` tool.
  - AC-3.3: Approval never bypasses the Landlock sandbox in `src/security.py`. Sandbox enforcement runs after gate approval; approving a `bash` call still subjects the resulting subprocess to `SecuritySettings`, including network and path constraints.
    - Positive: an approved bash call that violates Landlock policy still fails with the existing sandbox error path.
    - Negative: approving a tool causes the Landlock policy to be skipped or downgraded.

- AC-4: Denial protocol. A `PermissionDecision` dataclass carries `decision` ∈ {`allow_once`, `allow_session`, `deny`}, `reason` (string), `approval_key` (string used for session-allow registration), and `visible_summary` (Optional, reserved; in v1 always `None` because predict-before-call is implemented as a system-prompt augmentation per AC-5 and does not produce a summary string). When `decision == deny`, `Session._handle_model_turn` MUST append `Message(role="tool", content=f"<user_denied: {reason}>", tool_call_id=tool_call.id)` for that exact `tool_call_id`, BEFORE processing the next tool_call in the same assistant turn. The model continues the turn and may revise its plan based on the denial reason. Approval-callback exceptions are converted to `decision=deny` with `reason=f"callback_error: {exc}"`, so the protocol invariant is preserved even when the UI fails.
  - Positive: a turn with three tool calls where the second is denied produces three `tool` reply messages (one allow_once result, one denial, one allow_once result) all bound to their original `tool_call_id`s; the next API request is protocol-valid.
  - Positive: an exception inside the approval callback yields a denial `tool` message with `content="<user_denied: callback_error: ...>"` and chat continues without crashing.
  - Negative: a denied call producing zero `tool` messages (which would invalidate the next API request).
  - Negative: a denied call producing a `tool` message with a synthesized or empty `tool_call_id`.

- AC-5: Predict-before-call (env flag `GEM_CODE_PREDICT_BEFORE_CALL`, default `false`). When enabled, the system prompt produced by `get_system_prompt()` (`src/config.py`) is augmented with a predict-before-call instruction asking the main LLM to: write one or two short sentences of predicted observable side effects (files written, network calls, processes spawned) in its reasoning or content stream BEFORE emitting any tool call, then decide whether to actually emit the tool call. The instruction explicitly forbids hidden chain-of-thought; the prediction must appear in the assistant's visible output. The gate's runtime path does NOT make any second isolated LLM call, and `Session.history` is mutated only through the existing `chat()` flow exactly as it would be without the flag. The approval modal is unchanged by this flag (the prediction is the model's own streamed output, visible in the TUI's `OptimizedStreamingWidget` or the CLI console — not a modal field). Disabling the flag (default) leaves the system prompt unchanged.
  - AC-5.1: System-prompt augmentation. With the flag enabled, `get_system_prompt()` returns a string containing a predict-before-call clause; with the flag disabled, the returned string does not contain the clause.
    - Positive: setting `GEM_CODE_PREDICT_BEFORE_CALL=true` and starting a new session yields a system prompt whose text contains the predict-before-call instruction; the same env unset returns the legacy system prompt verbatim.
    - Negative: enabling the flag silently mutating any other part of the system prompt unrelated to predict-before-call.
  - AC-5.2: No extra LLM round-trip. The runtime tool-call path issues no additional API request specifically to generate a prediction.
    - Positive: a turn that ends in a gated tool call produces exactly the same number of API requests with `GEM_CODE_PREDICT_BEFORE_CALL=true` as with it unset (the difference is in the prompt content, not the request count).
    - Negative: the runtime path issuing an extra `tool_choice="none"` completion solely to generate a prediction.
  - AC-5.3: No history mutation by the gate. The permission gate does not append, delete, or rewrite entries in `Session.history`. Predictions, when present, arrive there only because the model emitted them as part of its normal assistant turn.
    - Positive: a snapshot of `Session.history` before and after a gated turn that was approved differs only by the standard assistant message + tool reply pair, not by any gate-injected entry.
    - Negative: the gate writing a synthetic system or assistant message into `Session.history`.

- AC-6 (DEFERRED to a follow-up release per DEC-1): tool self-discovery — the bash invocation counter, schema synthesis, sidecar registry at `~/.gem_code/projects/{encoded_cwd}/discovered_tools.json`, audit-only git patch generation, and `ToolDiscoveryScreen` modal — is NOT included in v1. The env flag `GEM_CODE_SELF_DISCOVERY` is reserved and registered as a Config field with default `false`, but no behavior is wired behind it in v1; it is a deliberate no-op so a follow-up release can drop in the curator without changing the public Config surface or the env-variable contract.

- AC-7: Tool-set rebuild on each `Session.chat()` entry. The rebuild merges built-in tools from `clone_tools()` with skill tools and the existing MCP tool set from session init. Rebuild is lazy: it re-runs only when the recursive fingerprint of `skills_dir` (deepest-mtime + file count) has changed since the previous `chat()` invocation. The MCP set is captured once at session init (matches the current behavior in `src/session.py`); mid-session MCP reconnection is out of scope for v1. When `Config.use_tool_search == True` (env `USE_TOOL_SEARCH`), the embedding map is rebuilt at the same moment so `search_tool()` and the embedding-keyed lookup never see stale embeddings.
  - Positive: dropping a new SKILL.md into `skills_dir` makes its skill tool callable on the next user message without restarting the session.
  - Positive: when `USE_TOOL_SEARCH=true`, after a skills_dir change, a tool whose description newly matches the user's prompt appears in the search ranking.
  - Negative: a `chat()` entry where neither fingerprint changed performing a full `clone_tools()` plus skill scan plus embedding rebuild (must be a no-op in that case).

- AC-8: Persistence.
  - AC-8.1: Per-session whitelist is in-memory only by default and does NOT survive `SessionManager.resume()`, `SessionManager.fork()`, or `Session.clear_history()`. Each of those operations starts the new session with the AC-1 default whitelist. This is documented behavior; cross-session persistence is reserved for a future iteration.
    - Positive: granting `allow_session` on `bash` "git status", then resuming the same session id, requires the user to re-approve `git status`.
    - Negative: a resumed session inheriting prior `allow_session` decisions silently.
  - AC-8.2: Reserved for the deferred self-discovery feature; in v1 there is no on-disk policy state.

- AC-9: Audit log. Every `PermissionDecision` (allow_once, allow_session, deny) is appended to the session JSONL via `Memory_Unit` with `type="message"`, `role="system"`, and a `[permission]` content prefix. Format: `[permission] <decision> <tool_name>[ command="<approval_key>"] reason=<reason> [audit_category=<category>]`. Audit-only category metadata for bash calls is included. These `[permission]` entries are TRANSCRIPT-ONLY: they are NOT appended to `Session.history` (the model-visible message list) and they are NOT rehydrated into model-visible context on `resume()` or `fork()`. They live solely in the on-disk JSONL for human audit. No `Memory_Unit` schema migration is needed; existing fields suffice.
  - Positive: granting `allow_once` on `bash "git status"` produces a JSONL entry containing `[permission] allow_once bash command="git status" reason=user_choice audit_category=git`; reading the next assistant turn shows that the model's history does not contain that entry.
  - Positive: a `resume()` that rehydrates context skips `[permission]` entries when reconstructing model-visible history.
  - Negative: a `[permission]` entry appearing in `Session.history` and being sent to the model.
  - Negative: a denial leaving no audit entry.

- AC-10: Non-interactive mode. A new `Config.permission_mode` field with values {`strict`, `auto_deny`, `auto_allow_safe`}. Defaults:
  - Interactive TUI and interactive CLI: `strict` (the modal/prompt is shown).
  - `--once` and the evaluation runner (`evaluation/run_swebench_verified.py` or any caller that sets a non-interactive flag): `auto_deny`.
  In `auto_deny`, gated tool calls receive an automatic deny decision with `reason="non_interactive"` immediately and the predict-before-call request is skipped (AC-5). In `auto_allow_safe`, the gate silently allows EXACTLY the AC-1 default whitelist and denies everything else; v1 does not extend the safe list. Future iterations may expand it via config.
  - Positive: running `python main.py --cli --once "do X"` on a turn that requires `bash` produces an immediate deny with `reason="non_interactive"`, the run does not hang, and the model receives a denial `tool` message.
  - Positive: running with `permission_mode=auto_allow_safe` allows `read_file` silently and denies `bash` silently.
  - Negative: a `--once` run blocking on stdin waiting for user input.
  - Negative: `auto_allow_safe` allowing any tool outside the AC-1 whitelist in v1.

- AC-11: Tests cover the v1 surface:
  - allow_once / allow_session / deny matrix on each gated tool category, including protocol validity (denied call still produces a bound `tool` message).
  - multiple tool calls in one assistant turn with mixed decisions (independent decisions, no skipping).
  - bash category-parser edge cases for AUDIT metadata: `"VAR=v cmd"`, `"cd /x && cmd"`, `"uv run pytest"`, `"python -m pytest"`, `"npx tsc"`, `"git status | grep"`, `"git status; ls"`, `"git status && rm -rf x"`, `"bash -c '...'"`, `":"`, redirection-only commands, here-doc bodies.
  - approval-key normalization edge cases: leading/trailing whitespace, internal whitespace runs, quoted whitespace preservation, escaped whitespace, multiline commands forcing allow-once-only.
  - CLI EOF and KeyboardInterrupt during the prompt resolve to deny.
  - predict-before-call: `get_system_prompt()` returns a body containing the predict clause when `GEM_CODE_PREDICT_BEFORE_CALL=true` and an unchanged body when unset; gated tool-call paths issue no extra LLM request regardless of the flag; `Session.history` snapshots before and after a gated turn show no entries injected by the gate.
  - lazy rebuild: `chat()` no-ops when no fingerprint changed; rebuild fires when a skill file is added; `USE_TOOL_SEARCH=true` rebuilds the embedding map at the same time.
  - audit log: `[permission]` entries land in the JSONL; they are absent from `Session.history`; resume() does not rehydrate them.
  - non-interactive mode: `--once` triggers `auto_deny`; `permission_mode=auto_allow_safe` silently allows AC-1 whitelist and denies everything else.


## Path Boundaries

### Upper Bound (Maximum Acceptable Scope)

The implementation lands AC-1 through AC-5 and AC-7 through AC-11, fully. It introduces a new module `src/permissions.py` housing `PermissionDecision`, `SessionPermissionPolicy`, the bash command-category parser (audit-only), and the shell-aware approval-key normalizer. `Session` accepts an injected `request_tool_approval` async callback through `Session.chat()` and threads it through `_handle_model_turn` and `run_tool`. The TUI exposes `ToolApprovalScreen` with three buttons and uses `push_screen_wait` so the modal returns a structured decision. The CLI prompt is implemented on top of `async_input` with `EOF=deny` and `KbdInterrupt=deny`. The `Config` dataclass gains four new fields (`permission_gate_enabled`, `predict_before_call_enabled`, `self_discovery_enabled`, `permission_mode`) loaded via env vars. Predict-before-call is implemented as a system-prompt augmentation in `get_system_prompt()`: when enabled, the system prompt instructs the main LLM to write a brief prediction in its visible reasoning/content stream before emitting a tool call; no isolated extra LLM round-trip exists at runtime. The lazy tool/embedding rebuild is wired into the top of `Session.chat()` with a recursive `skills_dir` fingerprint. `[permission]` audit entries are written through the existing `Memory_Unit` schema and are filtered out of model-visible context on `resume()` and `fork()`. AC-11 tests are implemented end-to-end with mock approval callbacks, fake sessions, and JSONL fixtures. Self-discovery (AC-6) is a documented no-op behind its env flag.

### Lower Bound (Minimum Acceptable Scope)

The implementation lands AC-1, AC-2, AC-3, AC-4, AC-7 (without self-discovery integration; lazy rebuild only triggered by `skills_dir` fingerprint), AC-8.1, AC-9 (audit log), AC-10 (non-interactive `auto_deny` for `--once` and evaluation runner), and AC-11 minus the predict-specific tests. The env flags `GEM_CODE_PREDICT_BEFORE_CALL` and `GEM_CODE_SELF_DISCOVERY` exist as Config fields with default `false` but the predict system-prompt clause (AC-5) is not yet wired — the system prompt is unchanged regardless of the flag value at the lower bound. This satisfies the acceptance criteria for the gate, denial protocol, audit log, non-interactive mode, and lazy rebuild while leaving predict-before-call as a follow-up enable.

### Allowed Choices

- Can use:
  - A new module `src/permissions.py` for the policy, decision dataclass, normalizer, and audit-only category parser.
  - Textual `push_screen_wait` for the awaitable modal, even though it is not currently used elsewhere in the codebase.
  - Reusing `asyncio.to_thread` (already used by `cli.async_input`) for any blocking CLI prompt work.
  - Reusing `Memory_Unit` with the existing fields for audit entries (no schema migration).
  - Augmenting the system prompt in `get_system_prompt()` (`src/config.py`) with the predict-before-call clause when the env flag is enabled.
  - Adding fields to the `Config` dataclass following the existing snake_case env-var pattern.
- Cannot use:
  - Writing to `src/tool.py` from the running agent under any circumstances. v1 has no self-discovery, and even if a follow-up adds it, the runtime path may not modify source code.
  - Bypassing or downgrading the Landlock policy in `src/security.py` based on a permission approval.
  - Tool-name-only granularity for `bash` (every `allow_session` for bash is keyed on the normalized command string).
  - Hidden chain-of-thought in the predict-before-call system-prompt clause (the prediction must be visible in the model's streamed reasoning/content output).
  - Issuing an extra `tool_choice="none"` LLM round-trip from inside the gate solely to generate a prediction.
  - Mutating `Session.history` from inside the gate or the permission policy.
  - Inactivity timeouts on the approval modal/prompt (DEC-6).

## Feasibility Hints and Suggestions

> Reference and understanding only. The conceptual approach below is one acceptable implementation path; other shapes that satisfy the acceptance criteria are equally valid.

### Conceptual Approach

A new `src/permissions.py` module hosts:

```
@dataclass(frozen=True)
class PermissionDecision:
    decision: Literal["allow_once", "allow_session", "deny"]
    reason: str
    approval_key: str
    visible_summary: Optional[str] = None
    audit_category: Optional[str] = None

class SessionPermissionPolicy:
    def __init__(self, defaults: PolicyDefaults): ...
    def is_whitelisted(self, tool_name: str, args: dict) -> bool: ...
    def record_decision(self, decision: PermissionDecision) -> None: ...
    def approval_key_for(self, tool_name: str, args: dict) -> str: ...

def normalize_bash_command(cmd: str) -> tuple[str, bool]:
    # returns (normalized_key, is_multiline)

def categorize_bash_command(cmd: str) -> str:
    # returns audit-only category, or "unknown"
```

`Session.__init__` constructs the policy with AC-1 defaults. `Session.chat()` accepts an additional optional argument:

```
async def chat(self, user_input, *, request_tool_approval=None, ...): ...
```

passes it down to `_handle_model_turn` which passes it to `run_tool`. `run_tool` consults `policy.is_whitelisted`; if not whitelisted, it builds an `ApprovalContext` and awaits `request_tool_approval(...)`. On `deny`, `run_tool` returns a structured `_PermissionDenied` sentinel; `_handle_model_turn` recognizes the sentinel and appends `Message(role="tool", content=..., tool_call_id=tool_call.id)` for that exact call before continuing the loop. The predict-before-call feature shapes the SYSTEM PROMPT only — the runtime gate path is identical regardless of the flag value.

In TUI mode, `_generate_response` now passes a closure that calls `await self.push_screen_wait(ToolApprovalScreen(context))` and converts the screen result into a `PermissionDecision`. In CLI mode, `cli.py`'s chat loop passes a closure that calls `async_input` and parses `[A/S/D]`. In non-interactive mode (`--once`, eval), the closure returns deny immediately.

Predict-before-call lives in `src/config.py::get_system_prompt()`. When `Config.predict_before_call_enabled` is `True`, `get_system_prompt()` appends a clause asking the model to: "Before emitting any tool call, write one or two sentences in your visible output describing what observable side effects (files written, network calls, processes spawned) you expect from the call, then decide whether to actually emit it." When the flag is `False`, the legacy system-prompt body is returned unchanged. The runtime tool-call path performs no extra LLM round-trip and the gate never mutates `self.history`.

The audit log uses `memory_acess.add_line(Memory_Unit(type="message", role="system", content=audit_string).model_dump_json())` and the rehydration path filters by content prefix `[permission]` so these entries never become model-visible.

The lazy rebuild fingerprint stores the recursive deepest-mtime + file count of `skills_dir` and the loaded set of MCP tool names; on each `chat()` entry, recomputed-fingerprint != last-fingerprint triggers `clone_tools()` + skill scan + (if `use_tool_search`) `build_tool_embedding`.

### Relevant References

- `src/session.py` (chat, _handle_model_turn, run_tool, chat_one_step) — the integration spine.
- `src/tool.py` (TOOLS, clone_tools, build_tool_embedding, search_tool) — the tool-set surface that gets rebuilt.
- `src/security.py` (SecuritySettings, build_command_policy, load_security_settings) — the `frozen dataclass + env-loader` precedent for `SessionPermissionPolicy` and `Config.permission_mode`.
- `src/tui.py` (HelpScreen at the modal-screen precedent, _generate_response, ToolStartMessage/ToolResultMessage) — the surface the `ToolApprovalScreen` plugs into.
- `src/cli.py` (`async_input`, the `cli_chat` loop, EOF/KeyboardInterrupt handling) — the CLI integration surface.
- `src/config.py` (`Config` dataclass, `load_config`, env-var pattern) — the surface for new permission-mode and env flags.
- `src/memory.py` (`Memory_Unit`, `JsonlRandomAccess`) — the audit-log surface.
- `src/session_manager.py` (init/resume/fork transcript paths) — to confirm in-memory whitelist policy correctly does not survive these operations.
- `src/context_manager.py` (`autocompaction` and how it filters history during rehydration) — the precedent for filtering `[permission]` entries out of model-visible context.

## Dependencies and Sequence

### Milestones

1. Foundation — chokepoint and protocol invariants.
   - Phase A: introduce `src/permissions.py` with `PermissionDecision`, `SessionPermissionPolicy`, `normalize_bash_command`, `categorize_bash_command`. Land unit tests for the normalizer and category parser.
   - Phase B: thread `request_tool_approval` through `Session.chat → _handle_model_turn → run_tool`; add the `_PermissionDenied` sentinel path; ensure the bound `tool_call_id` reply message is emitted on every denial. Land protocol tests for single-call and multi-call turns.
   - Phase C: Config fields + env-var loading for the four new flags + `permission_mode`.

2. User surface — TUI and CLI approval flows.
   - Phase A: implement `ToolApprovalScreen` (Textual ModalScreen with three buttons and an Escape binding) and wire `_generate_response` to provide the awaitable closure via `push_screen_wait`. Land TUI snapshot tests where feasible.
   - Phase B: implement the CLI `[A/S/D]` prompt with EOF/KeyboardInterrupt → deny. Land CLI tests.
   - Phase C: non-interactive default `auto_deny` for `--once` and the evaluation runner. Land integration test that `--once` does not hang.

3. Audit log and lazy rebuild.
   - Phase A: write `[permission]` `Memory_Unit` entries and verify rehydration filters them out of model-visible history.
   - Phase B: lazy tool-set rebuild on `chat()` entry with recursive `skills_dir` fingerprint; rebuild `USE_TOOL_SEARCH` embeddings simultaneously. Land test that no-change `chat()` is a no-op.

4. Predict-before-call (system-prompt augmentation).
   - Phase A: extend `get_system_prompt()` in `src/config.py` to append the predict-before-call clause when `Config.predict_before_call_enabled` is `True`. Verify the system-prompt body differs only in the appended clause.
   - Phase B: confirm the runtime tool-call path is unchanged: no extra LLM round-trip, no `Session.history` mutation by the gate.

Dependencies: Milestone 1 must complete before Milestone 2 (the awaitable callback parameter must exist before TUI/CLI can plug into it). Milestone 3 Phase A depends on Milestone 1 Phase B (audit log content is the `PermissionDecision` produced by the gate). Milestone 4 depends on Milestone 1 Phase C (the `Config.predict_before_call_enabled` field must exist before `get_system_prompt()` can read it); it does not depend on the approval UI in Milestone 2 because the system-prompt clause is independent of the modal.

## Task Breakdown

| Task ID | Description | Target AC | Tag (`coding`/`analyze`) | Depends On |
|---------|-------------|-----------|--------------------------|------------|
| task1 | Introduce `src/permissions.py` with `PermissionDecision`, `SessionPermissionPolicy`, shell-aware `normalize_bash_command`, and `categorize_bash_command`. Default whitelist matches AC-1. Land unit tests for the normalizer and category parser. | AC-1, AC-3 | coding | - |
| task2 | Thread an `async request_tool_approval` parameter through `Session.chat → _handle_model_turn → run_tool`. Use a `_PermissionDenied` sentinel from `run_tool` to drive the per-call denial reply path in `_handle_model_turn`. | AC-2, AC-3.3, AC-4 | coding | task1 |
| task3 | Implement the denial protocol in `_handle_model_turn`: append `Message(role="tool", content="<user_denied: ...>", tool_call_id=...)` for every denied call before processing the next call in the same turn. Convert approval-callback exceptions to deny-with-reason. Land protocol tests for single-call and multi-call turns. | AC-4 | coding | task2 |
| task4 | Add `Config` fields and env-var loading: `permission_gate_enabled` (always-on in v1; reserved field), `predict_before_call_enabled`, `self_discovery_enabled` (no-op in v1), `permission_mode` ∈ {strict, auto_deny, auto_allow_safe} with non-interactive defaults. | AC-5, AC-6, AC-10 | coding | task1 |
| task5 | Augment `get_system_prompt()` in `src/config.py` to append the predict-before-call clause when `Config.predict_before_call_enabled` is `True`; leave the legacy prompt unchanged otherwise. The clause asks the main LLM to write a visible 1–2 sentence prediction of side effects before emitting any tool call. Do NOT introduce any extra LLM round-trip or mutate `Session.history` from the gate. | AC-5 | coding | task4 |
| task6 | Implement TUI `ToolApprovalScreen` ModalScreen with three buttons, escape-to-deny binding, and integration via `push_screen_wait` from `_generate_response`'s closure. Multiline-bash commands disable the "allow session" button. | AC-2.1, AC-3.1 | coding | task2 |
| task7 | Implement CLI `[A/S/D]` prompt on top of `async_input`: EOF and KeyboardInterrupt resolve to deny; multiline commands accept only `[A/D]`. | AC-2.2, AC-3.1 | coding | task2 |
| task8 | Implement non-interactive defaults: `--once` and the evaluation runner pass a closure that returns deny immediately; `auto_allow_safe` silently allows the AC-1 whitelist and denies everything else. The predict-before-call flag affects only the system prompt and is independent of `permission_mode`. | AC-10 | coding | task4 |
| task9 | Implement audit log: write `[permission]` `Memory_Unit` entries through the existing JSONL accessor; ensure rehydration filters them out of model-visible context. | AC-9 | coding | task2 |
| task10 | Implement lazy tool/embedding rebuild on `Session.chat()` entry: recursive `skills_dir` fingerprint (deepest-mtime + file count), no-op when unchanged, rebuild `USE_TOOL_SEARCH` embeddings simultaneously. | AC-7 | coding | - |
| task11 | Document `Session.fork`/`resume`/`clear_history` semantics for the in-memory whitelist (AC-8.1) and confirm test coverage. | AC-8.1 | coding | task1 |
| task12 | Land the AC-11 test matrix: allow_once/allow_session/deny on each gated tool, multi-call independence, normalizer+category parser cases, CLI EOF/KbdInterrupt, predict isolation/timeout/skip, lazy-rebuild no-op, audit JSONL presence + history absence, non-interactive auto_deny + auto_allow_safe. | AC-11 | coding | task3, task5, task6, task7, task8, task9, task10 |
| task13 | Codex review of bash command-category parser corner cases (heredocs, `bash -c "..."`, aliases, sourced scripts, `command` builtin, `command -v`, redirection-only commands, comments, command substitution inside quoted regions). Surface any cases the AUDIT category should defensively classify as `unknown`. | AC-3.1 | analyze | task1 |

## Claude-Codex Deliberation

### Agreements

- Centralizing permission enforcement at `Session.run_tool()` is the correct chokepoint; it is the single dispatch point for built-in, MCP, and skill tools.
- A denied tool call MUST still produce a valid `tool` reply message bound to the original `tool_call_id`, or the next API request becomes protocol-invalid.
- `bash` requires finer granularity than tool-name-only; the v1 resolution is exact normalized-command-string matching with audit-only category metadata.
- MCP tools are gated by full `mcp__{server}__{tool}` name in v1; server-level approval is a future enhancement.
- The approval flow must produce a structured `PermissionDecision` (decision, reason, approval_key, visible_summary), not a string.
- Predict-before-call must be isolated from `self.history`, use `tool_choice="none"`, and never produce hidden chain-of-thought.
- The lazy tool-set rebuild must rebuild the `USE_TOOL_SEARCH` embedding map at the same moment to avoid stale embeddings.
- Per-session whitelist persistence across resume/fork/clear_history is intentionally out of scope for v1 (in-memory only).
- Non-interactive mode (`--once`, evaluation runner) must default to `auto_deny` so unattended runs never hang.
- `[permission]` audit entries are transcript-only and must not enter model-visible history.
- The agent must NEVER write to `src/tool.py` at runtime; even when self-discovery lands later, the artifact is an audit-only git patch the user merges manually.
- Approval enforcement runs BEFORE the Landlock sandbox; approval never bypasses sandbox policy.

### Resolved Disagreements

- Tool-name-only whitelist: Codex flagged this as too coarse for `bash`. Resolution: gate `bash` by exact normalized command string (DEC-3); other tools by full name (`mcp__server__tool` for MCP). A category parser still runs but is audit-only and never affects approval matching.
- Schema synthesis writing to `src/tool.py`: Codex flagged this as unsafe and incomplete (schemas need executors). Resolution: defer self-discovery (AC-6) entirely to a follow-up release per DEC-1. The env flag is reserved as a no-op in v1.
- `bash:git` style names containing `:`: invalid for OpenAI function-name regex. Resolution: when self-discovery later lands, the discovered tool name will be `discovered_bash_<category>`, matching `^[a-zA-Z0-9_-]+$`.
- `_handle_model_turn` ↔ `run_tool` denial signaling: Codex flagged that a plain denial string is weak for audit/UI. Resolution: `run_tool` raises a `_PermissionDenied` sentinel (or returns a tagged result) so `_handle_model_turn` can construct a structured `tool` reply with the bound `tool_call_id` and emit the audit entry.
- Predict-before-call protocol risk: Codex flagged that reusing normal session history might create protocol-invalid requests. Resolution (refined per CMT-1, DEC-8): predict-before-call is implemented as a system-prompt augmentation rather than an isolated extra LLM round-trip. The main LLM is asked, via its system prompt, to emit a brief prediction in its visible reasoning/content stream before a tool call; the runtime gate makes no extra API request and never mutates `Session.history`. The original protocol-validity concern dissolves because there is no separate request to be invalid.
- MCP fingerprinting in lazy rebuild: Codex flagged that MCP connection sets are fragile. Resolution: the MCP set is captured once at session init (matches current behavior); mid-session reconnection is out of scope; the lazy fingerprint covers `skills_dir` only.
- Approval visibility semantics: Codex asked whether gated tools should be hidden from the model. Resolution per DEC-2: execution-only gating; the model sees the full advertised tool list.
- Whitespace normalization for the approval key: Codex flagged that naive collapsing is unsafe inside quotes/here-docs. Resolution: shell-aware normalization that preserves quoted regions, escaped whitespace, here-doc bodies, and command-separator newlines; multiline commands are eligible for `allow_once` only per DEC-7.
- Initial whitelist composition: Codex offered `read_file`/`Glob`/`Grep`/`skill__*` or a broader low-risk-bash list. Resolution per DEC-4: read-only only.

### Convergence Status

- Final Status: `converged`
- Convergence rounds executed: 4 (Codex first-pass + 3 reasonability rounds + 1 post-decision sanity check; the final Codex round returned no DISAGREE, no REQUIRED_CHANGES, no UNRESOLVED).
- Refinement round (CMT-1, DEC-8): the predict-before-call design changed from an isolated extra LLM round-trip to a system-prompt augmentation per the user's annotation; AC-5 and downstream sections were rewritten and AC-4's `visible_summary` field was demoted to a reserved field (always `None` in v1). DEC-5 (30-second timeout) was superseded since no extra round-trip exists. No new disagreements introduced.

## Pending User Decisions

All user decisions have been resolved during plan generation. None remain `PENDING`.

- DEC-1: Should self-discovery (AC-6) ship in v1?
  - Claude Position: include AC-6 with sidecar registry and audit-only patch generation.
  - Codex Position: defer; v1 surface is large enough without it.
  - Tradeoff Summary: shipping AC-6 in v1 doubles the implementation surface and adds LLM-generated-schema risk; deferring keeps v1 focused on the gate and protocol invariants.
  - Decision Status: User decision — defer AC-6 to a follow-up release. Env flag `GEM_CODE_SELF_DISCOVERY` is reserved as a no-op in v1.
- DEC-2: Should gated tools remain visible in the model's advertised tool list, or be filtered out?
  - Claude Position: keep visible (execution-only gating).
  - Codex Position: open question (raised in round 2 / 3 UNRESOLVED).
  - Tradeoff Summary: visible is simpler and matches OpenAI semantics; filtering reduces what the model can plan but adds a per-approval rebuild and embedding reshuffle.
  - Decision Status: User decision — keep visible (execution-only gate).
- DEC-3: For bash, what does "allow this session" mean?
  - Claude Position: category-only (parsed `git`, `npm`, etc.).
  - Codex Position: at minimum category + safe-prefix list for higher-risk buckets like `git`, `npm`, `python`.
  - Tradeoff Summary: category-only is simpler but `allow_session` for `git` would silently approve `git push` and `git reset --hard`; exact-command match is the safest of the three options.
  - Decision Status: User decision — exact-command match. The category parser remains as audit-only metadata; matching is on the normalized command string.
- DEC-4: Initial whitelist composition.
  - Claude Position: read-only only.
  - Codex Position: read-only only (recommended) or include low-risk bash subsets.
  - Tradeoff Summary: read-only only is a safer first cut and keeps the heuristics out of v1; the low-risk-bash list is a usability optimization that can be added later via config.
  - Decision Status: User decision — read-only only.
- DEC-5: Predict-before-call timeout — hard requirement vs target.
  - Claude Position: target (any reasonable timeout in 15–60s with documented fallback).
  - Codex Position: hard requirement is acceptable.
  - Tradeoff Summary: hard 30s makes test assertions deterministic; a target lets the implementation tune for the chosen API provider.
  - Decision Status: SUPERSEDED by DEC-8. The 30-second timeout assumed an isolated predictor LLM round-trip; under the refined design (DEC-8), no such round-trip exists and there is nothing to time out. The DEC-5 record is preserved for traceability.
- DEC-6: Approval modal/prompt inactivity timeout.
  - Claude Position: no timeout (block until user).
  - Codex Position: open question.
  - Tradeoff Summary: no timeout means an unattended session blocks indefinitely (already mitigated by `auto_deny` non-interactive default); a timeout adds modal-scheduling complexity.
  - Decision Status: User decision — no inactivity timeout. `auto_deny` covers unattended runs.
- DEC-7: Multiline bash and "allow session".
  - Claude Position: open after Codex round 5 surfaced the corner case.
  - Codex Position: either disable session-allow for multiline, or preserve newlines verbatim in the approval key.
  - Tradeoff Summary: disabling avoids subtle mismatches; preserving newlines is more powerful but easier to get wrong.
  - Decision Status: User decision — multiline bash is `allow_once` only; the modal disables the "allow session" button and the CLI prompt accepts only `[A/D]`.
- DEC-8: Predict-before-call mechanism (raised by CMT-1 during refinement).
  - Claude Position (initial plan): isolated extra LLM round-trip with `tool_choice="none"`, surfaced as `visible_summary` in the approval modal.
  - Codex Position: had no objection to either mechanism, only to the protocol risks of mutating `self.history`.
  - Tradeoff Summary: isolated predictor adds an extra API request and modal field but keeps the model's reasoning unchanged; system-prompt augmentation puts the prediction inside the model's own visible turn, matches the original draft requirement that "the model predicts what this modification will do, then thinks further, then makes the tool call", but cannot deny based on the prediction without the user reading the streamed output.
  - Decision Status: User decision — system-prompt augmentation. AC-5 reflects the new design; DEC-5 is superseded; AC-4's `visible_summary` field is reserved (always `None` in v1).

## Implementation Notes

### Code Style Requirements

- Implementation code and comments must NOT contain plan-specific terminology such as "AC-", "Milestone", "Step", "Phase", or similar workflow markers. Those terms exist for the plan document only.
- Use descriptive, domain-appropriate naming in code: `SessionPermissionPolicy`, `PermissionDecision`, `request_tool_approval`, `normalize_bash_command`, `_PermissionDenied`, etc.
- Module placement: new policy code lives in `src/permissions.py`; do not grow `src/session.py` with the policy itself, only the wiring that consumes it.
- Tests use pytest fixtures consistent with the existing patterns under `tests/` (no new conftest.py is required unless test fixtures genuinely need cross-file sharing).
- Audit-log content strings are stable, parseable formats: `[permission] <decision> <tool_name>[ command="<key>"] reason=<reason>[ audit_category=<category>]`. Treat them as a contract; downstream tooling may parse them.
- The `[permission]` content prefix is the only authoritative filter for keeping audit entries out of model-visible context. Add it as a constant in `src/permissions.py` and reuse it in the rehydration path.
- Predict-before-call is a system-prompt augmentation (AC-5, DEC-8); it has no runtime LLM call, no timeout, and no separate modal field. The clause text lives in `src/config.py` next to `get_system_prompt()`.


--- Original Design Draft Start ---

# Session-Scoped Tool Permission Gate With Predict And Self-Discovery

## Original Idea

为我的智能体的TUI模式增加以下新特性：
1.针对每一个会话增加工具白名单(初始的白名单由你选择，他们的执行应该较为安全)，白名单中的工具无需用户审核同意，而不在白名单中的工具调用需要用户审核同意才可以执行，审核时用户有三个选择：同意（仅同意本次命令），这个会话都同意（本会话之后调用该工具时自动同意，即这个工具已经被加入白名单），拒绝本命令。
2.增加可选特性，由环境变量控制是否开启：当模型要进行工具调用前，让模型预测这个修改会发生什么，再进行进一步思考，最后工具调用
3.针对当前的coding agent，增加工具自学习能力，在本地进行工具调用时，对调用的bash工具进行计数（例如：uv类，git类，或者别的命令行工具，注意，不是单独的一个命令，而是一类命令），如果设定的一段时间内，该类工具调用频率大于一个设定值（暂定最近一百次工具调用中调用该工具的次数大于等于5次），那么由大模型生成该模型的json schema，再加入到/home/tao/gem_code/src/tool.py中，成为新的可调用工具，生成git patch，暂停对话，由用户审查之后加入工具集。并且，每次用户发送消息到LLM前，应该重建可调用tools的集合，包括tool，mcp tool和skill tool。

## Primary Direction: Per-Session Permission Layer

### Rationale

Center the design on a single session-scoped policy object that gates every `run_tool` invocation with allow / deny / session-allow decisions. Predict-before-call and tool self-discovery are folded in as additional consumers of the same gating hook, keeping a single chokepoint for safety and avoiding three independent feature bolt-ons that each touch the dispatch path.

### Approach Summary

Introduce a `SessionPermissionPolicy` dataclass that lives on `Session` for the lifetime of the session and mediates every tool call:

1. **Policy object**. Following the `SecuritySettings` precedent in `src/security.py`, store the session whitelist (initial safe defaults: `read_file`, `Glob`, `Grep`), a deque-based rolling window of recent tool calls, and a small registry of session-allow / deny decisions. Construction happens in `Session.__init__()` so the policy ages with the session.

2. **Permission gate at the dispatch boundary**. `Session.run_tool()` (currently `src/session.py:795-871`, a flat if/elif over `name`) gains a single guard at the top: if the tool is whitelisted, execute as today; otherwise invoke an injected approval callback that returns one of `allow_once` / `allow_session` / `deny`. `allow_session` mutates the policy object's whitelist so the same call category passes silently next time. `deny` returns a structured error string back to the model loop.

3. **Predict-before-call as a policy consumer**. A `GEM_CODE_PREDICT_BEFORE_CALL` env flag (loaded the same way `USE_TOOL_SEARCH` is in `src/config.py`) toggles a pre-execute step inside `run_tool` that calls a single-shot LLM completion ("Given this tool name and these arguments, predict observable side effects") and surfaces the prediction through the same approval callback so the user sees prediction text alongside the three-choice prompt.

4. **Self-discovery as another policy consumer**. The same gate counts each bash invocation by command category (parsed off the head of `command`, with mapping `uv`, `git`, `npm`, etc. — fragile parsing acknowledged as a risk). The deque holds the last 100 calls; when any category crosses the configured threshold (default ≥5 in 100), the policy emits a `discovery_proposed` event. A small curator routine (modeled on `Context_Manager.autocompaction()` in `src/context_manager.py`, which already uses `chat_one_step()` to generate persisted artifacts) calls the LLM to synthesize an OpenAI-compatible JSON schema, writes a git patch against `src/tool.py`, pauses the conversation, and waits for user review.

5. **Tool-set rebuild before each user turn**. Today `Session._all_tools` is built once during init (`src/session.py:93,249,302`). Add a `_rebuild_tools()` step at the top of `Session.chat()` that re-runs `clone_tools()` plus skill / MCP / discovered-tool merges, so newly accepted self-discovered tools become callable on the next message without restarting the session.

6. **TUI surface**. The approval callback is wired through the existing `on_tool_start` / `on_tool_result` callback channel and the `ToolStartMessage` / `ToolResultMessage` Textual Messages already in `src/tui.py:546-559,1250-1304`. A new `ToolApprovalScreen` ModalScreen modeled on `HelpScreen` at `src/tui.py:930` hosts the three buttons; a `ToolDiscoveryScreen` modal hosts the patch-review flow. CLI mode falls back to a printed prompt that reads `[A]llow once / [S]ession-allow / [D]eny` from stdin, so the gate works outside the TUI as well.

### Objective Evidence

- `src/session.py:795-871` — `run_tool()` is the single dispatch point for every built-in, MCP, and skill tool; today a straightforward if/elif with no policy layer, giving a clean integration seam.
- `src/security.py:97-116,208` — `SecuritySettings` frozen dataclass plus `build_command_policy()` are direct precedent for a session-level policy object built from environment variables.
- `src/session.py:679-744` — `_handle_model_turn()` already orchestrates the tool loop and exposes `on_tool_start` / `on_tool_result` callbacks (lines 721-730), the natural carriers for an approval-callback parameter.
- `src/tui.py:930` (HelpScreen) and `src/tui.py:546-559,1250-1304` (Tool*Message handlers) — proven modal-screen and event-message patterns the approval UI plugs into without new infrastructure.
- `src/tool.py:46-278,293` — `TOOLS` constant, `clone_tools()` factory, plus skill/MCP merge points at `src/session.py:241-266,302` mark exactly where dynamically discovered tools are appended.
- `src/session.py:804-812` — MCP dispatch is already isolated, so the gate wraps `mcp_client.call_tool()` with the same shape as the bash/file branches.
- `src/context_manager.py:26-95` — `autocompaction()` already drives an LLM via `chat_one_step()` to generate a persisted artifact; this is the working precedent for both the predict prompt and the schema-synthesis prompt the policy needs.
- `src/config.py` — env-variable feature flag plumbing (`USE_TOOL_SEARCH`, `GEM_CODE_SECURITY_*`) gives the loading pattern for `GEM_CODE_PREDICT_BEFORE_CALL` and `GEM_CODE_TOOL_APPROVAL`.
- `README.md:406-411` — project philosophy explicitly names "Agent对人类的安全性" and observability, aligning with the gate-first framing.

### Known Risks

- **Approval friction.** Prompting on every never-seen tool can choke the chat flow; mitigated by the session-allow path and conservative initial whitelist defaults.
- **Bash category parsing.** Splitting on the first token misclassifies wrappers, aliases, and piped chains (`env X=1 git status`, `bash -c "..."`); needs explicit normalization rules and an "unknown" bucket.
- **Async modal coordination.** Pausing `run_tool` for an approval modal must not freeze Textual's event loop; the TUI must use awaitable `push_screen_wait` (or equivalent) and the CLI fallback must not deadlock on stdin during streaming.
- **LLM-generated schema correctness.** Synthesized JSON schemas can hallucinate parameters or miss types; the patch-review pause is the safety net but adds friction and must validate the schema against the OpenAI tool-call contract before staging.
- **Patch application against a live `tool.py`.** If the user has uncommitted edits in `src/tool.py`, the curator's patch may conflict; the curator should refuse to apply when the working tree is dirty in the relevant file and surface a clear message instead of merging.
- **Predict-before-call latency cost.** An extra LLM round-trip per tool call doubles per-call latency when enabled; the env-flag default-off plus a per-tool-name prediction cache mitigate routine cases.

## Alternative Directions Considered

### Alt-1: Three-Stage Tool Pipeline
- Gist: Wrap `Session.run_tool()` in a four-stage pipeline (predict → approve → execute → observe), each stage a pluggable async handler with a standard interface and an environment toggle. The whitelist gating becomes the approve handler, prediction-before-call becomes the predict handler, and usage counting plus self-discovery hook the observe handler — three features composed instead of colliding inside one method.
- Objective Evidence:
  - `src/session.py:679-745` (`_handle_model_turn`) and `src/session.py:795-871` (`run_tool`) provide a clear interception boundary.
  - `src/context_manager.py:26-95` already shows pluggable, independently togglable stages (`microcompaction`, `autocompaction`).
  - `src/security.py:97-135` `SecuritySettings` with environment-driven flags is precedent for stage toggles.
  - `src/tool.py:46-278,293` (`TOOLS` constant and `clone_tools()`) shows where dynamically discovered tools would inject into the execute stage.
- Why not primary: The pipeline framework adds 400-600 LOC of new abstraction (`ToolInvocationPipeline`, `ToolStageHandler`) that the primary's single-policy chokepoint already covers with smaller surface and clearer reasoning.

### Alt-2: Event Bus + Interceptors
- Gist: Introduce a typed in-process event bus emitting `tool_call_requested` / `approved` / `executing` / `completed` events, with whitelist gating, prediction, counting, and schema synthesis each registered as independent subscribers. The TUI subscribes to the same bus for live UI feedback, decoupling `run_tool` from any UI knowledge.
- Objective Evidence:
  - `src/session.py:380-407` already routes lifecycle events to multiple consumers via `on_tool_start` / `on_tool_result` / `on_turn_end` callbacks.
  - `src/tui.py:537-559` (`ResponseMessage`, `ToolStartMessage`, `ToolResultMessage`) already models event-driven decoupling between session and UI.
  - `src/mcp_client.py:99-113` demonstrates the `AsyncExitStack` async lifecycle precedent an event bus would need.
  - `src/session.py:717-741` is a single dispatch boundary that keeps integration localized.
- Why not primary: The existing callback plumbing already provides the multi-subscriber semantics, so a parallel event bus duplicates the abstraction rather than extending the codebase's current style.

### Alt-3: TUI-Native Modal Approval
- Gist: Treat the three features as TUI components first — a `ToolApprovalScreen` ModalScreen for the three-choice prompt, a sidebar block for live tool-call frequency counters, and a `ToolDiscoveryScreen` for staged JSON-schema patches — with thin model classes backing each. UI is the design's anchor; runtime hooks are derived from what each screen needs.
- Objective Evidence:
  - `src/tui.py:930` (HelpScreen) provides the `push_screen` / dismiss + Button precedent.
  - `src/tui.py:546-559,1250,1275` shows how session events already flow into the TUI via Textual Messages.
  - `src/tui.py:770` (`Sidebar.update_context_usage`) shows the reactive-state pattern for live counter displays.
  - `src/tui.py:131,478` (`OptimizedStreamingWidget`, `ChatArea`) confirms Textual rendering can host approval and patch-review surfaces without performance work.
- Why not primary: Anchoring on UI risks letting the TUI shape policy invariants instead of the other way around, and the same components compose on top of the primary's permission layer with less risk of duplicating logic in CLI mode.

### Alt-4: Filesystem-Backed Persistent Policy
- Gist: Persist the per-session whitelist, the rolling 100-call window, and discovered-tool patches as JSONL files under `~/.gem_code/projects/{encoded_cwd}/`, reusing the `JsonlRandomAccess` accessor pattern already proven by session memory and context compaction. The runtime layer becomes a thin reader/writer on top of these accessors, so state is durable, auditable, and survives restarts.
- Objective Evidence:
  - `src/memory.py:93-202` (`JsonlRandomAccess`) provides O(1) random access and append-only durability already used for transcripts.
  - `src/session_manager.py:12-64` establishes the `~/.gem_code/projects/{encoded_cwd}/` layout the new files would slot into.
  - `src/context_manager.py:26-95` (`Context_Manager`) demonstrates the accessor pattern for session-scoped state, exactly the shape the new state needs.
  - `README.md:42,141-144,300-318` documents `MEMORY_COMPACTION_PATH` and the `JsonlRandomAccess` design as project conventions.
- Why not primary: Persistence is orthogonal to the gating logic — durable JSONL stores can be added under the primary's policy object once the gate semantics are correct, but choosing persistence as the design anchor underweights the runtime decision flow.

### Alt-5: Reflection + Self-Discovery Subagent
- Gist: Encapsulate the predict-before-call step and the new-tool synthesis flow as a lightweight in-process "tool-curator" subagent that consumes the call trace, runs the prediction prompt, generates JSON schemas via `chat_one_step` (the same pattern `autocompaction` already uses), and stages git patches for user review. Self-improvement becomes a meta-agent layered above the main session loop.
- Objective Evidence:
  - `src/context_manager.py:60-95` (`autocompaction`) already drives LLM-generated artifacts via `chat_one_step()` and persists them — direct precedent for curator-generated schemas.
  - `src/tool.py:26-43` (`_object_schema`) plus the `TOOLS` constant at `src/tool.py:46-278` show the schema shape the curator must emit.
  - `src/session.py:241-266` (skill discovery during init) plus `clone_tools()` at `src/tool.py:293` provide the tool-set rebuild seam.
  - `README.md:384` lists "subagent" as an explicit TODO, confirming the evolution path.
- Why not primary: A standalone curator subagent overcommits to meta-agent infrastructure when only two narrow LLM calls (predict + schema) are needed; folding both into the primary's permission layer keeps surface small and avoids splitting safety-critical state across components.

## Synthesis Notes

The primary already designates the gating chokepoint and treats predict and self-discovery as consumers of the same hook, which leaves the alternatives as decorations rather than competitors. Alt-1's pipeline framing is worth keeping in reserve as a folding move if a fourth concern (e.g., observe-after-call telemetry) lands later — the policy object can grow into a small handler registry without changing call sites. Alt-2's event-bus framing is overkill, but its instinct that the TUI should subscribe rather than be called directly is right, so the primary should publish through the existing `post_message` channel rather than reaching into UI widgets. Alt-3's `ToolApprovalScreen` and `ToolDiscoveryScreen` modal shapes are the natural UI surface and should be lifted verbatim; the primary's policy object is what they bind to. Alt-4's `JsonlRandomAccess`-backed stores are the right durability story for the rolling window and the discovered-tool patch log — adopt them once the in-memory shape stabilizes, with no need to restructure the gate. Alt-5's `chat_one_step()` pattern from `autocompaction` is the right mechanism for both prediction and schema synthesis prompts; the curator's identity as a "subagent" is unnecessary, but its two prompt templates are exactly what the primary's predict-and-discover consumers should call.

--- Original Design Draft End ---
