from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI


ApiMode = Literal["auto", "chat_completions", "responses"]


@dataclass
class Config:
    api_key: str
    base_url: str
    model: str
    workdir: str
    skills_dir: Optional[str]
    mcp_config_path: Optional[str]
    memory_compaction_path: str
    api_mode: ApiMode


def _expand_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return str(Path(path).expanduser())


def load_config() -> Config:
    """Load runtime configuration with conservative defaults.

    The project originally treated several paths as required. That made local
    development and automated testing fragile because a missing optional feature
    directory caused startup to fail before the CLI could even boot. We now
    provide defaults for optional integrations and keep only the API credentials
    mandatory.
    """

    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    model = os.getenv("OPENAI_MODEL", "MiniMax-M2.5")
    workdir = str(Path(os.getenv("WORKDIR") or os.getcwd()).expanduser())

    # Default the skills directory to the repository-local `.agents` folder used
    # by this project. `load_skills()` already handles missing directories, so
    # we keep the path optional rather than forcing callers to create it.
    skills_dir = _expand_path(os.getenv("SKILLS_DIR")) or str(Path(workdir) / ".agents")

    # MCP config is optional because the client also searches a documented list
    # of fallback paths when this explicit location is absent.
    mcp_config_path = _expand_path(os.getenv("MCP_CONFIG_PATH"))

    # Persist memory under a deterministic default so session storage works out
    # of the box for both TUI and tests.
    memory_compaction_path = (
        _expand_path(os.getenv("MEMORY_COMPACTION_PATH")) or "~/.gem_code/projects"
    )

    # `auto` keeps compatibility with OpenAI-compatible providers such as
    # MiniMax while allowing first-class Responses API support for OpenAI's own
    # platform. Session code resolves the effective mode at runtime.
    api_mode = os.getenv("OPENAI_API_MODE", "auto")
    if api_mode not in {"auto", "chat_completions", "responses"}:
        raise ValueError(
            "OPENAI_API_MODE must be one of: auto, chat_completions, responses"
        )

    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")
    if not base_url:
        raise ValueError("OPENAI_BASE_URL environment variable is not set")

    return Config(
        api_key=api_key,
        base_url=base_url,
        model=model,
        workdir=workdir,
        skills_dir=skills_dir,
        mcp_config_path=mcp_config_path,
        memory_compaction_path=memory_compaction_path,
        api_mode=api_mode,
    )


SYSTEM_PROMPT = """
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

## Skills
如果用户询问的内容与某个 Skill 的描述相关，自动触发调用以 "skill__" 开头的 tool 来加载 SKILL.md 文档中的最佳实践到上下文。

## 重要
- 所有命令都必须在工作目录下执行，不允许在没有用户同意的情况下执行危险的代码
- 使用完整路径避免混淆
- 遇到错误时，修复后重试
"""


def get_system_prompt(workdir: str) -> str:
    """Render the system prompt against the configured work directory."""

    return SYSTEM_PROMPT.replace("{workdir}", str(Path(workdir).expanduser()))


def resolve_api_mode(config: Config) -> Literal["chat_completions", "responses"]:
    """Pick the effective API mode.

    OpenAI's own platform should prefer the Responses API. OpenAI-compatible
    third-party providers still vary widely in feature coverage, so `auto`
    intentionally falls back to Chat Completions for non-OpenAI base URLs.
    """

    if config.api_mode == "chat_completions":
        return "chat_completions"
    if config.api_mode == "responses":
        return "responses"
    if "api.openai.com" in config.base_url:
        return "responses"
    return "chat_completions"


def create_openai_client(config: Config) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)
