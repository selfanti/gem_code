import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Final, TypedDict, Optional
from rich.console import Console
from rich.text import Text

console = Console()

# ==================== 类型定义 ====================
class ToolFunction(TypedDict):
    name: str
    arguments: str  # JSON 字符串

class ToolCall(TypedDict):
    function: ToolFunction
    # 可能还有 id, type 等字段

# ==================== 工具配置 ====================
# Final 表示常量，类似 TypeScript 的 as const
TOOLS: Final[list[dict[str, Any]]] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command in the current working directory",
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
    }
]

OUTPUT_TRUNCATE_LENGTH: Final[int] = 32000


# ==================== 核心函数 ====================
async def run_tool(name: str, args: dict[str, Any], workdir: str) -> str:
    """
    执行工具调用（异步）
    
    Args:
        name: 工具名 ("bash" 或 "read_file")
        args: 参数字典
        workdir: 工作目录
    
    Returns:
        执行结果字符串（已格式化）
    """
    # Rich 彩色输出（替代 picocolors）
    console.print("[bold cyan]⚡ ACT")
    console.print(f"[bold green]🛠[/] [dim]{name}({json.dumps(args)})[/dim]")
    
    try:
        if name == "bash":
            command = args.get("command")
            if not command or not isinstance(command, str):
                return "Error: bash tool requires a 'command' parameter"
            
            try:
                # 使用 asyncio 异步执行 shell 命令（替代 promisify(exec)）
                # 注意：create_subprocess_shell 相当于 exec，但更安全可用 create_subprocess_exec
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=workdir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=10 * 1024 * 1024  # 类似 maxBuffer (10MB)
                )
                
                stdout_bytes, stderr_bytes = await proc.communicate()
                stdout = stdout_bytes.decode('utf-8', errors='replace')
                stderr = stderr_bytes.decode('utf-8', errors='replace')
                
                output = stdout if stdout else (stderr if stderr else "(empty output)")
                return _format_tool_output(output)
                
            except Exception as e:
                return f"Error: {str(e)}"
        
        elif name == "read_file":
            file_path = args.get("path")
            if not file_path or not isinstance(file_path, str):
                return "Error: read_file tool requires a 'path' parameter"
            
            try:
                # Path.read_text 是同步的，用 run_in_executor 包装为异步
                # 或者直接用 aiofiles 库，这里为了少依赖用前者
                loop = asyncio.get_event_loop()
                content = await loop.run_in_executor(
                    None, 
                    lambda: Path(file_path).read_text(encoding='utf-8')
                )
                return _format_tool_output(content)
            except FileNotFoundError:
                return f"Error: File not found: {file_path}"
            except Exception as e:
                return f"Error: {str(e)}"
        
        else:
            return f'Error: unknown tool "{name}"'
            
    except Exception as e:
        return f"Error: {str(e)}"


def _format_tool_output(output: str) -> str:
    """格式化输出，截断过长内容"""
    trimmed = output.rstrip('\n')  # 替代 TypeScript 的 replace(/\n$/, "")
    
    if len(trimmed) > OUTPUT_TRUNCATE_LENGTH:
        truncated = trimmed[:OUTPUT_TRUNCATE_LENGTH]
        remaining = len(trimmed) - OUTPUT_TRUNCATE_LENGTH
        return f"{truncated}\n...(output truncated, {remaining} more characters)"
    
    return trimmed


def parse_tool_arguments(tool_call: ToolCall) -> dict[str, Any]:
    """
    解析工具调用的 JSON 参数
    
    注意：这是同步函数，因为 JSON 解析是 CPU 操作，无需 async
    """
    try:
        return json.loads(tool_call["function"]["arguments"])
    except (json.JSONDecodeError, KeyError):
        return {}


# ==================== 使用示例 ====================
async def main():
    """测试用"""
    # 测试 bash
    result = await run_tool("bash", {"command": "ls -la", "description": "List files"}, ".")
    print(result[:500])  # 只打印前500字符
    
    # 测试 read_file
    # result = await run_tool("read_file", {"path": "test.txt", "description": "Read test"}, ".")

if __name__ == "__main__":
    asyncio.run(main())