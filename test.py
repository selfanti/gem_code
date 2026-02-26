import json
from typing import Optional, Callable, List, Dict, Any, TypedDict, Union
from rich.console import Console
from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionChunk

console = Console()

# ==================== 类型定义 ====================
class ToolFunction(TypedDict):
    name: str
    arguments: str

class ToolCall(TypedDict):
    id: str
    type: str
    function: ToolFunction

class Message(TypedDict, total=False):
    role: str
    content: Optional[str]
    tool_calls: Optional[List[ToolCall]]
    tool_call_id: Optional[str]  # tool 角色消息使用

# ==================== 假设的导入（需根据实际项目调整） ====================
# from .config import create_openai_client, get_system_prompt, Message, ToolCall
# from .tools import TOOLS, run_tool, parse_tool_arguments

def get_system_prompt() -> str:
    return "You are a helpful assistant."

def create_openai_client(api_key: str, base_url: str) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key, base_url=base_url)

# 工具定义示例
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute shell command",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

async def run_tool(name: str, args: Dict[str, Any], workdir: str) -> str:
    """工具执行函数（示例）"""
    return f"Executed {name} with {args} in {workdir}"

def parse_tool_arguments(tool_call: ToolCall) -> Dict[str, Any]:
    try:
        return json.loads(tool_call["function"]["arguments"])
    except (json.JSONDecodeError, KeyError):
        return {}

# ==================== 核心类 ====================
class NanoSession:
    def __init__(self, api_key: str, base_url: str, model: str, workdir: str):
        self.client = create_openai_client(api_key, base_url)
        self.workdir = workdir
        self.model = model
        self.history: List[Message] = [
            {"role": "system", "content": get_system_prompt()}
        ]

    async def chat(
        self, 
        user_input: str, 
        on_chunk: Optional[Callable[[str], None]] = None
    ) -> None:
        """
        处理用户输入，支持流式输出和工具调用
        
        Args:
            user_input: 用户输入文本
            on_chunk: 可选的回调函数，用于实时接收流式输出
        """
        # 添加用户消息
        self.history.append({"role": "user", "content": user_input})

        while True:
            console.print("[dim]🤖 Thinking...[/]")

            # 发起流式请求
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=self.history,  # type: ignore
                tools=TOOLS,  # type: ignore
                tool_choice="auto",
                stream=True
            )

            full_content = ""
            has_tool_calls = False
            tool_calls_map: Dict[str, ToolCall] = {}

            # 处理流式响应
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                
                if not delta:
                    continue

                # 处理文本内容
                if delta.content:
                    full_content += delta.content
                    if on_chunk:
                        on_chunk(delta.content)

                # 处理工具调用
                if delta.tool_calls:
                    has_tool_calls = True
                    for tc in delta.tool_calls:
                        tc_id = tc.id
                        tc_func = tc.function
                        
                        if tc_id:
                            # 新的工具调用
                            if tc_id in tool_calls_map:
                                # 追加参数
                                if tc_func and tc_func.arguments:
                                    tool_calls_map[tc_id]["function"]["arguments"] += tc_func.arguments
                            else:
                                # 创建新条目
                                tool_calls_map[tc_id] = {
                                    "id": tc_id,
                                    "type": "function",
                                    "function": {
                                        "name": tc_func.name if tc_func else "",
                                        "arguments": tc_func.arguments if tc_func else ""
                                    }
                                }
                        elif tc_func and tc_func.arguments:
                            # 无 id 的参数片段（DeepSeek 行为）
                            # 追加到最后一个工具调用
                            if tool_calls_map:
                                last_key = list(tool_calls_map.keys())[-1]
                                tool_calls_map[last_key]["function"]["arguments"] += tc_func.arguments

            # 构造助手消息
            message: Message = {
                "role": "assistant",
                "content": full_content if full_content else None,
                "tool_calls": list(tool_calls_map.values()) if has_tool_calls else None
            }

            self.history.append(message)

            # 如果有工具调用，执行它们并继续循环
            if has_tool_calls and message.get("tool_calls"):
                console.print()

                for tool_call in message["tool_calls"]:
                    args = parse_tool_arguments(tool_call)
                    result = await run_tool(
                        tool_call["function"]["name"],
                        args,
                        self.workdir
                    )
                    
                    console.print("[blue]👁 OBSERVE[/]")
                    console.print(f"[dim]{result}[/]")
                    console.print()

                    # 添加工具结果到历史
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result
                    })

                console.print("[magenta]🔄 REPEAT[/]")
                continue  # 继续循环，将工具结果发给模型

            break  # 没有工具调用，结束

    def get_history(self) -> List[Message]:
        """获取对话历史"""
        return self.history

    def clear_history(self) -> None:
        """清空历史，保留系统提示词"""
        self.history = [{"role": "system", "content": get_system_prompt()}]

# ==================== 使用示例 ====================
async def main():
    session = NanoSession(
        api_key="sk-xxx",
        base_url="https://api.openai.com/v1",
        model="gpt-4",
        workdir="/tmp"
    )
    
    def print_chunk(text: str):
        print(text, end="", flush=True)
    
    await session.chat("查看当前目录文件", on_chunk=print_chunk)
    print("\n\n历史记录:", session.get_history())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())