# Gem Code CLI Agent

轻量级 CLI Agent，基于 OpenAI 兼容 API，支持工具调用、MCP 协议、技能扩展和智能上下文管理。

## 特性

- 🤖 **多模型支持**：OpenAI 兼容 API（默认 MiniMax-M2.5）
- 🔌 **MCP 协议**：连接外部工具服务器（Playwright、Fetch、Filesystem 等）
- 🛠️ **内置工具**：Bash、文件读写、字符串替换、URL 抓取、Glob、Grep
- 🔒 **安全沙箱**：`bash` 工具默认在 Landlock 子进程沙箱中运行，遵循最小权限原则
- 📁 **技能扩展**：通过 SKILL.md 扩展专业能力
- 💬 **流式响应**：实时显示，支持思考内容分离
- 🧠 **上下文管理**：智能压缩（microcompaction）和自动摘要（autocompaction）
- 💾 **会话持久化**：基于 JSONL 的会话存储和恢复
- 🖥️ **双模式**：TUI（推荐）和 CLI 模式

## 快速开始

### 1. 安装

```bash
uv sync
```
另外需要在embedding_model文件夹下下载一个嵌入模型，推荐使用Qwen3-Embedding-0.6B，USE_TOOL_SEARCH设置为false则不需要

### 2. 配置

```bash
cp .env_example .env
```

编辑 `.env`：

```bash
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.minimaxi.com/v1
OPENAI_MODEL=MiniMax-M2.5
OPENAI_API_MODE=auto
WORKDIR=~/gem_code
SKILLS_DIR=~/gem_code/.agents  # 可选，默认值为 WORKDIR/.agents
MCP_CONFIG_PATH=~/gem_code/mcp_config.json  # 可选
MEMORY_COMPACTION_PATH=~/.gem_code/projects
GEM_CODE_SECURITY_ENABLED=true
GEM_CODE_SECURITY_BEST_EFFORT=true
GEM_CODE_SECURITY_ALLOW_NETWORK=false
GEM_CODE_SECURITY_ALLOW_CONNECT=443
USE_TOOL_SEARCH=false
```

### 3. 运行

**TUI 模式（推荐）**：

```bash
uv run python main.py
```

**CLI 模式**：

```bash
uv run python main.py --cli
uv run python main.py "你的问题"  # 一次性提问
uv run python main.py --cli --once "你的问题"  # 发送一次后退出，适合自动化/评测
```

## 使用方法

### TUI 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Enter` | 换行 |
| `Ctrl+Enter` / `Send` | 发送消息 |
| `Ctrl+C` | 退出 |
| `Ctrl+L` / `Clear` | 清空聊天记录 |
| `Ctrl+S` | 切换侧边栏 |
| `?` | 帮助 |
| `Escape` | 聚焦输入框 |

### TUI 界面说明

侧边栏显示以下信息：
- **MODEL**: 当前使用的模型和 API
- **WORKSPACE**: 当前工作目录
- **TOOLS**: 可用的内置工具列表
- **CONTEXT**: 实时显示上下文使用量（绿色 <60%，黄色 60-80%，红色 >80%）
- **FILES**: 工作目录文件树

状态栏显示当前状态、模型名称和上下文使用百分比。

### 内置工具

| 工具 | 描述 | 参数 |
|------|------|------|
| `bash` | 执行 Shell 命令 | `command`, `description` |
| `read_file` | 读取文件，支持渐进式披露 | `path`, `start_line`, `end_line`, `description` |
| `write_file` | 写入文件 | `path`, `content`, `description` |
| `StrReplaceFile` | 字符串替换 | `path`, `edits` |
| `fetch_url` | 抓取 URL 内容 | `url`, `description` |
| `Glob` | 文件搜索 | `pattern`, `path` |
| `Grep` | 代码搜索 | `pattern`, `path`, `glob`, `output_mode`, `-i`, `-n`, `-B`, `-A`, `-C` |

### 安全模型

