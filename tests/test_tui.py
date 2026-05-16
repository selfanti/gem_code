import asyncio
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src.config import load_config
from src.permissions import PermissionDecision
from src.security import SecuritySettings
from src.tui import GemCodeApp


def test_tui_smoke_startup() -> None:
    """Ensure the TUI can mount without crashing.

    This is a regression test for the class of bugs where the TUI appeared to
    "not start" because startup exceptions were only visible inside Textual's
    alternate screen. Running the app headlessly through `run_test()` gives us
    a stable way to exercise the real startup path in CI.
    """

    async def run() -> None:
        config = load_config()
        config.memory_compaction_path = str(Path(config.workdir) / ".gem-code" / "test-memory")
        config.mcp_config_path = None
        with patch.dict("os.environ", {"GEM_CODE_DISABLE_MCP": "true"}):
            app = GemCodeApp(config)
            async with app.run_test() as pilot:
                await pilot.pause(0.2)
                assert app.session_manager is not None

    asyncio.run(run())


def test_inline_tool_approval_returns_allow_once() -> None:
    """The TUI approval surface should be an in-layout prompt, not a modal."""

    async def run() -> None:
        app = GemCodeApp(_test_config())
        with patch.dict("os.environ", {"GEM_CODE_DISABLE_MCP": "true"}):
            async with app.run_test() as pilot:
                approval_task = asyncio.create_task(
                    app.query_one("#tool-approval").request(
                        tool_name="fetch_url",
                        args={"url": "https://example.com"},
                        approval_key="fetch_url",
                        audit_category=None,
                        is_multiline_bash=False,
                    )
                )
                await pilot.pause(0.1)
                await pilot.click("#approval-once")
                decision = await asyncio.wait_for(approval_task, timeout=1)

        assert isinstance(decision, PermissionDecision)
        assert decision.decision == "allow_once"
        assert decision.approval_key == "fetch_url"

    asyncio.run(run())


def test_inline_tool_approval_disables_session_for_multiline_bash() -> None:
    async def run() -> None:
        app = GemCodeApp(_test_config())
        with patch.dict("os.environ", {"GEM_CODE_DISABLE_MCP": "true"}):
            async with app.run_test() as pilot:
                approval_task = asyncio.create_task(
                    app.query_one("#tool-approval").request(
                        tool_name="bash",
                        args={"command": "git status\nls"},
                        approval_key="git status\nls",
                        audit_category="unknown",
                        is_multiline_bash=True,
                    )
                )
                await pilot.pause(0.1)
                assert app.query_one("#approval-session").disabled is True
                await pilot.click("#approval-deny")
                decision = await asyncio.wait_for(approval_task, timeout=1)

        assert decision.decision == "deny"

    asyncio.run(run())


def _test_config() -> Config:
    return Config(
        api_key="test-key",
        base_url="https://example.invalid/",
        model="test-model",
        workdir="/tmp",
        skills_dir=None,
        mcp_config_path=None,
        memory_compaction_path="/tmp/.memory",
        api_mode="chat_completions",
        security=SecuritySettings(
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
        ),
        use_tool_search=False,
    )
