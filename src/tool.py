from __future__ import annotations

import asyncio
import copy
import glob
import json
import os
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

import aiofiles
from rich.console import Console
from trafilatura import extract, fetch_url

from .mcp_client import MCPClient
from .models import ToolCall
from .security import (
    SecuritySettings,
    build_sandbox_runner_command,
    ensure_url_permitted,
)

console = Console()


def _object_schema(
    properties: Dict[str, Any],
    required: List[str],
) -> Dict[str, Any]:
    """Build a JSON schema that rejects undeclared keys.

    Detailed schemas reduce hallucinated arguments and line up with OpenAI's
    current tool-calling guidance. We avoid `strict: true` at the transport
    layer because this project targets OpenAI-compatible providers whose schema
    support varies, but we still make the input contract explicit here.
    """

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


TOOLS: Final[List[Dict[str, Any]]] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Execute a shell command once inside the configured working "
                "directory and return stdout, stderr, and the exit code."
            ),
            "parameters": _object_schema(
                {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "description": {
                        "type": "string",
                        "description": "A short reason for running the command.",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": (
                            "Optional timeout in milliseconds. Defaults to "
                            "120000 and is capped at 600000."
                        ),
                    },
                },
                ["command", "description"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a text file from inside the working directory. Prefer "
                "using start_line/end_line for progressive disclosure on large files."
            ),
            "parameters": _object_schema(
                {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the working directory.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": (
                            "Optional 1-based inclusive start line. Use with "
                            "end_line to read only part of a file."
                        ),
                    },
                    "end_line": {
                        "type": "integer",
                        "description": (
                            "Optional 1-based inclusive end line. Omit to read "
                            "from start_line to the end of the file."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "A short reason for reading the file.",
                    },
                },
                ["path", "description"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text content to a file inside the working directory.",
            "parameters": _object_schema(
                {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the working directory.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete file content to write.",
                    },
                    "description": {
                        "type": "string",
                        "description": "A short reason for writing the file.",
                    },
                },
                ["path", "content", "description"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "StrReplaceFile",
            "description": "Replace specific strings in a text file inside the working directory.",
            "parameters": _object_schema(
                {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the working directory.",
                    },
                    "edits": {
                        "type": "array",
                        "description": "Ordered string replacements to apply once each.",
                        "items": _object_schema(
                            {
                                "target": {
                                    "type": "string",
                                    "description": "The text to replace.",
                                },
                                "replacement": {
                                    "type": "string",
                                    "description": "Replacement text.",
                                },
                            },
                            ["target", "replacement"],
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "A short reason for editing the file.",
                    },
                },
                ["path", "edits", "description"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a URL and extract readable Markdown content.",
            "parameters": _object_schema(
                {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch.",
                    },
                    "description": {
                        "type": "string",
                        "description": "A short reason for fetching the URL.",
                    },
                },
                ["url", "description"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": "Match files under the working directory using a glob pattern.",
            "parameters": _object_schema(
                {
                    "pattern": {
                        "type": "string",
                        "description": "A glob pattern such as '**/*.py'.",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional subdirectory relative to the working "
                            "directory in which to start the search."
                        ),
                    },
                },
                ["pattern"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Grep",
            "description": "Search file contents with ripgrep under the working directory.",
            "parameters": _object_schema(
                {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Optional file or directory relative to the working directory.",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Optional ripgrep --glob filter.",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                    },
                    "-B": {
                        "type": "integer",
                        "description": "Lines of context before each match.",
                    },
                    "-A": {
                        "type": "integer",
                        "description": "Lines of context after each match.",
                    },
                    "-C": {
                        "type": "integer",
                        "description": "Lines of context before and after each match.",
                    },
                    "-n": {
                        "type": "boolean",
                        "description": "Show line numbers in content mode.",
                    },
                    "-i": {
                        "type": "boolean",
                        "description": "Case-insensitive matching.",
                    },
                    "type": {
                        "type": "string",
                        "description": "Optional ripgrep file type filter.",
                    },
                    "head_limit": {
                        "type": "integer",
                        "description": "Limit the number of output lines or entries.",
                    },
                    "multiline": {
                        "type": "boolean",
                        "description": "Enable multiline mode.",
                    },
                },
                ["pattern"],
            ),
        },
    },
]


_mcp_client: Optional[MCPClient] = None


def set_mcp_client(client: Optional[MCPClient]) -> None:
    global _mcp_client
    _mcp_client = client


def get_mcp_client() -> Optional[MCPClient]:
    return _mcp_client


def clone_tools() -> List[Dict[str, Any]]:
    """Return a deep copy so sessions can safely append MCP and skill tools."""

    return copy.deepcopy(TOOLS)


def _workdir_root(workdir: str) -> Path:
    return Path(workdir).expanduser().resolve()


def _resolve_path_in_workdir(
    workdir: str,
    path: str,
    *,
    allow_create: bool = False,
    require_directory: bool = False,
) -> Path:
    """Resolve a user-supplied path without letting it escape the workspace.

    Coding agents need a hard filesystem boundary. Joining strings with
    `os.path.join()` is not enough because `../` segments and absolute paths can
    silently escape the intended root. We resolve both the workspace root and
    candidate path, then verify the candidate stays underneath the root.
    """

    root = _workdir_root(workdir)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate

    resolved = candidate.resolve(strict=not allow_create)

    if resolved != root and root not in resolved.parents:
        raise ValueError(
            f"Refusing to access path outside workdir: {resolved} is not within {root}"
        )

    if require_directory and not resolved.is_dir():
        raise ValueError(f"Expected a directory path, got: {resolved}")

    return resolved


def _format_subprocess_result(
    command: str,
    exit_code: int,
    stdout: bytes,
    stderr: bytes,
) -> str:
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    parts = [f"$ {command}", f"[exit_code={exit_code}]"]
    if stdout_text:
        parts.append(stdout_text)
    if stderr_text:
        parts.append(f"[stderr]\n{stderr_text}")
    if not stdout_text and not stderr_text:
        parts.append("(empty output)")
    return "\n".join(parts)


async def run_bash(
    command: str,
    workdir: str,
    timeout_ms: int = 120000,
    security_settings: Optional[SecuritySettings] = None,
) -> str:
    """Execute a single shell command with an enforced timeout.

    The previous implementation advertised timeouts and structured execution in
    the tool schema but ignored both at runtime. Returning the command and exit
    code makes failures debuggable for both humans and the model.
    """

    timeout_ms = max(1, min(timeout_ms, 600000))
    if security_settings is not None and security_settings.enabled:
        argv, env = build_sandbox_runner_command(command, workdir, security_settings)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(_workdir_root(workdir)),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(_workdir_root(workdir)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_ms / 1000,
        )
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        timeout_result = _format_subprocess_result(command, -1, stdout, stderr)
        return f"{timeout_result}\n[timeout_ms={timeout_ms}] Command timed out."

    return _format_subprocess_result(command, proc.returncode or 0, stdout, stderr)


async def run_read_file(
    path: str,
    workdir: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> str:
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be greater than or equal to 1")
    if end_line is not None and end_line < 1:
        raise ValueError("end_line must be greater than or equal to 1")

    effective_start_line = start_line or 1
    if end_line is not None and effective_start_line > end_line:
        raise ValueError("start_line must be less than or equal to end_line")

    file_path = _resolve_path_in_workdir(workdir, path)
    async with aiofiles.open(file_path, "r", encoding="utf-8") as handle:
        if start_line is None and end_line is None:
            content=await handle.read()
            return content

        selected_lines: List[str] = []
        total_lines = 0
        async for line in handle:
            total_lines += 1
            if total_lines < effective_start_line:
                continue
            if end_line is not None and total_lines > end_line:
                break
            selected_lines.append(f"{total_lines}: {line.rstrip(chr(10))}")

    if selected_lines:
        return "\n".join(selected_lines)

    requested_end = end_line if end_line is not None else "EOF"
    return (
        f"No content found in {file_path} for line range "
        f"{effective_start_line}-{requested_end}. File has {total_lines} line(s)."
    )


async def run_write_file(path: str, content: str, workdir: str) -> str:
    file_path = _resolve_path_in_workdir(workdir, path, allow_create=True)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(file_path, "w", encoding="utf-8") as handle:
        await handle.write(content)
    return f"Successfully wrote {len(content)} characters to {file_path}"


async def run_str_replace_file(path: str, edits: List[Dict[str, str]], workdir: str) -> str:
    """Apply deterministic single replacements and fail loudly on missing targets.

    Silent no-op replacements are dangerous for coding agents because the model
    believes it edited the file while the source tree remains unchanged.
    """

    file_path = _resolve_path_in_workdir(workdir, path)
    async with aiofiles.open(file_path, "r", encoding="utf-8") as handle:
        content = await handle.read()

    for edit in edits:
        target = edit.get("target", "")
        replacement = edit.get("replacement", "")
        if target not in content:
            raise ValueError(f"Target text not found in {file_path}: {target!r}")
        content = content.replace(target, replacement, 1)

    async with aiofiles.open(file_path, "w", encoding="utf-8") as handle:
        await handle.write(content)

    return f"Successfully applied {len(edits)} replacement(s) to {file_path}"


async def run_fetch_url_to_markdown(
    url: str,
    security_settings: Optional[SecuritySettings] = None,
) -> str:
    if security_settings is not None:
        ensure_url_permitted(url, security_settings)
    try:
        downloaded = await asyncio.to_thread(fetch_url, url)
        if not downloaded:
            return f"Failed to fetch URL {url}: No content received"
        result = await asyncio.to_thread(
            extract,
            downloaded,
            output_format="markdown",
            include_comments=False,
        )
        return result if result else ""
    except Exception as exc:
        return f"Failed to fetch the url {url}: {str(exc)}"


async def run_glob(pattern: str, workdir: str, path: Optional[str] = None) -> str:
    """Run a recursive glob search without allowing directory escape."""

    search_dir = (
        _resolve_path_in_workdir(workdir, path, require_directory=True)
        if path
        else _workdir_root(workdir)
    )

    if os.path.isabs(pattern):
        raise ValueError("Glob patterns must be relative to the working directory")

    def do_glob() -> List[str]:
        full_pattern = str(search_dir / pattern)
        matches = [
            str(Path(match).resolve())
            for match in glob.glob(full_pattern, recursive=True)
            if Path(match).exists()
        ]
        matches = [
            match
            for match in matches
            if _workdir_root(workdir) in Path(match).parents or Path(match) == _workdir_root(workdir)
        ]
        matches.sort(key=lambda item: os.path.getmtime(item), reverse=True)
        return matches

    matches = await asyncio.to_thread(do_glob)
    return "\n".join(matches) if matches else ""


async def run_grep(
    pattern: str,
    workdir: str,
    path: Optional[str] = None,
    glob_pattern: Optional[str] = None,
    file_type: Optional[str] = None,
    output_mode: str = "files_with_matches",
    case_insensitive: bool = False,
    show_line_numbers: bool = False,
    before_context: int = 0,
    after_context: int = 0,
    context: int = 0,
    head_limit: Optional[int] = None,
    multiline: bool = False,
) -> str:
    search_path = (
        _resolve_path_in_workdir(workdir, path)
        if path
        else _workdir_root(workdir)
    )

    cmd = ["rg"]

    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")

    if case_insensitive:
        cmd.append("-i")

    if output_mode == "content":
        if show_line_numbers:
            cmd.append("-n")
        if before_context > 0:
            cmd.extend(["-B", str(before_context)])
        if after_context > 0:
            cmd.extend(["-A", str(after_context)])
        if context > 0:
            cmd.extend(["-C", str(context)])

    if multiline:
        # `-U` enables multiline search and `--multiline-dotall` lets `.`
        # consume newlines, which matches ripgrep's documented behavior.
        cmd.extend(["-U", "--multiline-dotall"])

    if glob_pattern:
        cmd.extend(["--glob", glob_pattern])

    if file_type:
        cmd.extend(["--type", file_type])

    cmd.extend([pattern, str(search_path)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode not in (0, 1):
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            return f"Error executing grep: {error_msg}"

        result = stdout.decode("utf-8", errors="replace")
        if head_limit is not None and result:
            lines = result.splitlines()
            result = "\n".join(lines[:head_limit])

        return result
    except FileNotFoundError:
        return "Error: ripgrep (rg) not found. Please install ripgrep."
    except Exception as exc:
        return f"Error executing grep: {str(exc)}"


OUTPUT_TRUNCATE_LENGTH: Final[int] = 32000


def formatted_tool_output(output: str) -> str:
    cleaned = output.rstrip("\n")
    if len(cleaned) > OUTPUT_TRUNCATE_LENGTH:
        head_len = int(OUTPUT_TRUNCATE_LENGTH * 0.2)
        tail_len = int(OUTPUT_TRUNCATE_LENGTH * 0.2)
        head = cleaned[:head_len]
        tail = cleaned[-tail_len:]
        skipped = len(cleaned) - head_len - tail_len
        return f"{head}\n...[{skipped} characters omitted]...\n{tail}"
    return cleaned


def parse_tool_arguments(toolcall: ToolCall) -> Dict[str, Any]:
    try:
        return json.loads(toolcall.function.arguments)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Error parsing tool arguments: {str(exc)}[/red]")
        return {}
