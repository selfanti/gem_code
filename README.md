# Gem Code CLI Agent

一个轻量级、可扩展的 CLI Agent，基于 OpenAI 兼容 API 提供交互式对话能力，支持工具调用、技能扩展和流式响应。

## 特性

- 🤖 **多模型支持**：支持 OpenAI 兼容格式的 API（默认使用 MiniMax-M2.5）
- 🔌 **MCP 协议**：支持 Model Context Protocol，可连接外部工具服务器
- 🛠️ **工具调用**：内置 Bash 命令执行、文件读写、字符串替换等工具
- 📁 **技能扩展**：支持通过 SKILL.md 文件扩展专业能力
- 💬 **流式响应**：实时显示 AI 回复
- 🔄 **自动工具循环**：工具执行后自动继续对话，直到获得最终答案
- 🖥️ **TUI 界面**：基于 Textual 的现代化终端用户界面，性能优化（RichLog 流式渲染 + 批量更新）
- 🧠 **思考内容分离**：使用 Collapsible 组件折叠/展开模型的 reasoning 内容
- 📝 **多行输入**：支持多行文本编辑，自动调整高度（3-10 行）
- 🎨 **终端美化**：使用 Rich 库提供彩色、格式化的终端输出
- 📜 **历史记录**：维护对话上下文，支持多轮交互
- 🗑️ **一键清屏**：Clear 按钮快速清空输入和聊天记录

## 快速开始

### 1. 安装依赖

