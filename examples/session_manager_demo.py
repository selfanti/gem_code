"""
SessionManager 使用示例

演示如何使用 SessionManager 管理会话：
1. 创建新会话
2. 保存和加载会话
3. 暂停和恢复会话
4. Fork 会话
5. 列出和删除会话
6. 检查点管理
7. 导入导出会话
"""

import asyncio
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import load_config, Config
from src.session_manager import SessionManager, SessionStatus
from rich.console import Console
from rich.table import Table

console = Console()


async def demo_create_and_manage_sessions():
    """演示创建和管理会话"""
    console.print("\n[bold blue]=== Session Manager Demo ===[/bold blue]\n")
    
    # 加载配置
    config = load_config()
    
    # 创建 SessionManager
    manager = SessionManager(config)
    
    try:
        # 1. 创建新会话
        console.print("[green]1. Creating a new session...[/green]")
        session_id = await manager.create_session(
            name="demo-session",
            description="This is a demo session"
        )
        console.print(f"   Created session: {session_id[:8]}...\n")
        
        # 2. 模拟一些对话（添加消息到历史）
        console.print("[green]2. Adding some messages...[/green]")
        from src.config import Message
        session = manager.get_current_session()
        if session:
            session.history.append(Message(
                role="user",
                content="Hello, can you help me with Python?"
            ))
            session.history.append(Message(
                role="assistant",
                content="Of course! I'd be happy to help you with Python. What would you like to know?"
            ))
        console.print("   Added 2 messages\n")
        
        # 3. 手动保存会话
        console.print("[green]3. Saving session...[/green]")
        await manager.save_current_session(force=True)
        console.print("   Session saved\n")
        
        # 4. 列出所有会话
        console.print("[green]4. Listing all sessions...[/green]")
        sessions = manager.list_sessions()
        
        table = Table(title="Sessions")
        table.add_column("ID", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Status", style="yellow")
        table.add_column("Messages", style="blue")
        table.add_column("Updated", style="magenta")
        
        for s in sessions:
            table.add_row(
                s.session_id[:8],
                s.name,
                s.status,
                str(s.message_count),
                s.updated_at[:19]
            )
        console.print(table)
        console.print()
        
        # 5. 创建检查点
        console.print("[green]5. Creating checkpoint...[/green]")
        checkpoint_id = await manager.create_checkpoint("Before adding more content")
        console.print(f"   Created checkpoint: {checkpoint_id[:8]}...\n")
        
        # 6. Fork 会话
        console.print("[green]6. Forking session...[/green]")
        forked_id = await manager.fork_session(
            name="demo-session-fork",
            description="Forked from demo-session"
        )
        console.print(f"   Forked session: {forked_id[:8]}...\n")
        
        # 7. 暂停当前会话
        console.print("[green]7. Pausing current session...[/green]")
        await manager.pause_session()
        console.print("   Session paused\n")
        
        # 8. 恢复会话
        console.print("[green]8. Resuming session...[/green]")
        await manager.resume_session(session_id)
        console.print("   Session resumed\n")
        
        # 9. 导出会话
        console.print("[green]9. Exporting session...[/green]")
        export_path = manager.export_session(output_path="/tmp/demo-export.json")
        console.print(f"   Exported to: {export_path}\n")
        
        # 10. 查看会话信息
        console.print("[green]10. Getting session info...[/green]")
        info = manager.get_session_info(session_id)
        if info:
            console.print(f"   Name: {info.name}")
            console.print(f"   Description: {info.description}")
            console.print(f"   Workdir: {info.workdir}")
            console.print(f"   Messages: {info.message_count}")
        console.print()
        
        # 11. 列出检查点
        console.print("[green]11. Listing checkpoints...[/green]")
        checkpoints = manager.list_checkpoints(session_id)
        if checkpoints:
            cp_table = Table(title="Checkpoints")
            cp_table.add_column("ID", style="cyan")
            cp_table.add_column("Description", style="green")
            cp_table.add_column("Time", style="magenta")
            for cp in checkpoints:
                cp_table.add_row(
                    cp.checkpoint_id[:8],
                    cp.description,
                    cp.timestamp[:19]
                )
            console.print(cp_table)
        console.print()
        
        console.print("[bold green]Demo completed successfully![/bold green]")
        
    finally:
        # 清理
        await manager.cleanup()


async def demo_session_workflow():
    """演示完整的工作流程"""
    console.print("\n[bold blue]=== Session Workflow Demo ===[/bold blue]\n")
    
    config = load_config()
    manager = SessionManager(config)
    
    try:
        # 场景：开发者正在进行一个功能开发
        console.print("[yellow]Scenario: Feature Development Workflow[/yellow]\n")
        
        # 1. 开始新功能开发会话
        console.print("→ Starting a new feature development session...")
        session_id = await manager.create_session(
            name="feature-user-auth",
            description="Implementing user authentication feature"
        )
        
        # 2. 进行一些工作...
        console.print("→ Working on the feature (simulated)...")
        await asyncio.sleep(1)
        
        # 3. 创建检查点（实现里程碑）
        console.print("→ Creating checkpoint after login form implementation...")
        await manager.create_checkpoint("Login form implemented")
        
        # 4. 继续工作...
        console.print("→ Continuing work...")
        await asyncio.sleep(1)
        
        # 5. 暂停工作（下班/休息）
        console.print("→ Pausing session for the day...")
        await manager.pause_session()
        
        # 6. 第二天恢复工作
        console.print("→ Next day: Resuming session...")
        await manager.resume_session(session_id)
        
        # 7. 需要尝试不同方案 - Fork 会话
        console.print("→ Forking session to try alternative approach...")
        fork_id = await manager.fork_session(
            name="feature-user-auth-alt",
            description="Alternative approach for user auth"
        )
        
        # 8. 在 fork 的会话中工作
        console.print("→ Working on alternative approach...")
        
        # 9. 决定回到原方案
        console.print("→ Deciding to go back to original approach...")
        await manager.resume_session(session_id)
        
        # 10. 导出会话归档
        console.print("→ Exporting session for documentation...")
        manager.export_session(output_path="/tmp/feature-auth-session.json")
        
        console.print("\n[bold green]Workflow demo completed![/bold green]")
        
    finally:
        await manager.cleanup()


async def main():
    """主函数"""
    # 检查环境变量
    try:
        config = load_config()
    except ValueError as e:
        console.print(f"[red]Configuration error: {e}[/red]")
        console.print("[yellow]Please set OPENAI_API_KEY and OPENAI_BASE_URL environment variables[/yellow]")
        return
    
    # 运行演示
    await demo_create_and_manage_sessions()
    await demo_session_workflow()


if __name__ == "__main__":
    asyncio.run(main())
