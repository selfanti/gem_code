from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent


@dataclass(frozen=True)
class TaskDefinition:
    """Single source of truth for one Harbor task in the local smoke matrix.

    Keeping task definitions in Python instead of hand-maintained directories
    serves two practical goals:

    1. the evaluation suite can be regenerated deterministically after we tweak
       prompts, tests, or fixture code, and
    2. the rationale for each task can live next to the source template instead
       of being spread across dozens of mostly boilerplate files.
    """

    name: str
    title: str
    difficulty: str
    category: str
    tags: list[str]
    instruction: str
    workspace_files: dict[str, str]
    test_code: str


def _task_toml(task: TaskDefinition) -> str:
    tags = ", ".join(f'"{tag}"' for tag in task.tags)
    return dedent(
        f"""
        version = "1.0"

        [metadata]
        author_name = "gem-code"
        author_email = "local@example.com"
        difficulty = "{task.difficulty}"
        category = "{task.category}"
        tags = [{tags}]
        expert_time_estimate_min = 8.0
        junior_time_estimate_min = 20.0

        [verifier]
        timeout_sec = 300.0

        [agent]
        timeout_sec = 600.0

        [environment]
        build_timeout_sec = 30.0
        cpus = 1
        memory_mb = 2048
        storage_mb = 10240
        gpus = 0
        allow_internet = true
        mcp_servers = []

        [verifier.env]

        [solution.env]
        """
    ).strip() + "\n"


def _test_sh() -> str:
    return dedent(
        """
        #!/usr/bin/env bash
        set -euo pipefail

        # Harbor's verifier contract is "run tests and write a scalar reward".
        # We intentionally execute pytest from the copied gem-code checkout so
        # every task reuses the same interpreter and dependency set prepared by
        # the installed-agent setup step.
        WORKSPACE_PATH="${HARBOR_WORKSPACE_PATH:-/workspace}"
        TESTS_PATH="${HARBOR_TESTS_PATH:-/tests}"
        VERIFIER_LOGS_PATH="${HARBOR_VERIFIER_LOGS_PATH:-/logs/verifier}"

        cd "${WORKSPACE_PATH}"
        export PYTHONPATH="${WORKSPACE_PATH}/evaluation_fixture/src${PYTHONPATH:+:$PYTHONPATH}"

        if uv run pytest -q "${TESTS_PATH}/test_outputs.py"; then
          echo 1 > "${VERIFIER_LOGS_PATH}/reward.txt"
        else
          echo 0 > "${VERIFIER_LOGS_PATH}/reward.txt"
          exit 1
        fi
        """
    ).strip() + "\n"


def _environment_readme() -> str:
    return dedent(
        """
        This task is executed by gem-code's custom local Harbor environment.

        The directory only needs to exist because Harbor task validation expects
        an `environment/` folder. The actual runtime setup happens in
        `evaluation/local_harbor_environment.py`, which copies the current
        repository checkout and then overlays `workspace_template/` into
        `/workspace/evaluation_fixture`.
        """
    ).strip() + "\n"