本项目使用 [uv](https://docs.astral.sh/uv/) 作为包管理器：

```bash
uv sync
```

### 2. 配置环境变量

复制示例配置文件并填写你的 API 密钥：

```bash
cp .env_example .env
```

编辑 `.env` 文件：

```bash
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=https://api.minimaxi.com/v1
OPENAI_MODEL=MiniMax-M2.5
WORKDIR=~/gem_code
SKILLS_DIR=~/gem_code/.agent/skills  # 可选，技能目录
MCP_CONFIG_PATH=~/gem_code/mcp_config.json  # 可选，MCP 配置
```

**注意**：程序会自动加载 `.env` 文件，无需手动 source。

### 3. 运行

#### TUI 模式（推荐）

启动现代化终端界面：

```bash
uv run python main.py
# 或
uv run python main.py --tui
```

界面布局：

```
┌─────────────────────────────────────────────────────────┐
│ ⚡ Gem Code        │  🤖 GEM                  14:32:23 │
│ ─────────────────  │  ───────────────────────────────── │
│ MODEL              │  我来帮你读取 README.md 文件       │
│   Name: MiniMax... │                                    │
│   API: api.mini... │  🔧 READ_FILE                      │
│ WORKSPACE          │  Calling tool: `read_file`         │
│   ~/gem_code       │  Result: # gem-code...             │
│ FILES              │                                    │
│ ▼ 📁 gem_code      │  🤖 GEM                  14:32:25 │
│   📄 main.py       │  根据 README.md，这是一个...       │
│   📄 pyproject...  │                                    │
│   📁 src           │  ┌─────────────────┐ [Send ⏎]     │
│                    │  │ 多行输入框...    │ [Clear]       │
└─────────────────────────────────────────────────────────┘
```

界面特点：
- **左侧边栏**：显示模型信息、工作目录和可展开的文件树
- **主聊天区域**：显示对话历史，支持 Markdown 渲染和代码高亮
- **实时流式响应**：AI 回复逐字显示，带有思考动画
- **思考内容折叠**：点击 "🤔 Thinking..." 可展开/折叠查看模型的推理过程
- **工具调用显示**：Tool 调用紧跟对应的 Assistant 消息，结果清晰展示
- **多行输入框**：支持多行编辑，自动调整高度（3-10行），`Enter` 换行
- **状态栏**：显示当前模型和连接状态

快捷键：

| 快捷键 | 功能 |
|--------|------|
| `Enter` | 插入新行（多行输入） |
| `Ctrl+C` | 退出应用 |
| `Ctrl+L` | 清空聊天记录 |
| `Ctrl+S` | 切换侧边栏显示 |
| `?` | 显示帮助 |
| `Escape` | 取消/聚焦输入框 |
| `Tab` | 插入缩进 |

**按钮操作**：
- **Send ⏎**：发送当前输入的消息
- **Clear**：清空当前输入框内容和聊天记录历史（等同于 `Ctrl+L`）

#### CLI 模式

使用传统命令行界面：

```bash
uv run python main.py --cli
```

或直接从命令行传入问题（自动进入 CLI 模式）：

```bash
uv run python main.py "你的问题"
```

## 使用方法

### 交互模式

启动 CLI 后进入交互模式：

```
╔══════════════════════════════════════╗
║     Gem Code CLI Agent v1.0.0        ║
║     按 Ctrl+C 或输入 exit 退出        ║
╚══════════════════════════════════════╝

➜ 你的问题或指令
```

输入 `exit` 或按 `Ctrl+C` 退出程序。

### 支持的工具

Agent 可以自动调用以下内置工具：

| 工具 | 描述 | 参数 |
|------|------|------|
| `bash` | 执行 Shell 命令 | `command`, `description` |
| `read_file` | 读取文件内容 | `path`, `description` |
| `write_file` | 写入文件内容 | `path`, `content`, `description` |
| `StrReplaceFile` | 基于字符串匹配替换文件内容 | `path`, `edits` |
| `fetch_url` | 获取 URL 内容并转换为 Markdown | `url`, `description` |

此外，通过 **MCP (Model Context Protocol)** 协议，可以连接外部工具服务器，扩展 Agent 的能力。

工具输出会自动截断（超过 32000 字符时保留头部和尾部）。

### 技能扩展

技能是扩展 Agent 专业能力的方式。在 `SKILLS_DIR` 目录下创建子文件夹，每个文件夹包含一个 `SKILL.md` 文件：

```
.agent/skills/
├── python-best-practices/
│   └── SKILL.md
├── react-patterns/
│   └── SKILL.md
└── ...
```

SKILL.md 格式示例：

```markdown
# Python 最佳实践

## 代码规范

详细说明文档内容...
- 使用 PEP8 规范
- 类型注解
...
```

Agent 会在系统提示中自动加载并引用这些技能文档。

### MCP 配置

支持 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 协议，可连接外部工具服务器。

**配置文件位置**（按优先级）：
1. `./mcp_config.json`（当前目录）
2. `~/.gem-code/mcp_config.json`
3. `~/.config/gem-code/mcp.json`
4. 或通过 `MCP_CONFIG_PATH` 环境变量指定路径

**配置文件示例** (`mcp_config.json`)：

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
      "enabled": false
    }
  }
}
```

**支持的 MCP 服务器**：

| 服务器 | 安装命令 | 功能 |
|--------|----------|------|
| `server-filesystem` | `npx -y @modelcontextprotocol/server-filesystem <path>` | 文件系统访问 |
| `server-fetch` | `npx -y @modelcontextprotocol/server-fetch` | HTTP 请求 |
| `playwright-mcp` | `npx -y @playwright/mcp@latest` | 浏览器自动化 |
| `server-everything` | `npx -y @modelcontextprotocol/server-everything` | 测试服务器 |

启动时会自动连接配置的 MCP 服务器，并将工具注册到 OpenAI API。

## 项目结构

```
gem-code/
├── src/
│   ├── cli.py              # 命令行交互界面（CLI 模式）
│   ├── tui.py              # 终端用户界面（TUI 模式，基于 Textual）
│   ├── config.py           # 配置管理和数据模型
│   ├── session.py          # 对话会话管理（流式响应、工具调用循环、MCP 集成）
│   ├── tool.py             # 工具实现（bash、文件操作、MCP 工具路由）
│   ├── skill.py            # 技能加载和管理
│   ├── mcp.py              # MCP 数据模型（McpLocal、McpRemote 等）
│   ├── mcp_client.py       # MCP 客户端实现
│   └── decorate.py         # 终端颜色装饰函数
├── main.py                 # 程序入口，支持 CLI/TUI 模式切换
├── mcp_config.example.json # MCP 配置示例
├── .agent/                 # 技能目录
│   └── skills/
│       └── ...
├── .env_example            # 环境变量示例
├── pyproject.toml          # 项目配置和依赖
└── uv.lock                 # 依赖锁定文件
```

## 核心模块

### TUI (tui.py)

基于 [Textual](https://textual.textualize.io/) 的现代化终端界面，灵感来自 OpenCode：

**性能优化特点**：
- **双层渲染架构**：流式阶段使用 RichLog（高性能文本组件），完成后自动转换为 Markdown（美观渲染）
- **智能批量更新**：每 20 个字符或每 50ms 更新一次 UI，避免过度刷新
- **增量追加**：只渲染新增内容，不重复渲染已有文本
- **分轮次消息**：每次 API 调用产生独立消息，Tool 调用与对应 Content 正确分组

**思考内容折叠**：
- 使用 `Collapsible` 组件折叠 reasoning 内容
- 默认折叠，点击 "🤔 Thinking..." 展开查看
- 独立的 CSS 样式（暗淡背景、斜体标题）

主要组件：

| 组件 | 说明 |
|------|------|
| **ChatArea** | 滚动聊天记录区域，管理消息生命周期 |
| **ChatMessageWidget** | 已完成消息的 Markdown 渲染显示，支持 reasoning 折叠 |
| **OptimizedStreamingWidget** | 高性能流式消息组件（RichLog + Markdown 转换） |
| **InputArea** | 多行文本输入框（TextArea），支持自动高度调整，Send/Clear 按钮 |
| **Sidebar** | 侧边栏显示模型信息、工作目录、可展开的文件树 |
| **StatusBar** | 底部状态栏，显示模型名称和当前状态 |
| **ThinkingIndicator** | 思考动画指示器 |
| **HelpScreen** | 快捷键帮助弹窗 |

可调整的性能参数（`src/tui.py`）：
```python
BATCH_SIZE = 20          # 每 20 个字符更新一次 UI
BATCH_INTERVAL = 0.05    # 或每 50ms 更新一次
MAX_LOG_LINES = 1000     # RichLog 最大行数限制
```

### CLI (cli.py)

传统命令行界面：
- 处理命令行参数
- 交互式输入循环
- 异常处理和程序退出
- 支持 reasoning/content 分离显示（使用不同颜色）

### Session 会话管理 (session.py)

`Session` 类负责：
- 维护对话历史（包括 system、user、assistant、tool 消息）
- 处理流式响应，实时显示 AI 回复
- 管理工具调用循环（调用工具 → 获取结果 → 继续对话）
- 异步加载和集成技能
- 初始化 MCP 客户端并合并 MCP 工具到 OpenAI API 调用

**回调架构**：
```python
await session.chat(
    user_input,
    on_reasoning=lambda chunk: ...,    # 思考内容
    on_content=lambda chunk: ...,      # 正式输出
    on_turn_end=lambda content, reasoning, has_more: ...,  # 每轮结束
    on_tool_start=lambda name, args: ...,  # 工具开始
    on_tool_result=lambda name, result: ... # 工具结果
)
```

### MCP 客户端 (mcp_client.py)

`MCPClient` 类提供：
- `connect_server()`: 连接 MCP 服务器（支持 stdio 和 sse 两种传输方式）
- `get_all_tools_openai_format()`: 获取所有 MCP 工具的 OpenAI function 格式
- `call_tool()`: 执行 MCP 工具调用
- `disconnect_all()`: 清理所有连接

配置加载函数：
- `load_mcp_config_from_env()`: 从环境变量或默认位置加载配置
- `load_mcp_config_from_file()`: 从 JSON 文件加载配置

### 工具系统 (tool.py)

工具使用 OpenAI 兼容的函数调用格式定义：

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "...",
            "parameters": {...}
        }
    },
    ...
]
```

