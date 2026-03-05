# Gem Code CLI Agent

轻量级 CLI Agent，基于 OpenAI 兼容 API，支持工具调用、MCP 协议和技能扩展。

## 特性

- 🤖 **多模型支持**：OpenAI 兼容 API（默认 MiniMax-M2.5）
- 🔌 **MCP 协议**：连接外部工具服务器
- 🛠️ **内置工具**：Bash、文件读写、字符串替换、URL 抓取
- 📁 **技能扩展**：通过 SKILL.md 扩展专业能力
- 💬 **流式响应**：实时显示，支持思考内容分离
- 🖥️ **双模式**：TUI（推荐）和 CLI 模式

## 快速开始

### 1. 安装

```bash
uv sync
```

### 2. 配置

```bash
cp .env_example .env
```

编辑 `.env`：

```bash
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.minimaxi.com/v1
OPENAI_MODEL=MiniMax-M2.5
WORKDIR=~/gem_code
SKILLS_DIR=~/gem_code/.agent/skills  # 可选
MCP_CONFIG_PATH=~/gem_code/mcp_config.json  # 可选
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

### 内置工具

| 工具 | 描述 | 参数 |
|------|------|------|
| `bash` | 执行 Shell 命令 | `command`, `description` |
| `read_file` | 读取文件 | `path`, `description` |
| `write_file` | 写入文件 | `path`, `content`, `description` |
| `StrReplaceFile` | 字符串替换 | `path`, `edits` |
| `fetch_url` | 抓取 URL | `url`, `description` |

### 技能扩展

在 `SKILLS_DIR` 目录创建子文件夹，每个文件夹包含 `SKILL.md`：

```
.agent/skills/
├── python-best-practices/
│   └── SKILL.md
└── react-patterns/
    └── SKILL.md
```

SKILL.md 格式：

```markdown
# 技能名称

## 描述

详细说明内容...
```

### MCP 配置

创建 `mcp_config.json`：

```json
{
  "mcpServers": {
    "filesystem": {
      "type": "local",
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
      "enabled": true
    },
    "fetch": {
      "type": "local",
      "command": ["npx", "-y", "@modelcontextprotocol/server-fetch"],
      "enabled": true
    }
  }
}
```

常用 MCP 服务器：

| 服务器 | 命令 |
|--------|------|
| filesystem | `npx -y @modelcontextprotocol/server-filesystem <path>` |
| fetch | `npx -y @modelcontextprotocol/server-fetch` |
| playwright | `npx -y @playwright/mcp@latest` |

## 项目结构

```
gem-code/
├── src/
│   ├── cli.py          # CLI 模式
│   ├── tui.py          # TUI 模式（Textual）
│   ├── session.py      # 对话会话管理
│   ├── tool.py         # 工具实现
│   ├── skill.py        # 技能系统
│   ├── mcp_client.py   # MCP 客户端
│   └── config.py       # 配置管理
├── main.py             # 程序入口
├── mcp_config.json     # MCP 配置
└── pyproject.toml      # 项目依赖
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

## 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `OPENAI_API_KEY` | ✅ | - | API 密钥 |
| `OPENAI_BASE_URL` | ✅ | - | API 基础 URL |
| `OPENAI_MODEL` | ❌ | MiniMax-M2.5 | 模型名称 |
| `WORKDIR` | ❌ | 当前目录 | 工作目录 |
| `SKILLS_DIR` | ❌ | - | 技能目录 |
| `MCP_CONFIG_PATH` | ❌ | - | MCP 配置文件路径 |

## 技术栈

- **Python**: 3.12+
- **包管理器**: [uv](https://docs.astral.sh/uv/)
- **API**: OpenAI SDK (>=2.21.0)
- **MCP**: [mcp](https://github.com/modelcontextprotocol/python-sdk) (>=1.26.0)
- **TUI**: [Textual](https://textual.textualize.io/) (>=0.85.0)
- **终端**: Rich (>=14.3.3)

## TODO

- [x] TUI 界面
- [x] MCP (Model Context Protocol) 支持
- [x] 分离显示大模型思考内容和输出内容
- [x] 按需加载skill
- [ ] 上下文管理
- [ ] OpenAI API Response/Anthropic API 适配
- [ ] 对话历史持久化
- [ ] 消息编辑/重试功能

## 安全提示

⚠️ 请勿将包含 API 密钥的 `.env` 文件提交到版本控制。

## 许可证

MIT License
