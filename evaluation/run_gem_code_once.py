from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    # When this script is executed as `python evaluation/run_gem_code_once.py`,
    # Python otherwise places `evaluation/` rather than the repository root at
    # the front of `sys.path`, which breaks imports like `src.config`.
    sys.path.insert(0, str(REPO_ROOT))

from src.config import load_config
from src.session_manager import SessionManager


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one Gem Code turn and persist structured Harbor metadata.",
    )
    parser.add_argument(
        "--instruction",
        required=True,
        help="Single instruction to send to Gem Code.",
    )
    return parser


async def _run_once(instruction: str) -> int:
    metadata_path = Path(
        Path.cwd().joinpath("gem_code_run.json")
        if "HARBOR_GEM_CODE_RUN_METADATA_PATH" not in os.environ
        else os.environ["HARBOR_GEM_CODE_RUN_METADATA_PATH"]
    )
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    tool_events: list[dict[str, Any]] = []
    assistant_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    session_manager: SessionManager | None = None

    def on_reasoning(chunk: str) -> None:
        reasoning_chunks.append(chunk)

    def on_content(chunk: str) -> None:
        assistant_chunks.append(chunk)

    def on_tool_start(tool_name: str, args: dict) -> None:
        tool_events.append(
            {
                "event": "start",
                "tool_name": tool_name,
                "arguments": args,
            }
        )

    def on_tool_result(tool_name: str, result: str) -> None:
        # Tool outputs can be very large. The metadata file is for Harbor-side
        # reporting and debugging, so a truncated preview is enough to explain
        # what happened without duplicating the entire transcript.
        tool_events.append(
            {
                "event": "result",
                "tool_name": tool_name,
                "result_preview": result[:4000],
            }
        )

    metadata: dict[str, Any] = {
        "instruction": instruction,
        "assistant_output": "",
        "reasoning_output": "",
        "tool_events": tool_events,
        "status": "started",
    }

    try:
        config = load_config()
        session_manager = SessionManager(config)
        await session_manager.init()
        await session_manager.session.chat(
            instruction,
            on_reasoning=on_reasoning,
            on_content=on_content,
            on_tool_start=on_tool_start,
            on_tool_result=on_tool_result,
        )

        metadata.update(
            {
                "status": "ok",
                "assistant_output": "".join(assistant_chunks),
                "reasoning_output": "".join(reasoning_chunks),
                "session_id": session_manager.session_id,
                "transcript_path": str(session_manager.transcript_path),
                "context_usage": asdict(
                    session_manager.session.get_context_usage_snapshot()
                ),
            }
        )
        return_code = 0
    except Exception as exc:
        metadata.update(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback": traceback.format_exc(),
                "assistant_output": "".join(assistant_chunks),
                "reasoning_output": "".join(reasoning_chunks),
            }
        )
        return_code = 1
    finally:
        if session_manager is not None:
            await session_manager.session.cleanup()
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return return_code


def main() -> int:
    args = _build_argument_parser().parse_args()
    return asyncio.run(_run_once(args.instruction))


if __name__ == "__main__":
    raise SystemExit(main())