TASKS: list[TaskDefinition] = [
    TaskDefinition(
        name="config-defaults",
        title="Optional Config Defaults",
        difficulty="easy",
        category="software_engineering",
        tags=["python", "config", "defaults"],
        instruction=dedent(
            """
            Fix the configuration loader in `/workspace/evaluation_fixture/src/config_loader.py`.

            Requirements:
            - Keep `OPENAI_API_KEY` and `OPENAI_BASE_URL` required.
            - Default `skills_dir` to `{workdir}/.agents` when `SKILLS_DIR` is blank.
            - Treat `MCP_CONFIG_PATH` as optional and return `None` when blank.
            - Default `memory_compaction_path` to `~/.gem_code/projects` when blank.
            - Do not change the tests.
            """
        ).strip(),
        workspace_files={
            "src/config_loader.py": dedent(
                '''
                from dataclasses import dataclass
                from pathlib import Path
                import os


                @dataclass
                class Config:
                    api_key: str
                    base_url: str
                    model: str
                    workdir: str
                    skills_dir: str | None
                    mcp_config_path: str | None
                    memory_compaction_path: str | None


                def _expand_path(path: str | None) -> str | None:
                    if not path:
                        return None
                    return str(Path(path).expanduser())


                def load_config() -> Config:
                    """Load runtime configuration.

                    This intentionally starts from a buggy baseline: optional
                    integrations are left unset even though the application is
                    expected to provide stable defaults for local development.
                    """

                    api_key = os.getenv("OPENAI_API_KEY")
                    base_url = os.getenv("OPENAI_BASE_URL")
                    model = os.getenv("OPENAI_MODEL", "MiniMax-M2.5")
                    workdir = str(Path(os.getenv("WORKDIR") or os.getcwd()).expanduser())

                    skills_dir = _expand_path(os.getenv("SKILLS_DIR"))
                    mcp_config_path = _expand_path(os.getenv("MCP_CONFIG_PATH"))
                    memory_compaction_path = _expand_path(os.getenv("MEMORY_COMPACTION_PATH"))

                    if not api_key:
                        raise ValueError("OPENAI_API_KEY environment variable is not set")
                    if not base_url:
                        raise ValueError("OPENAI_BASE_URL environment variable is not set")

                    return Config(
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        workdir=workdir,
                        skills_dir=skills_dir,
                        mcp_config_path=mcp_config_path,
                        memory_compaction_path=memory_compaction_path,
                    )
                '''
            ).strip()
            + "\n",
        },
        test_code=dedent(
            """
            import os

            from config_loader import load_config


            def test_load_config_uses_repo_friendly_defaults(monkeypatch, tmp_path) -> None:
                monkeypatch.setenv("OPENAI_API_KEY", "test-key")
                monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
                monkeypatch.setenv("WORKDIR", str(tmp_path))
                monkeypatch.setenv("SKILLS_DIR", "")
                monkeypatch.setenv("MCP_CONFIG_PATH", "")
                monkeypatch.setenv("MEMORY_COMPACTION_PATH", "")

                config = load_config()

                assert config.skills_dir == os.path.join(str(tmp_path), ".agents")
                assert config.mcp_config_path is None
                assert config.memory_compaction_path == "~/.gem_code/projects"
            """
        ).strip()
        + "\n",
    ),
    TaskDefinition(
        name="context-rehydration",
        title="Context Rehydration",
        difficulty="medium",
        category="software_engineering",
        tags=["python", "memory", "resume"],
        instruction=dedent(
            """
            Implement `rehydration()` in `/workspace/evaluation_fixture/src/context_manager.py`.

            Requirements:
            - Always start the restored history with the provided system prompt.
            - Preserve the latest summary after the most recent compaction boundary.
            - Keep a small slice of recent messages from before the boundary.
            - Keep recent messages after the boundary.
            - Do not edit the tests.
            """
        ).strip(),
        workspace_files={
            "src/context_manager.py": dedent(
                '''
                from dataclasses import dataclass


                @dataclass
                class Message:
                    role: str
                    content: str


                def _to_message(unit: dict) -> Message | None:
                    if unit.get("type") != "message":
                        return None
                    return Message(role=unit["role"], content=unit.get("content", ""))


                def rehydration(
                    units: list[dict],
                    system_prompt: str,
                    recent_messages_before_boundary: int = 3,
                    recent_messages_after_boundary: int = 3,
                ) -> list[Message]:
                    """Rebuild an in-memory history from persisted transcript units.

                    The current implementation is intentionally incomplete: it
                    only returns messages after the compaction boundary and drops
                    both the summary and the useful context immediately before
                    compaction.
                    """

                    boundary_index = -1
                    for index, unit in enumerate(units):
                        if unit.get("type") == "compact_boundary":
                            boundary_index = index

                    restored = [Message(role="system", content=system_prompt)]

                    tail = units[boundary_index + 1 :]
                    for unit in tail[-recent_messages_after_boundary:]:
                        message = _to_message(unit)
                        if message is not None:
                            restored.append(message)

                    return restored
                '''
            ).strip()
            + "\n",
        },
        test_code=dedent(
            """
            from context_manager import rehydration


            def test_rehydration_restores_summary_and_recent_context() -> None:
                units = [
                    {"type": "message", "role": "system", "content": "old system"},
                    {"type": "message", "role": "user", "content": "open README"},
                    {"type": "message", "role": "assistant", "content": "reading files"},
                    {"type": "message", "role": "tool", "content": "README contents"},
                    {"type": "compact_boundary"},
                    {"type": "summary", "content": "summary text"},
                    {"type": "message", "role": "user", "content": "continue after compaction"},
                ]

                restored = rehydration(
                    units,
                    system_prompt="system prompt",
                    recent_messages_before_boundary=3,
                    recent_messages_after_boundary=3,
                )

                assert restored[0].role == "system"
                assert restored[0].content == "system prompt"
                assert any(message.role == "assistant" and "summary text" in message.content for message in restored)
                assert any(message.role == "tool" and message.content == "README contents" for message in restored)
                assert any(message.role == "user" and message.content == "continue after compaction" for message in restored)
            """
        ).strip()
        + "\n",
    ),
    TaskDefinition(
        name="context-token-estimation",
        title="Context Token Estimation",
        difficulty="medium",
        category="software_engineering",
        tags=["python", "context", "tokens"],
        instruction=dedent(
            """
            Improve token estimation in `/workspace/evaluation_fixture/src/context_usage.py`.

            Requirements:
            - Use a better heuristic than raw character count.
            - CJK-heavy text should estimate close to one token per character.
            - ASCII-heavy text should estimate closer to one token per four characters.
            - Message estimates should include small framing overhead.
            - Do not edit tests.
            """
        ).strip(),
        workspace_files={
            "src/context_usage.py": dedent(
                '''
                import math
                import re
                from dataclasses import dataclass


                @dataclass
                class Message:
                    role: str
                    content: str
                    tool_call_id: str | None = None


                def estimate_text_tokens(text: str) -> int:
                    """Very naive baseline that currently overestimates everything."""
                    return len(text)


                def estimate_message_tokens(message: Message) -> int:
                    return estimate_text_tokens(message.role) + estimate_text_tokens(message.content)
                '''
            ).strip()
            + "\n",
        },
        test_code=dedent(
            """
            from context_usage import Message, estimate_message_tokens, estimate_text_tokens


            def test_estimate_text_tokens_uses_language_aware_heuristic() -> None:
                assert estimate_text_tokens("abcdefgh") == 2
                assert estimate_text_tokens("你好世界") == 4


            def test_estimate_message_tokens_includes_small_overhead() -> None:
                estimate = estimate_message_tokens(Message(role="user", content="abcdefgh"))
                assert estimate >= 8
            """
        ).strip()
        + "\n",
    ),
    TaskDefinition(
        name="tool-display-formatting",
        title="Tool Display Formatting",
        difficulty="easy",
        category="software_engineering",
        tags=["python", "tui", "formatting"],
        instruction=dedent(
            """
            Beautify the tool display helpers in `/workspace/evaluation_fixture/src/tool_rendering.py`.

            Requirements:
            - `format_tool_start()` should produce a compact multi-line layout.
            - Arguments should be rendered in a stable order so snapshots stay deterministic.
            - Very long values should be truncated with an ellipsis.
            - `format_tool_result()` should include the tool name and a short body preview.
            """
        ).strip(),
        workspace_files={
            "src/tool_rendering.py": dedent(
                '''
                def format_tool_start(tool_name: str, args: dict) -> str:
                    """Return a string for the "tool started" event.

                    The current output is technically readable but too noisy for
                    a TUI because dict ordering and long values make the line
                    jitter between renders.
                    """

                    return f"{tool_name}: {args}"


                def format_tool_result(tool_name: str, result: str) -> str:
                    return result
                '''
            ).strip()
            + "\n",
        },
        test_code=dedent(
            """
            from tool_rendering import format_tool_result, format_tool_start


            def test_format_tool_start_is_stable_and_multiline() -> None:
                rendered = format_tool_start(
                    "bash",
                    {
                        "description": "run tests",
                        "command": "pytest -q " + "x" * 120,
                    },
                )
                assert rendered.startswith("[tool] bash")
                assert "\\n  command:" in rendered
                assert "\\n  description:" in rendered
                assert "..." in rendered


            def test_format_tool_result_mentions_tool_name() -> None:
                rendered = format_tool_result("read_file", "line1\\nline2")
                assert rendered.startswith("[result] read_file")
                assert "line1" in rendered
            """
        ).strip()
        + "\n",
    ),
    TaskDefinition(
        name="tui-startup-guard",
        title="Headless TUI Guard",
        difficulty="easy",
        category="software_engineering",
        tags=["python", "tui", "startup"],
        instruction=dedent(
            """
            Fix `_should_launch_tui()` in `/workspace/evaluation_fixture/src/main.py`.

            The function should only allow the TUI to start when both stdin and
            stdout are attached to an interactive terminal.
            """
        ).strip(),
        workspace_files={
            "src/main.py": dedent(
                '''
                def _should_launch_tui(*, stdin_is_tty: bool, stdout_is_tty: bool) -> bool:
                    """Return whether the environment can support a full-screen TUI.

                    This buggy baseline lets the UI start even when only one side
                    of the terminal is interactive, which mirrors the classic
                    "TUI flashes and exits" failure mode.
                    """

                    return stdin_is_tty or stdout_is_tty
                '''
            ).strip()
            + "\n",
        },
        test_code=dedent(
            """
            from main import _should_launch_tui


            def test_tui_requires_interactive_stdin_and_stdout() -> None:
                assert _should_launch_tui(stdin_is_tty=True, stdout_is_tty=True) is True
                assert _should_launch_tui(stdin_is_tty=False, stdout_is_tty=True) is False
                assert _should_launch_tui(stdin_is_tty=True, stdout_is_tty=False) is False
            """
        ).strip()
        + "\n",
    ),
    TaskDefinition(
        name="jsonl-index-rebuild",
        title="JSONL Index Rebuild",
        difficulty="medium",
        category="software_engineering",
        tags=["python", "storage", "bugfix"],
        instruction=dedent(
            """
            Fix the offset rebuild logic in `/workspace/evaluation_fixture/src/jsonl_store.py`.

            Requirements:
            - Rebuilding the index must not duplicate the first line.
            - Loading messages from an existing file should preserve order.
            - Do not change the tests.
            """
        ).strip(),
        workspace_files={
            "src/jsonl_store.py": dedent(
                '''
                import json
                from pathlib import Path


                class JsonlRandomAccess:
                    def __init__(self, filepath: Path):
                        self.filepath = filepath
                        self.filepath.parent.mkdir(parents=True, exist_ok=True)
                        self.offsets: list[int] = []
                        if self.filepath.exists():
                            self.rebuild_memory_index()

                    def rebuild_memory_index(self) -> None:
                        """Buggy implementation that seeds the first offset twice."""

                        self.offsets = [0]
                        if not self.filepath.exists():
                            return

                        offset = 0
                        with open(self.filepath, "rb") as handle:
                            for line in handle:
                                self.offsets.append(offset)
                                offset += len(line)

                    def add_line(self, payload: str) -> None:
                        encoded = (payload + "\\n").encode("utf-8")
                        with open(self.filepath, "ab") as handle:
                            offset = handle.tell()
                            handle.write(encoded)
                        self.offsets.append(offset)

                    def load_messages(self) -> list[dict]:
                        messages: list[dict] = []
                        with open(self.filepath, "r", encoding="utf-8") as handle:
                            for offset in self.offsets:
                                handle.seek(offset)
                                line = handle.readline()
                                if line.strip():
                                    messages.append(json.loads(line))
                        return messages
                '''
            ).strip()
            + "\n",
        },
        test_code=dedent(
            """
            import json

            from jsonl_store import JsonlRandomAccess


            def test_rebuild_memory_index_does_not_duplicate_first_line(tmp_path) -> None:
                path = tmp_path / "session.jsonl"
                path.write_text(json.dumps({"content": "first"}) + "\\n" + json.dumps({"content": "second"}) + "\\n")

                store = JsonlRandomAccess(path)
                assert [item["content"] for item in store.load_messages()] == ["first", "second"]
            """
        ).strip()
        + "\n",
    ),
    TaskDefinition(
        name="responses-history-conversion",
        title="Responses History Conversion",
        difficulty="medium",
        category="software_engineering",
        tags=["python", "api", "tool-calls"],
        instruction=dedent(
            """
            Fix `history_to_responses_input()` in `/workspace/evaluation_fixture/src/responses_adapter.py`.

            Requirements:
            - Preserve assistant tool calls as `function_call` items.
            - Preserve tool outputs as `function_call_output` items.
            - Keep ordinary text messages in order.
            """
        ).strip(),
        workspace_files={
            "src/responses_adapter.py": dedent(
                '''
                from dataclasses import dataclass, field


                @dataclass
                class FunctionCall:
                    name: str
                    arguments: str


                @dataclass
                class ToolCall:
                    id: str
                    function: FunctionCall
                    type: str = "function"


                @dataclass
                class Message:
                    role: str
                    content: str | None = None
                    tool_calls: list[ToolCall] | None = field(default=None)
                    tool_call_id: str | None = None


                def history_to_responses_input(messages: list[Message]) -> list[dict]:
                    """Convert local history into Responses API items.

                    The current version drops tool call structure, which makes it
                    impossible to replay multi-step conversations after resume.
                    """

                    items: list[dict] = []
                    for message in messages:
                        items.append(
                            {
                                "type": "message",
                                "role": message.role,
                                "content": message.content or "",
                            }
                        )
                    return items
                '''
            ).strip()
            + "\n",
        },
        test_code=dedent(
            """
            from responses_adapter import FunctionCall, Message, ToolCall, history_to_responses_input


            def test_history_to_responses_input_preserves_tool_structure() -> None:
                items = history_to_responses_input(
                    [
                        Message(role="system", content="system"),
                        Message(role="user", content="list files"),
                        Message(
                            role="assistant",
                            content="",
                            tool_calls=[
                                ToolCall(
                                    id="call-1",
                                    function=FunctionCall(name="bash", arguments='{"command":"ls"}'),
                                )
                            ],
                        ),
                        Message(role="tool", content="README.md", tool_call_id="call-1"),
                    ]
                )

                assert items[0]["type"] == "message"
                assert any(item["type"] == "function_call" for item in items)
                assert any(item["type"] == "function_call_output" for item in items)
            """
        ).strip()
        + "\n",
    ),
    TaskDefinition(
        name="session-rebind",
        title="Session Transcript Rebind",
        difficulty="easy",
        category="software_engineering",
        tags=["python", "session", "state"],
        instruction=dedent(
            """
            Fix `_rebind_transcript()` in `/workspace/evaluation_fixture/src/session_manager.py`.

            Requirements:
            - When the manager points at a new transcript, the session object must
              receive the new `memory_access` and `transcript_path` too.
            - Do not change the tests.
            """
        ).strip(),
        workspace_files={
            "src/session_manager.py": dedent(
                '''
                from dataclasses import dataclass
                from pathlib import Path


                @dataclass
                class JsonlRandomAccess:
                    filepath: Path


                @dataclass
                class Session:
                    memory_access: JsonlRandomAccess
                    transcript_path: Path


                class SessionManager:
                    def __init__(self, storage_dir: Path):
                        self.storage_dir = storage_dir
                        self.session_id = "initial"
                        self.transcript_path = self.storage_dir / "initial.jsonl"
                        self.memory_access = JsonlRandomAccess(self.transcript_path)
                        self.session = Session(self.memory_access, self.transcript_path)

                    def _rebind_transcript(self, session_id: str) -> None:
                        self.session_id = session_id
                        self.transcript_path = self.storage_dir / f"{session_id}.jsonl"
                        self.memory_access = JsonlRandomAccess(self.transcript_path)
                '''
            ).strip()
            + "\n",
        },
        test_code=dedent(
            """
            from pathlib import Path

            from session_manager import SessionManager


            def test_rebind_transcript_updates_session_references(tmp_path: Path) -> None:
                manager = SessionManager(tmp_path)
                manager._rebind_transcript("next")

                assert manager.session.memory_access.filepath == tmp_path / "next.jsonl"
                assert manager.session.transcript_path == tmp_path / "next.jsonl"
            """
        ).strip()
        + "\n",
    ),
    TaskDefinition(
        name="mcp-config-fallbacks",
        title="MCP Config Fallbacks",
        difficulty="medium",
        category="software_engineering",
        tags=["python", "mcp", "config"],
        instruction=dedent(
            """
            Fix `load_mcp_config_from_env()` in `/workspace/evaluation_fixture/src/mcp_config.py`.

            Requirements:
            - `MCP_CONFIG` should take precedence and be parsed as inline JSON.
            - Otherwise load `MCP_CONFIG_PATH` if provided.
            - Otherwise search these fallback files in order:
              `~/.gem-code/mcp_config.json`
              `~/.config/gem-code/mcp.json`
              `./mcp_config.json`
            - Return `None` when nothing exists.
            """
        ).strip(),
        workspace_files={
            "src/mcp_config.py": dedent(
                '''
                import json
                import os
                from pathlib import Path


                def load_mcp_config_from_env(explicit_path: str | None = None):
                    """Load MCP config from the environment.

                    The current implementation only looks at one path and ignores
                    both inline JSON and documented fallback locations.
                    """

                    candidate = explicit_path or os.getenv("MCP_CONFIG_PATH")
                    if not candidate:
                        return None
                    path = Path(candidate).expanduser()
                    if not path.exists():
                        return None
                    return json.loads(path.read_text(encoding="utf-8"))
                '''
            ).strip()
            + "\n",
        },
        test_code=dedent(
            """
            import json
            from pathlib import Path

            from mcp_config import load_mcp_config_from_env


            def test_inline_config_has_highest_priority(monkeypatch, tmp_path: Path) -> None:
                monkeypatch.setenv("MCP_CONFIG", '{"mcpServers":{"inline":{"type":"remote"}}}')
                assert load_mcp_config_from_env() == {"mcpServers": {"inline": {"type": "remote"}}}


            def test_fallback_files_are_checked_in_documented_order(monkeypatch, tmp_path: Path) -> None:
                monkeypatch.delenv("MCP_CONFIG", raising=False)
                monkeypatch.delenv("MCP_CONFIG_PATH", raising=False)
                monkeypatch.chdir(tmp_path)
                home_dir = tmp_path / "home"
                monkeypatch.setenv("HOME", str(home_dir))

                first = home_dir / ".gem-code" / "mcp_config.json"
                second = home_dir / ".config" / "gem-code" / "mcp.json"
                third = tmp_path / "mcp_config.json"

                second.parent.mkdir(parents=True, exist_ok=True)
                third.write_text(json.dumps({"mcpServers": {"cwd": {"type": "local"}}}), encoding="utf-8")
                second.write_text(json.dumps({"mcpServers": {"config": {"type": "local"}}}), encoding="utf-8")

                assert load_mcp_config_from_env() == {"mcpServers": {"config": {"type": "local"}}}

                first.parent.mkdir(parents=True, exist_ok=True)
                first.write_text(json.dumps({"mcpServers": {"home": {"type": "local"}}}), encoding="utf-8")
                assert load_mcp_config_from_env() == {"mcpServers": {"home": {"type": "local"}}}
            """
        ).strip()
        + "\n",
    ),
    TaskDefinition(
        name="agent-wrapper",
        title="Minimal Agent Wrapper",
        difficulty="easy",
        category="software_engineering",
        tags=["python", "agent", "async"],
        instruction=dedent(
            """
            Fix `/workspace/evaluation_fixture/src/agent.py`.

            Requirements:
            - Keep the wrapper small and valid.
            - `init()` should forward to the wrapped session manager.
            - `run()` should forward the instruction to the wrapped chat session.
            - Do not edit tests.
            """
        ).strip(),
        workspace_files={
            "src/agent.py": dedent(
                '''
                class FakeSession:
                    def __init__(self):
                        self.commands: list[str] = []

                    async def chat(self, instruction: str) -> None:
                        self.commands.append(instruction)


                class SessionManager:
                    def __init__(self, config):
                        self.config = config
                        self.session = FakeSession()
                        self.initialized = False

                    async def init(self) -> None:
                        self.initialized = True


                class Agent:
                    """Minimal wrapper reserved for future orchestration."""

                    def __init__(self, config):
                        self.session = SessionManager(config)

                    async def init(self) -> None:
                        pass

                    async def run(self, instruction: str) -> None:
                        return None
                '''
            ).strip()
            + "\n",
        },
        test_code=dedent(
            """
            import asyncio

            from agent import Agent


            def test_agent_wrapper_forwards_to_session_manager() -> None:
                async def run() -> None:
                    agent = Agent(config={"name": "demo"})
                    await agent.init()
                    await agent.run("inspect README")
                    assert agent.session.initialized is True
                    assert agent.session.session.commands == ["inspect README"]

                asyncio.run(run())
            """
        ).strip()
        + "\n",
    ),
]


