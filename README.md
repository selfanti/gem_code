# Gem Code CLI Agent

一个轻量级、可扩展的 CLI Agent，基于 OpenAI 兼容 API 提供交互式对话能力，支持工具调用、技能扩展和流式响应。

## 特性

- 🤖 **多模型支持**：支持 OpenAI 兼容格式的 API（默认使用 MiniMax-M2.5）
- 🛠️ **工具调用**：内置 Bash 命令执行、文件读写、字符串替换等工具
- 📁 **技能扩展**：支持通过 SKILL.md 文件扩展专业能力
- 💬 **流式响应**：实时显示 AI 回复
- 🔄 **自动工具循环**：工具执行后自动继续对话，直到获得最终答案
- 🖥️ **TUI 界面**：基于 Textual 的现代化终端用户界面（类似 OpenCode）
- 🎨 **终端美化**：使用 Rich 库提供彩色、格式化的终端输出
- 📜 **历史记录**：维护对话上下文，支持多轮交互

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
SKILLS_DIR=~/gem_code/.agents/skills  # 可选，技能目录
```

**注意**：程序不会自动加载 `.env` 文件，你需要手动导出环境变量或使用 `source`：

```bash
export $(cat .env | xargs)
```

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
│ ⚡ Gem Code        │  🤖 GEM                    14:32:23 │
│ ─────────────────  │  ───────────────────────────────── │
│ MODEL              │  Hello! How can I help you today?  │
│   Name: MiniMax... │                                    │
│   API: api.mini... │  👤 YOU                    14:32:45│
│ WORKSPACE          │  ───────────────────────────────── │
│   ~/gem_code       │  Write a Python function...        │
│ FILES              │                                    │
│ ▼ 📁 gem_code      │  🤔 Thinking...                    │
│   📄 main.py       │                                    │
│   📄 pyproject...  │  [Send a message...] [Send] [Clear]│
│   📁 src           │                                    │
└─────────────────────────────────────────────────────────┘
```

界面特点：
- **左侧边栏**：显示模型信息、工作目录和可展开的文件树
- **主聊天区域**：显示对话历史，支持 Markdown 渲染和代码高亮
- **实时流式响应**：AI 回复逐字显示，带有思考动画
- **底部输入框**：支持多行输入（Shift+Enter 换行）
- **状态栏**：显示当前模型和连接状态

快捷键：

| 快捷键 | 功能 |
|--------|------|
| `Enter` | 发送消息 |
| `Shift+Enter` | 插入换行 |
| `Ctrl+C` | 退出应用 |
| `Ctrl+L` | 清空聊天记录 |
| `Ctrl+S` | 切换侧边栏显示 |
| `?` | 显示帮助 |
| `Escape` | 取消/聚焦输入框 |

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

Agent 可以自动调用以下工具：

| 工具 | 描述 | 参数 |
|------|------|------|
| `bash` | 执行 Shell 命令 | `command`, `description` |
| `read_file` | 读取文件内容 | `path`, `description` |
| `write_file` | 写入文件内容 | `path`, `content`, `description` |
| `StrReplaceFile` | 基于字符串匹配替换文件内容 | `path`, `edits` |
| `fetch_url` | 获取 URL 内容并转换为 Markdown | `url`, `description` |

工具输出会自动截断（超过 32000 字符时保留头部和尾部）。

### 技能扩展

技能是扩展 Agent 专业能力的方式。在 `SKILLS_DIR` 目录下创建子文件夹，每个文件夹包含一个 `SKILL.md` 文件：

```
.agents/skills/
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

## 项目结构

```
gem-code/
├── src/
│   ├── cli.py              # 命令行交互界面（CLI 模式）
│   ├── tui.py              # 终端用户界面（TUI 模式，基于 Textual）
│   ├── config.py           # 配置管理和数据模型
│   ├── session.py          # 对话会话管理（流式响应、工具调用循环）
│   ├── tool.py             # 工具实现（bash、文件操作等）
│   ├── skill.py            # 技能加载和管理
│   └── decorate.py         # 终端颜色装饰函数
├── main.py                 # 程序入口，支持 CLI/TUI 模式切换
├── .agents/
│   └── skills/             # 技能目录
│       └── requesting-code-review/
│           └── SKILL.md
├── .env_example            # 环境变量示例
├── pyproject.toml          # 项目配置和依赖
└── uv.lock                 # 依赖锁定文件
```

## 核心模块

### TUI (tui.py)

基于 [Textual](https://textual.textualize.io/) 的现代化终端界面，灵感来自 OpenCode：

主要组件：

| 组件 | 说明 |
|------|------|
| **ChatArea** | 滚动聊天记录区域，支持 Markdown 渲染和代码块高亮 |
| **ChatMessageWidget** | 单条消息显示，带角色头像和时间戳 |
| **StreamingMessageWidget** | 实时流式消息显示，逐字更新 |
| **InputArea** | 输入框和操作按钮（Send / Clear） |
| **Sidebar** | 侧边栏显示模型信息、工作目录、可展开的文件树 |
| **StatusBar** | 底部状态栏，显示模型名称和当前状态 |
| **ThinkingIndicator** | 思考动画指示器 |
| **HelpScreen** | 快捷键帮助弹窗 |

### CLI (cli.py)

传统命令行界面：
- 处理命令行参数
- 交互式输入循环
- 异常处理和程序退出

### Session 会话管理 (session.py)

`Session` 类负责：
- 维护对话历史（包括 system、user、assistant、tool 消息）
- 处理流式响应，实时显示 AI 回复
- 管理工具调用循环（调用工具 → 获取结果 → 继续对话）
- 异步加载和集成技能

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

## 工作流程

1. 用户输入问题
2. 发送给 AI 模型（附带工具定义）
3. 流式接收响应：
   - 如果是普通文本：直接显示
   - 如果是工具调用：执行工具，将结果追加到历史，重新请求
4. 循环直到获得最终答案（无工具调用）

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
- **TUI 框架**: [Textual](https://textual.textualize.io/) (>=0.85.0)
- **终端输出**: Rich (>=14.3.3)
- **网页抓取**: trafilatura (>=2.0.0)
- **测试**: pytest (>=9.0.2)
## TODO
- [x] TUI 界面
- [ ] 分离显示大模型思考内容和输出内容
- [ ] 上下文压缩
- [ ] OpenAI API Response 适配
- [ ] 适配 MCP (Model Context Protocol)
## 常见问题

### TUI 界面无法启动

确保终端支持 ANSI 转义序列，并尝试：
```bash
export $(cat .env | xargs)
uv run python main.py
```

### 文件树无法展开

检查 `WORKDIR` 环境变量是否正确设置。支持 `~` 家目录符号：
```bash
WORKDIR=~/your-project  # 会自动展开为 /home/user/your-project
```

### 流式响应卡顿

某些终端模拟器可能会限制渲染性能。尝试：
- 使用更现代的终端（如 Windows Terminal、iTerm2、Alacritty）
- 减小 `max_tokens` 参数

## 安全提示

⚠️ **重要**：
- 请勿将包含真实 API 密钥的 `.env` 文件提交到版本控制
- 请确保 `.env` 已在 `.gitignore` 中
- bash 工具会执行用户工作目录下的任意命令，请谨慎使用

## 许可证

MIT License
