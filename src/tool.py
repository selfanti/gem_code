from typing import Any, Final, Dict, List, Optional
from rich.console import Console
from .config import ToolCall,Message
from .skill import load_skills
# import openai  # 当前未使用
import json
import os
from trafilatura import fetch_url,extract
import aiofiles
# import subprocess  # 已改用 asyncio.create_subprocess_shell
from .mcp_client import MCPClient
import asyncio
import glob
console=Console()

TOOLS: Final[List[Dict[str, Any]]] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Executes a given bash command in a persistent shell session with optional timeout, ensuring proper handling and security measures.\n\nBefore executing the command, please follow these steps:\n\n1. Directory Verification:\n   - If the command will create new directories or files, first use the LS tool to verify the parent directory exists and is the correct location\n   - For example, before running \"mkdir foo/bar\", first use LS to check that \"foo\" exists and is the intended parent directory\n\n2. Command Execution:\n   - Always quote file paths that contain spaces with double quotes (e.g., cd \"path with spaces/file.txt\")\n   - Examples of proper quoting:\n     - cd \"/Users/name/My Documents\" (correct)\n     - cd /Users/name/My Documents (incorrect - will fail)\n     - python \"/path/with spaces/script.py\" (correct)\n     - python /path/with spaces/script.py (incorrect - will fail)\n   - After ensuring proper quoting, execute the command.\n   - Capture the output of the command.\n\nUsage notes:\n  - The command argument is required.\n  - You can specify an optional timeout in milliseconds (up to 600000ms / 10 minutes). If not specified, commands will timeout after 120000ms (2 minutes).\n  - It is very helpful if you write a clear, concise description of what this command does in 5-10 words.\n  - If the output exceeds 30000 characters, output will be truncated before being returned to you.\n  - You can use the `run_in_background` parameter to run the command in the background, which allows you to continue working while the command runs. You can monitor the output using the Bash tool as it becomes available. Never use `run_in_background` to run 'sleep' as it will return immediately. You do not need to use '&' at the end of the command when using this parameter.\n  - VERY IMPORTANT: You MUST avoid using search commands like `find` and `grep`. Instead use Grep, Glob, or Task to search. You MUST avoid read tools like `cat`, `head`, `tail`, and `ls`, and use Read and LS to read files.\n - If you _still_ need to run `grep`, STOP. ALWAYS USE ripgrep at `rg` first, which all Claude Code users have pre-installed.\n  - When issuing multiple commands, use the ';' or '&&' operator to separate them. DO NOT use newlines (newlines are ok in quoted strings).\n  - Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.\n    <good-example>\n    pytest /foo/bar/tests\n    </good-example>\n    <bad-example>\n    cd /foo/bar && pytest tests\n    </bad-example>\n\n\n# Committing changes with git\n\nWhen the user asks you to create a new git commit, follow these steps carefully:\n\n1. You have the capability to call multiple tools in a single response. When multiple independent pieces of information are requested, batch your tool calls together for optimal performance. ALWAYS run the following bash commands in parallel, each using the Bash tool:\n  - Run a git status command to see all untracked files.\n  - Run a git diff command to see both staged and unstaged changes that will be committed.\n  - Run a git log command to see recent commit messages, so that you can follow this repository's commit message style.\n2. Analyze all staged changes (both previously staged and newly added) and draft a commit message:\n  - Summarize the nature of the changes (eg. new feature, enhancement to an existing feature, bug fix, refactoring, test, docs, etc.). Ensure the message accurately reflects the changes and their purpose (i.e. \"add\" means a wholly new feature, \"update\" means an enhancement to an existing feature, \"fix\" means a bug fix, etc.).\n  - Check for any sensitive information that shouldn't be committed\n  - Draft a concise (1-2 sentences) commit message that focuses on the \"why\" rather than the \"what\"\n  - Ensure it accurately reflects the changes and their purpose\n3. You have the capability to call multiple tools in a single response. When multiple independent pieces of information are requested, batch your tool calls together for optimal performance. ALWAYS run the following commands in parallel:\n   - Add relevant untracked files to the staging area.\n   - Create the commit with a message ending with:\n   🤖 Generated with [Claude Code](https://claude.ai/code)\n\n   Co-Authored-By: Claude <noreply@anthropic.com>\n   - Run git status to make sure the commit succeeded.\n4. If the commit fails due to pre-commit hook changes, retry the commit ONCE to include these automated changes. If it fails again, it usually means a pre-commit hook is preventing the commit. If the commit succeeds but you notice that files were modified by the pre-commit hook, you MUST amend your commit to include them.\n\nImportant notes:\n- NEVER update the git config\n- NEVER run additional commands to read or explore code, besides git bash commands\n- NEVER use the TodoWrite or Task tools\n- DO NOT push to the remote repository unless the user explicitly asks you to do so\n- IMPORTANT: Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported.\n- If there are no changes to commit (i.e., no untracked files and no modifications), do not create an empty commit\n- In order to ensure good formatting, ALWAYS pass the commit message via a HEREDOC, a la this example:\n<example>\ngit commit -m \"$(cat <<'EOF'\n   Commit message here.\n\n   🤖 Generated with [Claude Code](https://claude.ai/code)\n\n   Co-Authored-By: Claude <noreply@anthropic.com>\n   EOF\n   )\"\n</example>\n\n# Creating pull requests\nUse the gh command via the Bash tool for ALL GitHub-related tasks including working with issues, pull requests, checks, and releases. If given a Github URL use the gh command to get the information needed.\n\nIMPORTANT: When the user asks you to create a pull request, follow these steps carefully:\n\n1. You have the capability to call multiple tools in a single response. When multiple independent pieces of information are requested, batch your tool calls together for optimal performance. ALWAYS run the following bash commands in parallel using the Bash tool, in order to understand the current state of the branch since it diverged from the main branch:\n   - Run a git status command to see all untracked files\n   - Run a git diff command to see both staged and unstaged changes that will be committed\n   - Check if the current branch tracks a remote branch and is up to date with the remote, so you know if you need to push to the remote\n   - Run a git log command and `git diff [base-branch]...HEAD` to understand the full commit history for the current branch (from the time it diverged from the base branch)\n2. Analyze all changes that will be included in the pull request, making sure to look at all relevant commits (NOT just the latest commit, but ALL commits that will be included in the pull request!!!), and draft a pull request summary\n3. You have the capability to call multiple tools in a single response. When multiple independent pieces of information are requested, batch your tool calls together for optimal performance. ALWAYS run the following commands in parallel:\n   - Create new branch if needed\n   - Push to remote with -u flag if needed\n   - Create PR using gh pr create with the format below. Use a HEREDOC to pass the body to ensure correct formatting.\n<example>\ngh pr create --title \"the pr title\" --body \"$(cat <<'EOF'\n## Summary\n<1-3 bullet points>\n\n## Test plan\n[Checklist of TODOs for testing the pull request...]\n\n🤖 Generated with [Claude Code](https://claude.ai/code)\nEOF\n)\"\n</example>\n\nImportant:\n- NEVER update the git config\n- DO NOT use the TodoWrite or Task tools\n- Return the PR URL when you're done, so the user can see it\n\n# Other common operations\n- View comments on a Github PR: gh api repos/foo/bar/pulls/123/comments",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command to execute"
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what this command does in 5-10 words"
                    }
                },
                "required": ["command", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to read"
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of why you're reading this file"
                    }
                },
                "required": ["path", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to write to"
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file"
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of why you're writing to this file"
                    }
                },
                "required": ["path", "content", "description"]
            }
        }
    },
    {
        "type":"function",
        "function":{
            "name":"StrReplaceFile",
            "description":"Replace content in a file based on string matching",
            "parameters":{
                "type":"object",
                "properties":{
                    "path":{
                        "type":"string",
                        "description":"The file path to operate on"
                    },
                    "edits":{
                        "type":"array",
                        "description":"List of the dictionaries of the key of 'target' to perform and the value of the 'replacement'",
                        "items":{
                            "type":"object",
                            "properties":{
                                "target":{
                                    "type":"string",
                                    "description":"The string to be replaced"
                                },
                                "replacement":{
                                    "type":"string",
                                    "description":"The string to replace with"
                                }
                            },
                            "required":["target","replacement"]
                        }
                    },
                    "description":{
                        "type": "string",
                        "description": "Brief description of why do you replace the content of this file"
                    }
                },
                "required":["path","edits","description"]
            }
        }
    },
    {
        "type":"function",
        "function":{
            "name":"fetch_url",
            "description":"Fetch content of the url, output is Markdown format",
            "parameters":{
                "type":"object",
                "properties":{
                    "url":{
                        "type":"string",
                        "description":"The url to fetch content"
                    },
                    "description":{
                        "type": "string",
                        "description": "Brief description of what do you get about the content of this url"
                    }
                },
                "required":["url","description"]
            }
        }
    },
     {
        "type":"function",
        "function":{
      "name": "Glob",
      "description": "- Fast file pattern matching tool that works with any codebase size\n- Supports glob patterns like \"**/*.js\" or \"src/**/*.ts\"\n- Returns matching file paths sorted by modification time\n- Use this tool when you need to find files by name patterns\n- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead\n- You have the capability to call multiple tools in a single response. It is always better to speculatively perform multiple searches as a batch that are potentially useful.",
      "parameters": {
        "type": "object",
        "properties": {
          "pattern": {
            "type": "string",
            "description": "The glob pattern to match files against"
          },
          "path": {
            "type": "string",
            "description": "The directory to search in. If not specified, the current working directory will be used. IMPORTANT: Omit this field to use the default directory. DO NOT enter \"undefined\" or \"null\" - simply omit it for the default behavior. Must be a valid directory path if provided."
          }
        },
        "required": ["pattern"]
       
      }
    }
    },
    {"type":"function",
        "function":{
      "name": "Grep",
      "description": "A powerful search tool built on ripgrep\n\n  Usage:\n  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.\n  - Supports full regex syntax (e.g., \"log.*Error\", \"function\\s+\\w+\")\n  - Filter files with glob parameter (e.g., \"*.js\", \"**/*.tsx\") or type parameter (e.g., \"js\", \"py\", \"rust\")\n  - Output modes: \"content\" shows matching lines, \"files_with_matches\" shows only file paths (default), \"count\" shows match counts\n  - Use Task tool for open-ended searches requiring multiple rounds\n  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)\n  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`\n",
      "parameters": {
        "type": "object",
        "properties": {
          "pattern": {
            "type": "string",
            "description": "The regular expression pattern to search for in file contents"
          },
          "path": {
            "type": "string",
            "description": "File or directory to search in (rg PATH). Defaults to current working directory."
          },
          "glob": {
            "type": "string",
            "description": "Glob pattern to filter files (e.g. \"*.js\", \"*.{ts,tsx}\") - maps to rg --glob"
          },
          "output_mode": {
            "type": "string",
            "enum": [
              "content",
              "files_with_matches",
              "count"
            ],
            "description": "Output mode: \"content\" shows matching lines (supports -A/-B/-C context, -n line numbers, head_limit), \"files_with_matches\" shows file paths (supports head_limit), \"count\" shows match counts (supports head_limit). Defaults to \"files_with_matches\"."
          },
          "-B": {
            "type": "number",
            "description": "Number of lines to show before each match (rg -B). Requires output_mode: \"content\", ignored otherwise."
          },
          "-A": {
            "type": "number",
            "description": "Number of lines to show after each match (rg -A). Requires output_mode: \"content\", ignored otherwise."
          },
          "-C": {
            "type": "number",
            "description": "Number of lines to show before and after each match (rg -C). Requires output_mode: \"content\", ignored otherwise."
          },
          "-n": {
            "type": "boolean",
            "description": "Show line numbers in output (rg -n). Requires output_mode: \"content\", ignored otherwise."
          },
          "-i": {
            "type": "boolean",
            "description": "Case insensitive search (rg -i)"
          },
          "type": {
            "type": "string",
            "description": "File type to search (rg --type). Common types: js, py, rust, go, java, etc. More efficient than include for standard file types."
          },
          "head_limit": {
            "type": "number",
            "description": "Limit output to first N lines/entries, equivalent to \"| head -N\". Works across all output modes: content (limits output lines), files_with_matches (limits file paths), count (limits count entries). When unspecified, shows all results from ripgrep."
          },
          "multiline": {
            "type": "boolean",
            "description": "Enable multiline mode where . matches newlines and patterns can span lines (rg -U --multiline-dotall). Default: false."
          }
        },
        "required": ["pattern"]
      }
    }
}
   

]
# MCP 客户端实例（将在 Session 中初始化）
_mcp_client: Optional[MCPClient] = None

