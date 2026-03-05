from typing import Any, Final,Dict,List,Optional
from rich.console import Console
from .config import ToolCall,Message
from.skill import load_skills
import openai
import json
import os
from trafilatura import fetch_url,extract
console=Console()
import subprocess
from .mcp_client import MCPClient

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
    # Execute shell command in the specified working directory
    result = subprocess.run(
        command,
        shell=True,
        cwd=os.path.expanduser(workdir),
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    return result.stdout if result.stdout else (result.stderr if result.stderr else "(empty output)")
async def run_read_file(path: str,workdir:str) -> str:
    path=os.path.join(os.path.expanduser(workdir), path)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file {path}: {str(e)}"
async def run_write_file(path: str, content: str, workdir: str) -> str:
    path=os.path.join(os.path.expanduser(workdir), path)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing to file {path}: {str(e)}"
async def run_str_replace_file(path: str, edits: List[Dict[str, str]], workdir: str) -> str:
    path=os.path.join(os.path.expanduser(workdir), path)
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
async def run_fetch_url_to_markdown(url:str)->str:
    try:
        downloaded=fetch_url(url)
        result = extract(downloaded,output_format="markdown",include_comments=False)
        return result if result else ""
    except Exception as e:
        return f"Failed to fetch the url {url}: {str(e)}"
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