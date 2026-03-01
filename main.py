#!/usr/bin/env python3
"""
Gem Code - A lightweight CLI Agent with interactive chat
"""

import sys
import argparse


def main():
    parser = argparse.ArgumentParser(
        description="Gem Code - AI CLI Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Launch TUI mode
  python main.py --cli              # Launch CLI mode
  python main.py "your question"    # One-shot mode with initial prompt
  python main.py --help             # Show this help message
        """
    )
    
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Initial prompt to send (optional)"
    )
    
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Use CLI mode instead of TUI"
    )
    
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Use TUI mode (default)"
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version="Gem Code v0.1.0"
    )
    
    args = parser.parse_args()
    
    # Determine mode
    use_cli = args.cli or (args.prompt and not args.tui)
    
    if use_cli:
        # CLI mode
        from src.cli import main as cli_main
        import asyncio
        
        # If prompt provided, set it as command line args for CLI
        if args.prompt:
            sys.argv = [sys.argv[0], args.prompt]
        else:
            sys.argv = [sys.argv[0]]
        
        try:
            asyncio.run(cli_main())
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
    else:
        # TUI mode (default)
        from src.tui import GemCodeApp
        from src.config import load_config
        config = load_config()
        app = GemCodeApp(config)
        app.run()


if __name__ == "__main__":
    main()
