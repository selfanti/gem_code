"""
MCP (Model Context Protocol) Client Implementation

提供对 MCP 服务器的连接、工具发现和调用功能。
支持 stdio (本地命令) 和 sse (远程 HTTP) 两种传输方式。

依赖: pip install mcp
"""

import asyncio
import json
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.types import CallToolResult, Tool

from .mcp import Mcp, McpLocal, McpRemote, Status, StatusConnected, StatusFailed


@dataclass
class MCPTool:
    """MCP 工具封装，兼容 OpenAI function 格式"""
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema
    server_name: str  # 来源服务器名称
    
    def to_openai_function(self) -> Dict[str, Any]:
        """转换为 OpenAI function 格式"""
        return {
            "type": "function",
            "function": {
                "name": f"mcp__{self.server_name}__{self.name}",
                "description": f"[{self.server_name}] {self.description}",
                "parameters": self.parameters
            }
        }
    
    @property
    def full_name(self) -> str:
        """完整工具名，用于路由"""
        return f"mcp__{self.server_name}__{self.name}"


@dataclass
class ServerConnection:
    """单个 MCP 服务器的连接状态"""
    name: str
    config: Mcp
    status: Status = field(default_factory=lambda: StatusFailed(status="failed", error="Not initialized"))
    session: Optional[ClientSession] = None
    tools: List[MCPTool] = field(default_factory=list)
    _exit_stack: Optional[AsyncExitStack] = None
    _stdio_task: Optional[asyncio.Task] = None


