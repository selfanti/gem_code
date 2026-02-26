# Gem Code CLI Agent

一个轻量级、可扩展的 CLI Agent，基于 OpenAI 兼容 API 提供交互式对话能力，支持工具调用、技能扩展和流式响应。

## 特性

- 🤖 **多模型支持**：支持 OpenAI 兼容格式的 API（默认使用 MiniMax-M2.5）
- 🛠️ **工具调用**：内置 Bash 命令执行、文件读写等工具
- 📁 **技能扩展**：支持通过 SKILL.md 文件扩展专业能力
- 💬 **流式响应**：实时显示 AI 回复，支持推理内容分离
- 🎨 **终端美化**：使用 Rich 库提供彩色、格式化的终端输出

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
WORKDIR=/path/to/your/workdir
SKILLS_DIR=/path/to/skills  # 可选
```

### 3. 运行

```bash
uv run python src/cli.py
```

或者直接运行主程序：

```bash
uv run python main.py
```

## 使用方法

### 交互模式

启动 CLI 后进入交互模式：

```
╔══════════════════════════════════════╗
║     Gem Code CLI Agent v1.0.0        ║
║     输入 exit 或按 Ctrl+C 退出        ║
╚══════════════════════════════════════╝

➜ 你的问题或指令
```

### 支持的工具

Agent 可以自动调用以下工具：

| 工具 | 描述 |
|------|------|
| `bash` | 执行 Shell 命令 |
| `read_file` | 读取文件内容 |
| `write_file` | 写入文件内容 |
| `StrReplaceFile` | 基于字符串匹配替换文件内容 |

### 技能扩展

在 `SKILLS_DIR` 目录下创建子文件夹，每个文件夹包含一个 `SKILL.md` 文件：

```
skills/
├── python/
│   └── SKILL.md
├── react/
│   └── SKILL.md
└── ...
```

SKILL.md 格式：

```markdown
# 技能名称

## 技能描述

详细说明文档内容...
```

## 项目结构

```
gem-code/
├── main.py                 # 程序入口
├── src/
│   ├── cli.py             # 命令行交互界面
│   ├── config.py          # 配置管理和数据模型
│   ├── session.py         # 对话会话管理
│   ├── tool.py            # 工具实现（bash、文件操作等）
│   ├── skill.py           # 技能加载和管理
│   └── decorate.py        # 终端颜色装饰函数
├── .env_example           # 环境变量示例
├── pyproject.toml         # 项目配置和依赖
└── uv.lock               # 依赖锁定文件
```

## 核心模块

### Session 会话管理

`Session` 类负责：
- 维护对话历史
- 处理流式响应
- 管理工具调用循环
- 加载和集成技能

### 工具系统

工具定义在 `src/tool.py` 中，使用 OpenAI 兼容的函数调用格式：

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

### 配置管理

通过环境变量配置：

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `OPENAI_API_KEY` | ✅ | - | API 密钥 |
| `OPENAI_BASE_URL` | ✅ | - | API 基础 URL |
| `OPENAI_MODEL` | ❌ | MiniMax-M2.5 | 模型名称 |
| `WORKDIR` | ❌ | 当前目录 | 工作目录 |
| `SKILLS_DIR` | ❌ | - | 技能目录 |

## 开发

### 运行测试

```bash
uv run pytest
```

### 代码规范

- 遵循 PEP8 命名规范
- 使用类型注解
- 使用 dataclass 定义数据结构

## 技术栈

- **Python**: 3.12+
- **包管理器**: [uv](https://docs.astral.sh/uv/)
- **API 客户端**: OpenAI Python SDK
- **终端输出**: Rich
- **测试**: pytest

## 安全提示

⚠️ **重要**：请勿将包含真实 API 密钥的 `.env` 文件提交到版本控制。请确保 `.env` 已在 `.gitignore` 中。

## 许可证

MIT License
