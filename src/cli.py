from .config import Config,load_config
import sys
from rich import print
from rich.console import Console
from rich.text import Text
import asyncio
from .session import Session
from .session_manager import SessionManager
from .decorate import pc_gray,pc_blue,pc_cyan,pc_magenta
import readline
console=Console()
async def async_input(prompt: str = "") -> str:
    return input(prompt)

# CLI 模式：分别处理 reasoning 和 content
def on_reasoning(chunk: str):
    """处理 reasoning（思考过程）- 使用暗淡颜色"""
    console.print(Text(chunk, style="dim"), end="")

def on_content(chunk: str):
    """处理正式 content - 使用蓝色"""
    console.print(Text(chunk,style="blue"), end="")

def on_tool_start(tool_name: str, args: dict):
    """处理 tool 开始调用"""
    console.print(pc_blue(f"\n🛠️  Executing tool: {tool_name}"))

def on_tool_result(tool_name: str, result: str):
    """处理 tool 执行结果"""
    console.print(pc_blue(f"👁 OBSERVE\n{result}\n"))
async def main(initial_prompt: str | None = None):
    session: SessionManager | None = None
    try:
        config = load_config()
    except Exception as e:
        print(f"[red]Error loading config: {e}[/]")
        return
    
    console.print(pc_cyan("""
╔══════════════════════════════════════╗
║     Gem Code CLI Agent v1.0.0        ║
║     按 Ctrl+C 或输入 exit 退出        ║
╚══════════════════════════════════════╝
"""))
    
    try:
        session = SessionManager(config)
        await session.init()
        
        if initial_prompt:
            console.print(pc_gray(f"User input from command line: {initial_prompt}"))
            await session.session.chat(initial_prompt, on_reasoning=on_reasoning, on_content=on_content, on_tool_start=on_tool_start, on_tool_result=on_tool_result)
            console.print()
        
        while True:
            try:
                user_input =await async_input("➜ ")
                if not user_input or user_input.lower() == "exit":
                    if user_input:
                        console.print(pc_gray("Exiting..."))
                    break
                if not user_input.strip():
                    continue
                console.print(pc_gray(f"➜ User: {user_input}"))
                await session.session.chat(user_input, on_reasoning=on_reasoning, on_content=on_content, on_tool_start=on_tool_start, on_tool_result=on_tool_result)
                console.print()
            except EOFError:
                console.print(pc_gray("\nExiting..."))
                break
            except KeyboardInterrupt:
                console.print(pc_gray("\nExiting..."))
                break
    finally:
        # 确保资源被清理
        if session:
            await session.session.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[red]Unexpected error: {e}[/]")