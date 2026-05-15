from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from rich import print
from rich.console import Console
from rich.text import Text

from .config import Config, load_config
from .decorate import pc_blue, pc_cyan, pc_gray
from .permissions import PermissionDecision, normalize_bash_command
from .session_manager import SessionManager
import readline

console = Console()
_stream_phase: str | None = None


def _switch_stream_phase(next_phase: str) -> None:
    global _stream_phase
    if _stream_phase and _stream_phase != next_phase:
        console.print()
    _stream_phase = next_phase


def _end_stream_line() -> None:
    global _stream_phase
    if _stream_phase is not None:
        console.print()
        _stream_phase = None


async def async_input(prompt: str = "") -> str:
    """Collect terminal input without blocking the event loop."""

    return await asyncio.to_thread(input, prompt)


def on_reasoning(chunk: str) -> None:
    _switch_stream_phase("reasoning")
    console.print(Text(chunk, style="dim"), end="")


def on_content(chunk: str) -> None:
    _switch_stream_phase("content")
    console.print(Text(chunk, style="blue"), end="")


def on_tool_start(tool_name: str, args: dict) -> None:
    _end_stream_line()
    console.print(pc_blue(f"🛠️  Executing tool: {tool_name}"))


def on_tool_result(tool_name: str, result: str) -> None:
    _end_stream_line()
    console.print(pc_blue(f"👁 OBSERVE\n{result}\n"))


def make_cli_approval_callback(config: Config):
    """Return an `async request_tool_approval` closure for CLI sessions.

    The closure prompts the user with `[A]llow once / [S]ession-allow / [D]eny`
    and resolves all non-affirmative outcomes (EOF, KeyboardInterrupt,
    unrecognized input, multiline-bash + `S`) to `deny`. Multiline bash
    commands disable the `S` choice per DEC-7 — the prompt only accepts
    `[A/D]` in that case.
    """

    async def request_tool_approval(
        tool_name: str,
        args: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[PermissionDecision]:
        approval_key = context.get("approval_key", tool_name)
        audit_category = context.get("audit_category")
        is_multiline = bool(context.get("is_multiline_bash"))

        _end_stream_line()
        console.print(pc_cyan(f"\n🔐 Tool approval required: {tool_name}"))
        if tool_name == "bash":
            command = args.get("command", "")
            console.print(pc_gray(f"   command: {command!r}"))
            if audit_category:
                console.print(pc_gray(f"   audit_category: {audit_category}"))
            if is_multiline:
                console.print(pc_gray("   note: multiline bash → \"allow session\" disabled"))
        else:
            if args:
                console.print(pc_gray(f"   args: {args}"))

        prompt_choices = "[A]llow once / [D]eny" if is_multiline else "[A]llow once / [S]ession-allow / [D]eny"
        try:
            answer = (await async_input(f"   {prompt_choices}: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print(pc_gray("   → deny (cli_interrupt)"))
            return PermissionDecision(
                decision="deny",
                reason="cli_interrupt",
                approval_key=approval_key,
                audit_category=audit_category,
            )

        if answer in {"a", "allow", "allow_once", "y", "yes"}:
            return PermissionDecision(
                decision="allow_once",
                reason="user_choice",
                approval_key=approval_key,
                audit_category=audit_category,
            )
        if answer in {"s", "session", "allow_session"} and not is_multiline:
            return PermissionDecision(
                decision="allow_session",
                reason="user_choice",
                approval_key=approval_key,
                audit_category=audit_category,
            )
        if answer in {"d", "deny", "n", "no"}:
            return PermissionDecision(
                decision="deny",
                reason="user_choice",
                approval_key=approval_key,
                audit_category=audit_category,
            )
        # Unrecognized input → fail closed.
        console.print(pc_gray(f"   unrecognized input {answer!r} → deny"))
        return PermissionDecision(
            decision="deny",
            reason="unrecognized_cli_choice",
            approval_key=approval_key,
            audit_category=audit_category,
        )

    return request_tool_approval


async def main(initial_prompt: str | None = None, once: bool = False) -> None:
    session_manager: SessionManager | None = None
    try:
        config = load_config()
    except Exception as exc:
        print(f"[red]Error loading config: {exc}[/]")
        return

    # AC-10: non-interactive `--once` runs default to `auto_deny` so the loop
    # never blocks on a non-existent human. Explicit env override still wins.
    if once and os.getenv("GEM_CODE_PERMISSION_MODE", "").strip().lower() not in {
        "strict",
        "auto_deny",
        "auto_allow_safe",
    }:
        config.permission_mode = "auto_deny"

    request_tool_approval = make_cli_approval_callback(config)

    console.print(
        pc_cyan(
            """
╔══════════════════════════════════════╗
║     Gem Code CLI Agent v1.0.0        ║
║     按 Ctrl+C 或输入 exit 退出        ║
╚══════════════════════════════════════╝
"""
        )
    )

    try:
        session_manager = SessionManager(config)
        await session_manager.init()

        if initial_prompt:
            console.print(pc_gray(f"User input from command line: {initial_prompt}"))
            await session_manager.session.chat(
                initial_prompt,
                on_reasoning=on_reasoning,
                on_content=on_content,
                on_tool_start=on_tool_start,
                on_tool_result=on_tool_result,
                request_tool_approval=request_tool_approval,
            )
            _end_stream_line()
            if once:
                return

        while True:
            try:
                user_input = await async_input("➜ ")
                if not user_input or user_input.lower() == "exit":
                    if user_input:
                        console.print(pc_gray("Exiting..."))
                    break
                if not user_input.strip():
                    continue
                console.print(pc_gray(f"➜ User: {user_input}"))
                await session_manager.session.chat(
                    user_input,
                    on_reasoning=on_reasoning,
                    on_content=on_content,
                    on_tool_start=on_tool_start,
                    on_tool_result=on_tool_result,
                    request_tool_approval=request_tool_approval,
                )
                _end_stream_line()
            except EOFError:
                console.print(pc_gray("\nExiting..."))
                break
            except KeyboardInterrupt:
                console.print(pc_gray("\nExiting..."))
                break
    finally:
        if session_manager:
            await session_manager.session.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"[red]Unexpected error: {exc}[/]")