所有工具都在 `workdir` 指定的工作目录下执行。

### 技能系统 (skill.py)

- `load_skills()`: 从指定目录加载所有技能
- `parse_Skill()`: 解析 SKILL.md 文件（提取标题和描述）
- `format_skill_for_prompt()`: 将技能格式化为系统提示

### 配置管理 (config.py)

通过环境变量配置：

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `OPENAI_API_KEY` | ✅ | - | API 密钥 |
| `OPENAI_BASE_URL` | ✅ | - | API 基础 URL |
| `OPENAI_MODEL` | ❌ | MiniMax-M2.5 | 模型名称 |
| `WORKDIR` | ❌ | 当前目录 | 工作目录（工具执行位置） |
| `SKILLS_DIR` | ❌ | - | 技能目录路径 |
| `MCP_CONFIG_PATH` | ❌ | - | MCP 配置文件路径 |
| `MCP_CONFIG` | ❌ | - | MCP 配置 JSON 字符串 |

## 数据模型

### Message

```python
@dataclass
class Message:
    role: Role                    # "system", "user", "assistant", "tool"
    content: Optional[str]        # 消息内容
    tool_calls: Optional[List]    # 工具调用（assistant 消息）
    tool_call_id: Optional[str]   # 工具调用 ID（tool 消息）
```

