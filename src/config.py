import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional
from dotenv import load_dotenv
from openai import AsyncOpenAI

from .permissions import PermissionMode, policy_mode_from_env
from .security import SecuritySettings, load_security_settings


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
    security: SecuritySettings
    use_tool_search: bool = True
    permission_gate_enabled: bool = True
    predict_before_call_enabled: bool = False
    self_discovery_enabled: bool = False
    permission_mode: PermissionMode = "strict"


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

    security = load_security_settings(workdir)

    return Config(
        api_key=api_key,
        base_url=base_url,
        model=model,
        workdir=workdir,
        skills_dir=skills_dir,
        mcp_config_path=mcp_config_path,
        memory_compaction_path=memory_compaction_path,
        api_mode=api_mode,
        security=security,
        use_tool_search=os.getenv("USE_TOOL_SEARCH", "false").lower() == "true",
        permission_gate_enabled=_parse_bool_env("GEM_CODE_PERMISSION_GATE_ENABLED", True),
        predict_before_call_enabled=_parse_bool_env("GEM_CODE_PREDICT_BEFORE_CALL", False),
        self_discovery_enabled=_parse_bool_env("GEM_CODE_SELF_DISCOVERY", False),
        permission_mode=policy_mode_from_env(default="strict"),
    )


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    return default


SYSTEM_PROMPT = """
你是 Gem Code，一个轻量级的 CLI Agent。

当前工作目录：{workdir}
当前安全策略：{security_summary}

## 工具使用规则

当你需要执行命令或读取文件时，必须使用 tool_calls 格式调用工具。

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

可选参数：
- start_line: 起始行号，1-based 且包含该行
- end_line: 结束行号，1-based 且包含该行

优先使用 `start_line` / `end_line` 做渐进式披露，只读取当前需要的片段，避免一次读取超大文件。

示例：
{"path": "package.json", "description": "查看项目配置"}
{"path": "src/session.py", "start_line": 120, "end_line": 180, "description": "查看工具分发逻辑"}

## 工作流程
1. 直接调用工具获取信息，不要在回复中描述你要做什么
2. 基于工具返回的结果回答用户问题
3. 保持回答简洁、直接

## Skills
如果用户询问的内容与某个 Skill 的描述相关，自动触发调用以 "skill__" 开头的 tool 来加载 SKILL.md 文档中的最佳实践到上下文，接着遵循最佳实践中的指示行动。

## 重要
- 所有命令都必须在工作目录下执行，不允许在没有用户同意的情况下执行危险的代码
- `bash` 在安全沙箱中执行；默认只允许工作目录、私有临时目录和必要的系统运行时路径
- 使用完整路径避免混淆
- 遇到错误时，修复后重试
"""


PREDICT_BEFORE_CALL_CLAUSE = """
## 工具调用前的预测要求 (Predict before call)
当你即将调用任何工具之前，必须在 *visible* 输出（reasoning 或正式回复）中先用 1–2 句话简短预测：
- 这次调用最可能产生的可观察副作用（写入哪些文件、是否发起网络调用、是否启动子进程）。
- 如果该工具可能修改用户工作区或外部状态，明确指出来。
然后再判断是否真的发出 tool_call。
预测必须出现在你可见的回复中（不要把它藏在隐式的 chain-of-thought 里）。
仅本会话启用 GEM_CODE_PREDICT_BEFORE_CALL=true 时该规则生效；其他情况下忽略本节。
"""


def get_system_prompt(
    workdir: str,
    security: Optional[SecuritySettings] = None,
    *,
    predict_before_call_enabled: bool = False,
) -> str:
    """Render the system prompt against the configured work directory.

    When `predict_before_call_enabled` is True (driven by the
    `GEM_CODE_PREDICT_BEFORE_CALL` env flag), append the predict-before-call
    clause so the main LLM produces a brief visible side-effect prediction
    before emitting any tool call. This is a system-prompt augmentation only —
    the runtime tool-call path issues no extra LLM round-trip and the gate
    never mutates `Session.history`.
    """

    rendered = SYSTEM_PROMPT.replace("{workdir}", str(Path(workdir).expanduser()))
    rendered = rendered.replace(
        "{security_summary}",
        security.summary() if security is not None else "sandbox on; policy hidden",
    )
    if predict_before_call_enabled:
        rendered = rendered + PREDICT_BEFORE_CALL_CLAUSE
    return rendered


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