def set_mcp_client(client: Optional[MCPClient]) -> None:
    """设置全局 MCP 客户端实例"""
    global _mcp_client
    _mcp_client = client

def get_mcp_client() -> Optional[MCPClient]:
    """获取全局 MCP 客户端实例"""
    return _mcp_client


async def run_bash(command: str, workdir: str) -> str:
    # Execute shell command in the specified working directory asynchronously
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=os.path.expanduser(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    result = stdout.decode('utf-8', errors='replace') if stdout else (stderr.decode('utf-8', errors='replace') if stderr else "(empty output)")
    return result
async def run_read_file(path: str, workdir: str) -> str:
    file_path = os.path.join(os.path.expanduser(workdir), path)
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            return await f.read()
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"
async def run_write_file(path: str, content: str, workdir: str) -> str:
    file_path = os.path.join(os.path.expanduser(workdir), path)
    try:
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(content)
        return f"Successfully wrote to {file_path}"
    except Exception as e:
        return f"Error writing to file {file_path}: {str(e)}"
async def run_str_replace_file(path: str, edits: List[Dict[str, str]], workdir: str) -> str:
    file_path = os.path.join(os.path.expanduser(workdir), path)
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        for edit in edits:
            target = edit.get("target", "")
            replacement = edit.get("replacement", "")
            content = content.replace(target, replacement, 1)
        
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(content)
        
        return f"Successfully performed string replacements in {file_path}"
    except Exception as e:
        return f"Error performing string replacements in file {file_path}: {str(e)}"
async def run_fetch_url_to_markdown(url: str) -> str:
    try:
        # Run blocking trafilatura operations in thread pool
        downloaded = await asyncio.to_thread(fetch_url, url)
        if not downloaded:
            return f"Failed to fetch URL {url}: No content received"
        result = await asyncio.to_thread(
            extract, 
            downloaded, 
            output_format="markdown", 
            include_comments=False
        )
        return result if result else ""
    except Exception as e:
        return f"Failed to fetch the url {url}: {str(e)}"
async def run_glob(pattern: str, workdir: str, path: Optional[str] = None) -> str:
    """
    Fast file pattern matching tool that works with any codebase size.
    Supports glob patterns like "**/*.js" or "src/**/*.ts".
    Returns matching file paths sorted by modification time.
    """
    # 确定搜索目录
    if path:
        search_dir = os.path.join(os.path.expanduser(workdir), path)
    else:
        search_dir = os.path.expanduser(workdir)
    
    try:
        def do_glob():
            # 如果 pattern 是绝对路径或相对路径前缀，直接使用；否则拼接
            if os.path.isabs(pattern) or pattern.startswith('./') or pattern.startswith('../'):
                full_pattern = pattern
            else:
                full_pattern = os.path.join(search_dir, pattern)
            
            # 执行 glob 搜索（递归模式）
            matches = glob.glob(full_pattern, recursive=True)
            
            # 按修改时间排序（最新的在前）
            matches.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return matches
        
        # 在线程池中执行以避免阻塞事件循环
        matches = await asyncio.to_thread(do_glob)
        
        return "\n".join(matches) if matches else ""
        
    except Exception as e:
        return f"Error globbing pattern '{pattern}': {str(e)}"


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
    multiline: bool = False
) -> str:
    """
    A powerful search tool built on ripgrep.
    """
    # 确定搜索路径
    if path:
        search_path = os.path.join(os.path.expanduser(workdir), path)
    else:
        search_path = os.path.expanduser(workdir)
    
    # 构建 ripgrep 命令
    cmd = ["rg"]
    
    # 输出模式映射
    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    elif output_mode == "content":
        pass  # rg 默认输出详细内容
    else:
        cmd.append("-l")  # 默认
    
    # 搜索选项映射（对应 JSON 中的 -i, -n, -B, -A, -C）
    if case_insensitive:
        cmd.append("-i")
    
    # 以下选项仅在 content 模式下有效
    if output_mode == "content":
        if show_line_numbers:
            cmd.append("-n")
        if before_context > 0:
            cmd.extend(["-B", str(before_context)])
        if after_context > 0:
            cmd.extend(["-A", str(after_context)])
        if context > 0:
            cmd.extend(["-C", str(context)])
    
    # 多行模式（对应 rg -U --multiline-dotall）
    if multiline:
        cmd.append("-U")
    
    # 文件过滤（对应 JSON 中的 glob 和 type）
    if glob_pattern:
        cmd.extend(["--glob", glob_pattern])
    
    if file_type:
        cmd.extend(["--type", file_type])
    
    # 添加搜索模式和目标路径
    cmd.extend([pattern, search_path])
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await proc.communicate()
        
        # rg 返回码 1 表示未找到匹配，这不是错误
        if proc.returncode not in (0, 1):
            error_msg = stderr.decode('utf-8', errors='replace').strip()
            return f"Error executing grep: {error_msg}"
        
        result = stdout.decode('utf-8', errors='replace')
        
        # 处理 head_limit 截断（适用于所有输出模式）
        if head_limit is not None and result:
            lines = result.splitlines()
            result = "\n".join(lines[:head_limit])
        
        return result
        
    except FileNotFoundError:
        return "Error: ripgrep (rg) not found. Please install ripgrep."
    except Exception as e:
        return f"Error executing grep: {str(e)}"
    