- `bash` 不再直接在主 Agent 进程里执行，而是通过独立的 Landlock runner 启动受限子进程
- 默认只允许访问工作目录、工作目录下的私有临时目录 `.gem-code/tmp`，以及必要的系统运行时路径（如 `/usr`、`/bin`、`/lib`）
- 出站网络默认开启；显式配置 `GEM_CODE_SECURITY_ALLOW_NETWORK=true` 或在 `GEM_CODE_SECURITY_ALLOW_CONNECT` 中放行端口后，`bash` 和 `fetch_url` 才允许联网
- 内核或环境不支持 Landlock 时，默认以 `best-effort` 模式继续执行并在工具输出中标注安全降级；如需 fail-closed，可设置 `GEM_CODE_SECURITY_BEST_EFFORT=false`

可选环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GEM_CODE_SECURITY_ENABLED` | `true` | 是否启用内置安全策略 |
| `GEM_CODE_SECURITY_BEST_EFFORT` | `true` | Landlock 不可用时是否继续执行 |
| `GEM_CODE_SECURITY_ALLOW_NETWORK` | `true` | 是否完全放开网络 |
| `GEM_CODE_SECURITY_ALLOW_CONNECT` | 空 | 允许出站连接的 TCP 端口，逗号分隔 |
| `GEM_CODE_SECURITY_ALLOW_BIND` | 空 | 允许监听的 TCP 端口，逗号分隔 |
| `GEM_CODE_SECURITY_EXTRA_READ_PATHS` | 空 | 追加只读路径，逗号分隔 |
| `GEM_CODE_SECURITY_EXTRA_WRITE_PATHS` | 空 | 追加可写路径，逗号分隔 |
| `GEM_CODE_SECURITY_EXTRA_EXECUTE_PATHS` | 空 | 追加可执行路径，逗号分隔 |
注意部分限制特性依赖Linux内核版本
### 上下文管理

Gem Code 实现了智能上下文管理系统，有效处理长对话：

**Micro Compaction（微观压缩）**
- 当上下文使用量超过 60% 时自动触发
- 将早期的工具输出内容转移到持久化存储
- 保留消息引用，释放上下文空间

**Auto Compaction（自动摘要）**
- 当上下文使用量超过 80% 时自动触发
- 使用 LLM 生成对话摘要，包含：
  - 用户意图和关键决策
  - 已探索的概念和修改的文件
  - 遇到的错误和解决方案
  - 待处理任务和当前状态
- 添加压缩边界，后续对话基于摘要继续

**会话持久化**
- 会话自动保存到 `~/.gem_code/memory/{workspace}/{session_id}.jsonl`
- 支持会话恢复（Fork）和继续
- O(1) 时间复杂度读取任意历史消息

### 技能扩展

在 `SKILLS_DIR` 目录创建子文件夹，每个文件夹包含 `SKILL.md`：

```
.agent/skills/
├── python-best-practices/
│   └── SKILL.md
├── react-patterns/
│   └── SKILL.md
└── code-review-excellence/
    └── SKILL.md
```

SKILL.md 格式：

```markdown
---
name: Python Best Practices
description: 提供 Python 代码审查和最佳实践建议
---

## 代码风格
- 遵循 PEP 8
- 使用类型注解
...

## 常见模式
...
```

Agent 会自动按需加载相关技能，无需手动选择。

### MCP 配置

创建 `mcp_config.json`：

```json
{
  "mcpServers": {
    "filesystem": {
      "type": "local",
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
      "enabled": true,
      "timeout": 30000
    },
    "fetch": {
      "type": "local",
      "command": ["npx", "-y", "@modelcontextprotocol/server-fetch"],
      "enabled": true
    },
    "playwright": {
      "type": "local",
      "command": ["npx", "-y", "@playwright/mcp@latest"],
      "environment": {
        "PLAYWRIGHT_CHROMIUM": "true"
      },
      "enabled": true,
      "timeout": 60000
    }
  }
}
```

常用 MCP 服务器：

| 服务器 | 命令 | 说明 |
|--------|------|------|
| filesystem | `npx -y @modelcontextprotocol/server-filesystem <path>` | 文件系统访问 |
| fetch | `npx -y @modelcontextprotocol/server-fetch` | HTTP 请求 |
| playwright | `npx -y @playwright/mcp@latest` | 浏览器自动化（需 Chrome） |

### MCP 前提条件

**Playwright MCP** 需要 Google Chrome：

```bash
# 方法 1: 系统安装
wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
sudo sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list'
sudo apt-get update
sudo apt-get install -y google-chrome-stable