class MCPClient:
    """
    MCP 客户端管理器
    
    管理多个 MCP 服务器的连接，提供统一的工具发现和调用接口。
    
    Usage:
        client = MCPClient()
        await client.connect_server("filesystem", McpLocal(...))
        await client.connect_server("remote-api", McpRemote(...))
        
        # 获取所有工具（OpenAI 格式）
        tools = client.get_all_tools_openai_format()
        
        # 调用工具
        result = await client.call_tool("mcp__filesystem__read_file", {"path": "/tmp/test.txt"})
    """
    
    def __init__(self):
        self._servers: Dict[str, ServerConnection] = {}
        self._lock = asyncio.Lock()
    
    async def connect_server(self, name: str, config: Mcp) -> Status:
        """
        连接到指定的 MCP 服务器
        
        Args:
            name: 服务器名称标识
            config: MCP 配置（McpLocal 或 McpRemote）
            
        Returns:
            连接状态
        """
        async with self._lock:
            # 如果已存在连接，先断开
            if name in self._servers:
                await self._disconnect_server(name)
            
            conn = ServerConnection(name=name, config=config)
            self._servers[name] = conn
            
            try:
                if isinstance(config, McpLocal):
                    await self._connect_stdio(conn, config)
                elif isinstance(config, McpRemote):
                    await self._connect_sse(conn, config)
                else:
                    raise ValueError(f"Unknown config type: {type(config)}")
                
                # 连接成功后获取工具列表
                await self._refresh_tools(conn)
                conn.status = StatusConnected(status="connected")
                
            except Exception as e:
                error_msg = f"Failed to connect to server '{name}': {str(e)}"
                conn.status = StatusFailed(status="failed", error=error_msg)
            
            return conn.status
    
    async def _connect_stdio(self, conn: ServerConnection, config: McpLocal) -> None:
        """通过 stdio 连接到本地 MCP 服务器"""
        server_params = StdioServerParameters(
            command=config.command[0],
            args=config.command[1:] if len(config.command) > 1 else [],
            env={**os.environ, **(config.environment or {})}
        )
        
        exit_stack = AsyncExitStack()
        
        try:
            # 使用 exit_stack 管理生命周期
            stdio_transport = await exit_stack.enter_async_context(stdio_client(server_params))
            stdio_read, stdio_write = stdio_transport
            
            session = await exit_stack.enter_async_context(
                ClientSession(stdio_read, stdio_write)
            )
            
            # 初始化会话
            await session.initialize()
            
            conn.session = session
            conn._exit_stack = exit_stack
        except Exception:
            # 连接失败时确保清理
            try:
                await exit_stack.aclose()
            except Exception:
                pass
            raise
    
    async def _connect_sse(self, conn: ServerConnection, config: McpRemote) -> None:
        """通过 SSE 连接到远程 MCP 服务器"""
        # 注意：sse_client 需要 headers 参数
        headers = config.headers or {}
        
        exit_stack = AsyncExitStack()
        
        # 连接到 SSE 端点
        sse_transport = await exit_stack.enter_async_context(
            sse_client(config.url, headers=headers)
        )
        sse_read, sse_write = sse_transport
        
        session = await exit_stack.enter_async_context(
            ClientSession(sse_read, sse_write)
        )
        
        # 初始化会话
        await session.initialize()
        
        conn.session = session
        conn._exit_stack = exit_stack
    
    async def _refresh_tools(self, conn: ServerConnection) -> None:
        """刷新服务器的工具列表"""
        if not conn.session:
            raise RuntimeError(f"Server '{conn.name}' not connected")
        
        tools_result = await conn.session.list_tools()
        
        conn.tools = [
            MCPTool(
                name=tool.name,
                description=tool.description or "",
                parameters=tool.inputSchema,
                server_name=conn.name
            )
            for tool in tools_result.tools
        ]
    
    async def _disconnect_server(self, name: str) -> None:
        """断开指定服务器的连接"""
        conn = self._servers.get(name)
        if not conn:
            return
        
        if conn._exit_stack:
            try:
                # 使用 asyncio.wait_for 防止清理时卡住
                await asyncio.wait_for(conn._exit_stack.aclose(), timeout=5.0)
            except asyncio.TimeoutError:
                pass  # 超时，继续清理
            except Exception:
                pass  # 忽略清理错误
        
        conn.session = None
        conn.tools = []
        conn._exit_stack = None
        conn.status = StatusFailed(status="failed", error="Disconnected")
    
    async def disconnect_server(self, name: str) -> None:
        """公共方法：断开指定服务器的连接"""
        async with self._lock:
            await self._disconnect_server(name)
    
    async def disconnect_all(self) -> None:
        """断开所有服务器的连接"""
        async with self._lock:
            for name in list(self._servers.keys()):
                await self._disconnect_server(name)
            self._servers.clear()
    
    async def refresh_all_tools(self) -> None:
        """刷新所有服务器的工具列表"""
        async with self._lock:
            for conn in self._servers.values():
                if conn.session and isinstance(conn.status, StatusConnected):
                    try:
                        await self._refresh_tools(conn)
                    except Exception as e:
                        conn.status = StatusFailed(
                            status="failed", 
                            error=f"Failed to refresh tools: {str(e)}"
                        )
    
    def get_all_tools(self) -> List[MCPTool]:
        """获取所有可用工具的列表"""
        all_tools: List[MCPTool] = []
        for conn in self._servers.values():
            if isinstance(conn.status, StatusConnected):
                all_tools.extend(conn.tools)
        return all_tools
    
    def get_all_tools_openai_format(self) -> List[Dict[str, Any]]:
        """获取所有工具（OpenAI function 格式）"""
        return [tool.to_openai_function() for tool in self.get_all_tools()]
    
    def get_server_tools(self, server_name: str) -> List[MCPTool]:
        """获取指定服务器的工具列表"""
        conn = self._servers.get(server_name)
        if conn and isinstance(conn.status, StatusConnected):
            return conn.tools
        return []
    
    def get_server_status(self, name: str) -> Optional[Status]:
        """获取指定服务器的状态"""
        conn = self._servers.get(name)
        return conn.status if conn else None
    
    def get_all_status(self) -> Dict[str, Status]:
        """获取所有服务器的状态"""
        return {name: conn.status for name, conn in self._servers.items()}
    
    async def call_tool(self, full_tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        调用指定工具
        
        Args:
            full_tool_name: 完整工具名（格式: mcp__server_name__tool_name）
            arguments: 工具参数
            
        Returns:
            工具执行结果的文本内容
        """
        # 解析工具名
        if not full_tool_name.startswith("mcp__"):
            raise ValueError(f"Invalid MCP tool name: {full_tool_name}")
        
        parts = full_tool_name.split("__", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid MCP tool name format: {full_tool_name}")
        
        _, server_name, tool_name = parts
        
        async with self._lock:
            conn = self._servers.get(server_name)
            if not conn:
                raise ValueError(f"Server '{server_name}' not found")
            
            if not isinstance(conn.status, StatusConnected):
                raise RuntimeError(f"Server '{server_name}' is not connected")
            
            if not conn.session:
                raise RuntimeError(f"Server '{server_name}' session not available")
            
            # 调用工具
            result = await conn.session.call_tool(tool_name, arguments)
            
            # 解析结果
            return self._parse_tool_result(result)
    
    def _parse_tool_result(self, result: CallToolResult) -> str:
        """解析工具调用结果为文本"""
        if result.isError:
            error_contents = []
            for content in result.content:
                if content.type == "text":
                    error_contents.append(content.text)
            raise RuntimeError(f"Tool error: {' '.join(error_contents)}")
        
        # 收集所有文本内容
        texts = []
        for content in result.content:
            if content.type == "text":
                texts.append(content.text)
            elif content.type == "image":
                texts.append(f"[Image data: {content.mimeType}]")
            elif content.type == "resource":
                resource = content.resource
                if hasattr(resource, 'text') and resource.text:     #type: ignore
                    texts.append(resource.text)                     #type: ignore
                elif hasattr(resource, 'blob') and resource.blob:   #type: ignore
                    texts.append(f"[Binary resource: {resource.mimeType}]")
        
        return "\n".join(texts) if texts else "(empty result)"
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口，自动清理所有连接"""
        await self.disconnect_all()


# ============== 配置加载相关函数 ==============

def load_mcp_config_from_dict(config_dict: Dict[str, Any]) -> Dict[str, Mcp]:
    """
    从字典加载 MCP 配置
    
    支持格式：
    {
        "mcpServers": {
            "server_name": {
                "type": "local",
                "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "environment": {"KEY": "value"},
                "enabled": true,
                "timeout": 30000
            },
            "remote_server": {
                "type": "remote",
                "url": "http://localhost:3000/sse",
                "headers": {"Authorization": "Bearer token"},
                "enabled": true,
                "timeout": 30000
            }
        }
    }
    """
    servers_config = config_dict.get("mcpServers", {})
    result: Dict[str, Mcp] = {}
    
    for name, server_config in servers_config.items():
        # 跳过非字典类型的配置（如注释字段 _comment）
        if not isinstance(server_config, dict):
            continue
        
        server_type = server_config.get("type", "local")
        enabled = server_config.get("enabled", True)
        
        if not enabled:
            continue
        
        if server_type == "local":
            command = server_config.get("command", [])
            if isinstance(command, str):
                command = command.split()
            
            result[name] = McpLocal(
                type="local",
                command=command,
                environment=server_config.get("environment"),
                enabled=enabled,
                timeout=server_config.get("timeout", 30000)
            )
        
        elif server_type == "remote":
            result[name] = McpRemote(
                type="remote",
                url=server_config["url"],
                headers=server_config.get("headers"),
                oauth=server_config.get("oauth"),
                enabled=enabled,
                timeout=server_config.get("timeout", 30000)
            )
    
    return result


def load_mcp_config_from_file(config_path: str) -> Dict[str, Mcp]:
    """从 JSON 文件加载 MCP 配置"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config_dict = json.load(f)
    return load_mcp_config_from_dict(config_dict)


def load_mcp_config_from_env() -> Dict[str, Mcp]:
    """
    从环境变量加载 MCP 配置
    
    优先检查 MCP_CONFIG 环境变量（JSON 字符串），
    其次检查 MCP_CONFIG_PATH 指定的文件路径
    """
    # 1. 尝试从 MCP_CONFIG 环境变量读取 JSON
    config_json = os.getenv("MCP_CONFIG")
    if config_json:
        config_dict = json.loads(config_json)
        return load_mcp_config_from_dict(config_dict)
    
    # 2. 尝试从 MCP_CONFIG_PATH 读取文件
    env_config_path = os.getenv("MCP_CONFIG_PATH")
    config_path = os.path.expanduser(env_config_path) if env_config_path else None
    if config_path and os.path.exists(config_path):
        return load_mcp_config_from_file(config_path)
    
    # 3. 尝试从默认位置加载
    default_paths = [
        os.path.expanduser("~/.gem_code/mcp-config.json"),
        os.path.expanduser("~/.config/gem-code/mcp.json"),
        "./mcp_config.json",
    ]
    for path in default_paths:
        if os.path.exists(path):
            return load_mcp_config_from_file(path)
    
    return {}


# ============== 便捷函数 ==============

async def create_mcp_client_with_config(config: Optional[Dict[str, Mcp]] = None) -> MCPClient:
    """
    创建并初始化 MCP 客户端
    
    Args:
        config: MCP 服务器配置，如果为 None 则从环境变量加载
    """
    if config is None:
        config = load_mcp_config_from_env()
    
    client = MCPClient()
    
    # 并行连接所有服务器
    if config:
        tasks = [
            client.connect_server(name, server_config)
            for name, server_config in config.items()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    return client


# ============== 调试/测试代码 ==============

async def _test_mcp_client():
    """测试 MCP 客户端"""
    # 示例配置
    config = {
        "everything": McpLocal(
            type="local",
            command=["npx", "-y", "@modelcontextprotocol/server-everything"],
        )
    }
    
    async with MCPClient() as client:
        # 连接服务器
        for name, server_config in config.items():
            status = await client.connect_server(name, server_config)
            print(f"Server '{name}' status: {status}")
        
        # 获取所有工具
        tools = client.get_all_tools()
        print(f"\nAvailable tools ({len(tools)}):")
        for tool in tools[:5]:  # 只显示前5个
            print(f"  - {tool.full_name}: {tool.description[:50]}...")
        
        # 获取 OpenAI 格式的工具
        openai_tools = client.get_all_tools_openai_format()
        print(f"\nOpenAI format tools: {len(openai_tools)}")
        
        # 尝试调用一个工具
        if tools:
            try:
                result = await client.call_tool(
                    tools[0].full_name,
                    {}
                )
                print(f"\nTool result: {result[:200]}...")
            except Exception as e:
                print(f"\nTool call error: {e}")


if __name__ == "__main__":
    asyncio.run(_test_mcp_client())