OUTPUT_TRUNCATE_LENGTH: Final[int] = 32000
def formatted_tool_output(output: str) -> str:
    # 1. 清理尾部空白（学习 TS 版本）
    cleaned = output.rstrip('\n')
    
    if len(cleaned) > OUTPUT_TRUNCATE_LENGTH:
        head_len = int(OUTPUT_TRUNCATE_LENGTH * 0.2)
        tail_len = int(OUTPUT_TRUNCATE_LENGTH * 0.2)
        head = cleaned[:head_len]
        tail = cleaned[-tail_len:]
        skipped = len(output) - head_len - tail_len

        return f"{head}\n...[{skipped} characters omitted]...\n{tail}"
    
    return cleaned
def parse_tool_arguments(toolcall: ToolCall):
    try:
        return json.loads(toolcall.function.arguments)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error parsing tool arguments: {str(e)}[/red]")
        return {}


def map_grep_params(args: Dict[str, Any]) -> Dict[str, Any]:
    """Map Grep tool JSON parameter names to run_grep function parameter names."""
    param_mapping = {
        "pattern": "pattern",
        "path": "path",
        "glob": "glob_pattern",
        "type": "file_type",
        "output_mode": "output_mode",
        "-i": "case_insensitive",
        "-n": "show_line_numbers",
        "-B": "before_context",
        "-A": "after_context",
        "-C": "context",
        "head_limit": "head_limit",
        "multiline": "multiline",
    }
    
    mapped = {}
    for json_key, func_key in param_mapping.items():
        if json_key in args:
            mapped[func_key] = args[json_key]
    
    return mapped