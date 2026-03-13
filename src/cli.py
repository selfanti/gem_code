from __future__ import annotations

import asyncio

from rich import print
from rich.console import Console
from rich.text import Text

from .config import load_config
from .decorate import pc_blue, pc_cyan, pc_gray
from .session_manager import SessionManager

console = Console()


async def async_input(prompt: str = "") -> str:
    """Collect terminal input without blocking the event loop."""

    return await asyncio.to_thread(input, prompt)


def on_reasoning(chunk: str) -> None:
    console.print(Text(chunk, style="dim"), end="")


def on_content(chunk: str) -> None:
    console.print(Text(chunk, style="blue"), end="")


def on_tool_start(tool_name: str, args: dict) -> None:
    console.print(pc_blue(f"\n🛠️  Executing tool: {tool_name}"))


def on_tool_result(tool_name: str, result: str) -> None:
    console.print(pc_blue(f"👁 OBSERVE\n{result}\n"))


async def main(initial_prompt: str | None = None, once: bool = False) -> None:
    session_manager: SessionManager | None = None
    try:
        config = load_config()
    except Exception as exc:
        print(f"[red]Error loading config: {exc}[/]")
        return

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
            )
            console.print()
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
                )
                console.print()
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