def build_matrix(base_dir: Path | None = None) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    matrix_root = base_dir or repo_root / "evaluation" / "harbor_matrix"
    tasks_root = matrix_root / "tasks"

    if matrix_root.exists():
        shutil.rmtree(matrix_root)

    tasks_root.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, object]] = []

    for task in TASKS:
        task_dir = tasks_root / task.name
        (task_dir / "environment").mkdir(parents=True, exist_ok=True)
        (task_dir / "tests").mkdir(parents=True, exist_ok=True)
        (task_dir / "workspace_template").mkdir(parents=True, exist_ok=True)

        (task_dir / "task.toml").write_text(_task_toml(task), encoding="utf-8")
        (task_dir / "instruction.md").write_text(task.instruction + "\n", encoding="utf-8")
        (task_dir / "environment" / "README.md").write_text(
            _environment_readme(),
            encoding="utf-8",
        )
        (task_dir / "tests" / "test.sh").write_text(_test_sh(), encoding="utf-8")
        (task_dir / "tests" / "test_outputs.py").write_text(
            task.test_code,
            encoding="utf-8",
        )

        for relative_path, contents in task.workspace_files.items():
            output_path = task_dir / "workspace_template" / relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(contents, encoding="utf-8")

        manifest.append(
            {
                "name": task.name,
                "title": task.title,
                "difficulty": task.difficulty,
                "category": task.category,
                "tags": task.tags,
            }
        )

    (matrix_root / "README.md").write_text(
        dedent(
            """
            # Harbor Smoke Matrix

            This directory contains the initial 10-task Harbor benchmark matrix
            for gem-code. The tasks are generated by
            `evaluation/build_harbor_matrix.py` so the fixtures, prompts, and
            tests stay synchronized.
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (matrix_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return matrix_root


if __name__ == "__main__":
    output_dir = build_matrix()
    print(output_dir)