# 方法 2: Playwright 安装
npx playwright install chrome
```

## 项目结构

```
gem-code/
├── src/
│   ├── cli.py              # CLI 模式
│   ├── tui.py              # TUI 模式（Textual）
│   ├── session.py          # 对话会话管理
│   ├── session_manager.py  # 会话生命周期管理
│   ├── tool.py             # 工具实现
│   ├── skill.py            # 技能系统
│   ├── mcp_client.py       # MCP 客户端
│   ├── mcp.py              # MCP 数据模型
│   ├── context_manager.py  # 上下文压缩管理
│   ├── memory.py           # 会话持久化（JsonlRandomAccess）
│   ├── models.py           # 数据模型（Message, ToolCall 等）
│   ├── config.py           # 配置管理
│   ├── decorate.py         # 终端输出装饰
│   └── agent.py            # Agent 模式（实验性）
├── main.py                 # 程序入口
├── mcp_config.json         # MCP 配置
├── pyproject.toml          # 项目依赖
└── README.md               # 本文档
```

## 核心组件

### Session (`session.py`)

管理对话历史、流式响应、工具调用循环：

```python
await session.chat(
    user_input,
    on_reasoning=lambda chunk: ...,    # 思考内容
    on_content=lambda chunk: ...,      # 正式输出
    on_turn_end=lambda content, reasoning, has_more: ...,  # 每轮结束
    on_tool_start=lambda name, args: ...,   # 工具开始
    on_tool_result=lambda name, result: ... # 工具结果
)
```

### SessionManager (`session_manager.py`)

管理会话生命周期：

```python
manager = SessionManager(config)
await manager.init()

# Fork 新会话（保留历史但创建新 ID）
manager.fork()

# 恢复已有会话
manager.resume(session_id)
```

### Context Manager (`context_manager.py`)

智能上下文管理：

- **阈值**: 最大上下文 200K tokens
  - 60% 触发 microcompaction
  - 80% 触发 autocompaction
- **压缩边界**: 在 JSONL 中标记压缩点
- **重新激活**: 从摘要恢复完整上下文（rehydration，开发中）

### Memory System (`memory.py`)

基于 JSONL 的持久化存储：

```python
# Memory_Unit 数据模型
class Memory_Unit(BaseModel):
    type: "message" | "compact_boundary" | "summary"
    role: "system" | "user" | "assistant" | "tool"
    id: UUID
    timestamp: datetime
    content: Optional[str]
    tool_calls: Optional[list[ToolCall]]

