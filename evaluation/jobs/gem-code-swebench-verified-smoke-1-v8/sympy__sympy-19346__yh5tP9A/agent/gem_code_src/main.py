#!/usr/bin/env python3
"""Gem Code entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys


def _should_launch_tui(*, stdin_is_tty: bool, stdout_is_tty: bool) -> bool:
    """Return whether the environment can support Textual's full-screen TUI.

    The TUI needs an interactive terminal on both stdin and stdout. When the
    app is launched from a non-interactive runner, Textual can appear to open
    and then exit immediately without a useful explanation. Centralizing this
    check lets us fail loudly or fall back to CLI mode instead of silently
    confusing the user.
    """

    return stdin_is_tty and stdout_is_tty


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gem Code - AI CLI Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Launch TUI mode
  python main.py --cli              # Launch CLI mode
  python main.py --cli --once "fix tests"   # One-shot CLI mode
  python main.py "your question"    # One-shot mode with initial prompt
  python main.py --help             # Show this help message
        """,
    )

    parser.add_argument("prompt", nargs="?", help="Initial prompt to send (optional)")
    parser.add_argument("--cli", action="store_true", help="Use CLI mode instead of TUI")
    parser.add_argument("--tui", action="store_true", help="Use TUI mode (default)")
    parser.add_argument(
        "--once",
        action="store_true",
        help="When running in CLI mode, send the initial prompt and exit.",
    )
    parser.add_argument("--version", action="version", version="Gem Code v0.1.0")

    args = parser.parse_args()
    use_cli = args.cli or (args.prompt and not args.tui)

    if use_cli:
        from src.cli import main as cli_main

        try:
            asyncio.run(cli_main(initial_prompt=args.prompt, once=args.once))
        except KeyboardInterrupt:
            print("\nGoodbye!")
        return

    from src.config import load_config
    from src.tui import GemCodeApp

    if not _should_launch_tui(
        stdin_is_tty=sys.stdin.isatty(),
        stdout_is_tty=sys.stdout.isatty(),
    ):
        # This fallback specifically fixes the "TUI cannot start" class of bug
        # in headless or piped environments. If a one-shot prompt is available
        # we can still provide useful behavior by routing to CLI mode.
        if args.prompt:
            from src.cli import main as cli_main

            asyncio.run(cli_main(initial_prompt=args.prompt, once=True))
            return

        print(
            "Gem Code TUI requires an interactive terminal. "
            "Run with `--cli`, or launch it from a real TTY."
        )
        return

    config = load_config()
    app = GemCodeApp(config)
    try:
        app.run()
    except Exception as exc:
        # Textual startup failures can otherwise collapse the alternate screen
        # before the user sees what happened. Re-raising as plain stderr output
        # makes startup issues diagnosable from a normal shell.
        print(f"Gem Code TUI failed to start: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
