from dataclasses import dataclass
from typing import Literal
import os
from typing import Optional,List
from openai import OpenAI, AsyncOpenAI
@dataclass
class Config:
    api_key: str
    base_url: str  # 改为小写 base_url（PEP8 规范）
    model: str
    workdir: str
    skills_dir:Optional[str]


def load_config() -> Config:
    # 获取环境变量（带类型提示）
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    model = os.getenv("OPENAI_MODEL", "MiniMax-M2.5")  # 提供默认值
    workdir = os.getenv("WORKDIR") or os.getcwd()      # 提供默认值
    skills_dir = os.getenv("SKILLS_DIR")  # 可选项，无默认值    
    # 验证必需项
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")
    if not base_url:
        raise ValueError("OPENAI_BASE_URL environment variable is not set")
    
    return Config(
        api_key=api_key,
        base_url=base_url,
        model=model,
        workdir=workdir,
        skills_dir=skills_dir
    )

SYSTEM_PROMPT="""
你是 Gem Code，一个轻量级的 CLI Agent。

当前工作目录：{workdir}

## 工具使用规则

当你需要执行命令或读取文件时，必须使用 tool_calls 格式调用工具，而不是在文本中描述。

### bash 工具
用于执行 Shell 命令。调用时必须提供：
- command: 完整的命令字符串（必填）
- description: 简洁说明（必填）

示例：
{"command": "ls -la", "description": "列出文件"}

### read_file 工具
用于读取文件内容。调用时必须提供：
- path: 文件路径（必填）
- description: 简洁说明（必填）

示例：
{"path": "package.json", "description": "查看项目配置"}

## 工作流程
1. 直接调用工具获取信息，不要在回复中描述你要做什么
2. 基于工具返回的结果回答用户问题
3. 保持回答简洁、直接

## 重要
- 所有命令都必须在工作目录下执行
- 使用完整路径避免混淆
- 遇到错误时，修复后重试
"""
def get_system_prompt() -> str:
    return SYSTEM_PROMPT.replace('{workdir}',os.getcwd())

Role = Literal["system", "user", "assistant", "tool"]
@dataclass
class Message:
    role: Role                    # 必填
    content: Optional[str]       # 必填，但可为 None
    tool_calls: Optional[List] = None  # 可选，默认 None
    tool_call_id: Optional[str] = None # 可选，默认 None

@dataclass
class FunctionCall:
    name:str
    arguments:str
@dataclass
class ToolCall:
    id:str
    function:FunctionCall
    type:str="function"
def createOpenAIClient(api_key:str, base_url:str) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key, base_url=base_url)