# JsonlRandomAccess - O(1) 读取
accessor = JsonlRandomAccess(filepath)
accessor.add_line(memory_unit.model_dump_json())
message = accessor.get_line(index)
```

## 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `OPENAI_API_KEY` | ✅ | - | API 密钥 |
| `OPENAI_BASE_URL` | ✅ | - | API 基础 URL |
| `OPENAI_MODEL` | ❌ | MiniMax-M2.5 | 模型名称 |
| `OPENAI_API_MODE` | ❌ | auto | `auto` / `chat_completions` / `responses` |
| `WORKDIR` | ❌ | 当前目录 | 工作目录 |
| `SKILLS_DIR` | ❌ | `WORKDIR/.agents` | 技能目录 |
| `MCP_CONFIG_PATH` | ❌ | - | MCP 配置文件路径 |
| `MEMORY_COMPACTION_PATH` | ❌ | `~/.gem_code/projects` | 会话压缩与持久化目录 |

## 技术栈

- **Python**: 3.12+
- **包管理器**: [uv](https://docs.astral.sh/uv/)
- **API**: OpenAI SDK (>=2.21.0)
- **MCP**: [mcp](https://github.com/modelcontextprotocol/python-sdk) (>=1.26.0)
- **TUI**: [Textual](https://textual.textualize.io/) (>=0.85.0)
- **终端**: Rich (>=14.3.3)
- **数据验证**: Pydantic (>=2.0)

## 开发状态

本项目参考 Claude Code 和 Kimi CLI 设计：
<https://github.com/MoonshotAI/kimi-cli>

### 已实现 ✅

- [x] TUI 界面（基于 Textual）
- [x] MCP (Model Context Protocol) 支持
- [x] 分离显示大模型思考内容和输出内容
- [x] 按需加载 Skill
- [x] 上下文管理
  - [x] Microcompaction（工具输出转移）
  - [x] Autocompaction（自动摘要、自动读取最近的文件并重启TODO）
- [x] 会话持久化（Session Memory）
- [x] 使用 Pydantic 优化数据模型
- [x] tool search支持

### 开发中 🚧

- [x] OpenAI API Responses 适配（通过 `OPENAI_API_MODE=responses|auto` 启用）
  - 参考: <https://developers.openai.com/api/reference/resources/responses>
- [x] MCP Streamable HTTP 传输支持（兼容 legacy SSE）
  - 参考: <https://modelcontextprotocol.io/specification/2025-06-18/basic/transports>
- [x] 基础 Harbor Installed Agent 适配骨架
  - 参考: <https://harborframework.com/docs/agents>
- [ ] 多 API 支持（DeepSeek、Kimi、OpenAI 等）
- [ ] Agent Teams（多 Agent 协作）（多agent的有效性有待商榷，暂时搁置）
  - 领导 Agent 任务分配
  - 基于文件系统的 Agent 间通信
  - 参考文献: <https://decodeclaude.com/teams-and-swarms/>
- [x] 基于 Harbor 的完整 Coding Agent 测试矩阵
  - 已补充 Harbor Installed Agent 适配、ATIF 轨迹落盘、结构化运行元数据采集，以及面向官方 `swebench-verified@1.0` 数据集的 runner：`evaluation/run_swebench_verified.py`
  - 本地 synthetic smoke matrix 仍保留在 `evaluation/harbor_matrix/`，仅用于快速回归 adapter、context 管理和 TUI/CLI 行为，不再作为正式 benchmark 主入口
  - 初始 SWE-bench Verified 采样运行示例：
    - `uv run python evaluation/run_swebench_verified.py --n-tasks 10`
  - 参考: <https://harborframework.com/docs/agents>
- [ ] 增加用户交互（human in the loop），针对开发者，主动披露下一步行动的目的和具体操作。支持agent运行时的interupt。便于开发者及时阻止错误方向
- [ ] Plan Mode
- [ ] TODO List
- [ ] 增加审查者角色的LLM
- [ ] subagent
- [ ] 基于slime框架实现Agentic RL

## 安全提示

⚠️ 请勿将包含 API 密钥的 `.env` 文件提交到版本控制。

## 参考链接

- Agent Skills 协议: <https://agentskills.io/home>
- MCP 协议: <https://modelcontextprotocol.io/docs/getting-started/intro>
- OpenCode: <https://github.com/anomalyco/opencode>
- Mem0 技术报告: <https://arxiv.org/abs/2504.19413>
- MiniMax API 文档: <https://platform.minimaxi.com/docs/api-reference/text-chat>
- Claude Code 博客: <https://claude.com/blog>
- Claude Code 上下文压缩: <https://decodeclaude.com/compaction-deep-dive/>
- Claude Code 会话记忆: <https://decodeclaude.com/session-memory/>
- Kimi API参考<https://platform.moonshot.cn/docs/guide/kimi-k2-5-quickstart#%E5%8F%82%E6%95%B0%E5%8F%98%E5%8A%A8%E8%AF%B4%E6%98%8E>
- Minimax API参考<https://platform.minimaxi.com/docs/api-reference/text-openai-api>
- Deepseek API参考<https://api-docs.deepseek.com/zh-cn/>

## 一点想法
对于coding agent甚至大部分agent来说，我认为应该具有以下几点功能：
- **可观测性/便于观测性**：大模型的执行过程可能需要人的介入，为了方便人类使用者，agent重点输出的应该是方便供使用者检查的
内容以及对应格式。
- **分层文件系统**：对于LLM需要的任何较大的信息源，都应该进行知识的分层，顶层到底层由概括到细致。这将大大有利于AI的信息提取。目前的agent方案要么使用不包含结构信息的RAG，要么使用Glob和Grep工具针对关键词进行查找。这两种方案效率都是较低的。
- **面向Agent的工作环境**：LLM的参数越来越多，能力越来越强，但是想要更好的发挥出它的潜力，一个精心设计的、面向LLM而不是面向人的的工作环境至关重要。其中工具设计、上下文工程和针对agent设计的记忆功能都是重点.
- **Agent对人类的安全性**：AI技术的一切发展都以人类为中心，为了防止Agent的能力范围超出人类掌握，需要有严格的安全措施，这在目前是较少谈论的。
## 许可证

MIT License
