from typing import Any, Final,Dict,List
from rich.console import Console
from config import ToolCall
import openai
import json
console=Console()
import subprocess
TOOLS: Final[List[Dict[str, Any]]] = [
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
                    }
                },
                "required":["path","edits"]
            }
        }
    }
]
async def run_tool(name: str, args: Dict[str, Any], workdir: str) -> str:
    """
    Execute a tool call (async)
    
    Args:
        name: Tool name ("bash", "read_file", "write_file", "StrReplaceFile")
        args: Argument dictionary
        workdir: Working directory
    Returns:
        Formatted result string
    """
    try:
        if name == "bash":
            command = args.get("command", "")
            output=await run_bash(command, workdir)
            return formatted_tool_output(output)
        elif name == "read_file":
            path = args.get("path", "")
            output=await run_read_file(path)
            return formatted_tool_output(output)
        elif name == "write_file":
            path = args.get("path", "")
            output = args.get("content", "")
            return formatted_tool_output(output)
        elif name == "StrReplaceFile":
            path = args.get("path", "")
            edits = args.get("edits", [])
            output=await run_str_replace_file(path, edits, workdir)
            return formatted_tool_output(output)
        else:
            return f"Error: Unknown tool: {name}"
    except Exception as e:
        return f"Error executing tool {name}: {str(e)}"

async def run_bash(command: str, workdir: str) -> str:
    # Placeholder for actual bash execution logic
    result = subprocess.run(
    [command, workdir], 
    capture_output=True, 
    text=True,  # 自动解码为字符串（Python 3.7+）
    encoding="utf-8"
)
    return result.stdout if result.stdout else (result.stderr if result.stderr else "(empty output)")
async def run_read_file(path: str) -> str:
    # Placeholder for actual file reading logic
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file {path}: {str(e)}"
async def run_write_file(path: str, content: str, workdir: str) -> str:
    # Placeholder for actual file writing logic
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing to file {path}: {str(e)}"
async def run_str_replace_file(path: str, edits: List[Dict[str, str]], workdir: str) -> str:
    # Placeholder for actual string replacement logic
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        for edit in edits:
            target = edit.get("target", "")
            replacement = edit.get("replacement", "")
            content = content.replace(target, replacement,1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully performed string replacements in {path}"
    except Exception as e:
        return f"Error performing string replacements in file {path}: {str(e)}"

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
def parseToolArguments(toolcall:ToolCall):
    try:
        return json.loads(toolcall.function.arguments)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error parsing tool arguments: {str(e)}[/red]")
        return {}