### ToolCall

```python
@dataclass
class ToolCall:
    id: str
    function: FunctionCall
    type: str = "function"

@dataclass
class FunctionCall:
    name: str
    arguments: str  # JSON 字符串
```

### ChatEntry (TUI)

```python
@dataclass
class ChatEntry:
    role: str                      # "user", "assistant", "tool"
    content: str                   # 显示内容
    timestamp: datetime            # 时间戳
    is_tool_call: bool = False     # 是否是工具调用
    tool_name: str | None = None   # 工具名称
    tool_result: str | None = None # 工具结果
    reasoning_content: str | None = None  # 推理/思考内容
```

## 工作流程

1. 用户输入问题
2. 发送给 AI 模型（附带工具定义）
3. 流式接收响应：
   - 如果是普通文本：直接显示
   - 如果是工具调用：执行工具，将结果追加到历史，重新请求
4. 循环直到获得最终答案（无工具调用）

**消息分组**：
- 每次 API 调用产生一条独立的 Assistant 消息
- Tool 调用紧跟在触发它的 Assistant 消息之后
- 第二轮及以后的响应显示为新的 Assistant 消息

## 开发

### 运行测试

```bash
uv run pytest
```

### 代码规范

- 遵循 PEP8 命名规范
- 使用类型注解
- 使用 dataclass 定义数据结构
- 异步函数使用 `async/await`

## 技术栈

- **Python**: 3.12+
- **包管理器**: [uv](https://docs.astral.sh/uv/)
- **API 客户端**: OpenAI Python SDK (>=2.21.0)
- **MCP SDK**: [mcp](https://github.com/modelcontextprotocol/python-sdk) (>=1.26.0)
- **TUI 框架**: [Textual](https://textual.textualize.io/) (>=0.85.0)
- **终端输出**: Rich (>=14.3.3)
- **网页抓取**: trafilatura (>=2.0.0)
- **测试**: pytest (>=9.0.2)

## TODO

- [x] TUI 界面
- [x] MCP (Model Context Protocol) 支持
- [x] 分离显示大模型思考内容和输出内容
- [ ] 上下文压缩
- [ ] OpenAI API Response 适配
- [ ] 对话历史持久化
- [ ] 消息编辑/重试功能

## 常见问题

### TUI 界面无法启动

确保终端支持 ANSI 转义序列，并尝试：
```bash
uv run python main.py
```

### 文件树无法展开

检查 `WORKDIR` 环境变量是否正确设置。支持 `~` 家目录符号：
```bash
WORKDIR=~/your-project  # 会自动展开为 /home/user/your-project
```

### Clear 按钮功能

Clear 按钮会同时执行以下操作：
1. 清空输入框中的当前内容
2. 清空聊天记录历史
3. 重置对话上下文

注意：此操作不可撤销，请确认后再点击。

### 流式响应卡顿

TUI 已针对性能进行优化：
- **流式阶段**：使用 RichLog 高性能组件（比 Markdown 快 10 倍以上）
- **批量更新**：每 20 个字符或 50ms 刷新一次 UI
- **完成后渲染**：流式结束后自动转换为 Markdown 保持格式

如果仍然卡顿，尝试：
- 使用更现代的终端（如 Windows Terminal、iTerm2、Alacritty、WezTerm）
- 减小 `max_tokens` 参数
- 检查终端字体渲染设置

### Reasoning 内容未显示

确保：
1. API 支持 `reasoning_split` 参数（MiniMax M2.5 支持）
2. 模型支持 reasoning（M2.5 支持，M1 不支持）
3. 在 TUI 中点击 "🤔 Thinking..." 可以展开查看

## 安全提示

⚠️ **重要**：
- 请勿将包含真实 API 密钥的 `.env` 文件提交到版本控制
- 请确保 `.env` 已在 `.gitignore` 中
- bash 工具会执行用户工作目录下的任意命令，请谨慎使用

## 许可证

MIT License
