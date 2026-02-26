from config import Config,load_config
import sys
from rich import print
from rich.console import Console
import asyncio
from session import Session
from decorate import pc_gray,pc_blue,pc_cyan,pc_magenta
import readline
console=Console()
async def async_input(prompt: str = "") -> str:
    return input(prompt)
def onChunk(chunk:str):
    console.print(pc_blue(chunk), end="")
async def main():
    try:
        config=load_config()
    except Exception as e:
        print(f"[red]Error loading config: {e}[/]")
        return
    console.print(pc_cyan("""
╔══════════════════════════════════════╗
║     Gem Code CLI Agent v1.0.0        ║
║     按 Ctrl+C 或输入 exit 退出        ║
╚══════════════════════════════════════╝
"""))
    session=Session(config)
    await session.init()
    args=" ".join(sys.argv[1:])
    if args:
        console.print(pc_gray(f"User input from command line:{args}"))
        await session.chat(args,onChunk)
        console.print()
    while True:
        try:
            user_input = await async_input(("➜ "))
            if not user_input or user_input.lower()=="exit":
                if user_input:
                    console.print(pc_gray("Exiting..."))
                break
            if not user_input.strip():
                continue
            console.print(pc_gray(f"➜ User: {user_input}"))
            await session.chat(user_input,onChunk)
            console.print()
        except EOFError:
            break
        except KeyboardInterrupt:
            console.print(pc_gray("\nExiting..."))
            break
if __name__=="__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[red]Unexpected error: {e}[/]")