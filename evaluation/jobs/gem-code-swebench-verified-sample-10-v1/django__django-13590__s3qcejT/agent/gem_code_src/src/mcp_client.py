"""
MCP (Model Context Protocol) Client Implementation

提供对 MCP 服务器的连接、工具发现和调用功能。
支持 stdio、Streamable HTTP 和 legacy SSE 三种传输方式。
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from .mcp import Mcp, McpLocal, McpRemote, Status, StatusConnected, StatusFailed


@dataclass
class MCPTool:
    name: str
    description: str
    parameters: Dict[str, Any]
    server_name: str

    def to_openai_function(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": f"mcp__{self.server_name}__{self.name}",
                "description": f"[{self.server_name}] {self.description}",
                "parameters": self.parameters,
            },
        }

    @property
    def full_name(self) -> str:
        return f"mcp__{self.server_name}__{self.name}"


@dataclass
class ServerConnection:
    name: str
    config: Mcp
    status: Status = field(
        default_factory=lambda: StatusFailed(status="failed", error="Not initialized")
    )
    session: Optional[ClientSession] = None
    tools: List[MCPTool] = field(default_factory=list)
    _exit_stack: Optional[AsyncExitStack] = None


class MCPClient:
    def __init__(self):
        self._servers: Dict[str, ServerConnection] = {}
        self._lock = asyncio.Lock()

    async def connect_server(self, name: str, config: Mcp) -> Status:
        async with self._lock:
            if name in self._servers:
                await self._disconnect_server(name)

            conn = ServerConnection(name=name, config=config)
            self._servers[name] = conn

            try:
                if isinstance(config, McpLocal):
                    await self._connect_stdio(conn, config)
                elif isinstance(config, McpRemote):
                    await self._connect_remote(conn, config)
                else:
                    raise ValueError(f"Unknown config type: {type(config)}")

                await self._refresh_tools(conn)
                conn.status = StatusConnected(status="connected")
            except Exception as exc:
                conn.status = StatusFailed(
                    status="failed",
                    error=f"Failed to connect to server '{name}': {str(exc)}",
                )

            return conn.status

    async def _connect_stdio(self, conn: ServerConnection, config: McpLocal) -> None:
        server_params = StdioServerParameters(
            command=config.command[0],
            args=config.command[1:] if len(config.command) > 1 else [],
            env={**os.environ, **(config.environment or {})},
        )

        exit_stack = AsyncExitStack()
        try:
            stdio_read, stdio_write = await exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            session = await exit_stack.enter_async_context(ClientSession(stdio_read, stdio_write))
            await session.initialize()
            conn.session = session
            conn._exit_stack = exit_stack
        except Exception:
            try:
                await exit_stack.aclose()
            except Exception:
                pass
            raise

    async def _connect_remote(self, conn: ServerConnection, config: McpRemote) -> None:
        """Connect using the transport recommended by the MCP specification.

        The 2025 MCP transport spec recommends Streamable HTTP for remote
        clients. We keep SSE as an explicit and inferred fallback so existing
        `/sse` endpoints continue to work without requiring a config migration.
        """

        transport = config.transport
        if transport is None:
            transport = "sse" if config.url.rstrip("/").endswith("/sse") else "streamable_http"

        if transport == "streamable_http":
            await self._connect_streamable_http(conn, config)
            return

        await self._connect_sse(conn, config)

    async def _connect_streamable_http(self, conn: ServerConnection, config: McpRemote) -> None:
        exit_stack = AsyncExitStack()
        try:
            http_client = await exit_stack.enter_async_context(
                httpx.AsyncClient(headers=config.headers or {})
            )
            read_stream, write_stream, _ = await exit_stack.enter_async_context(
                streamable_http_client(config.url, http_client=http_client)
            )
            session = await exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            conn.session = session
            conn._exit_stack = exit_stack
        except Exception:
            try:
                await exit_stack.aclose()
            except Exception:
                pass
            raise

    async def _connect_sse(self, conn: ServerConnection, config: McpRemote) -> None:
        headers = config.headers or {}
        exit_stack = AsyncExitStack()
        try:
            sse_read, sse_write = await exit_stack.enter_async_context(
                sse_client(config.url, headers=headers)
            )
            session = await exit_stack.enter_async_context(ClientSession(sse_read, sse_write))
            await session.initialize()
            conn.session = session
            conn._exit_stack = exit_stack
        except Exception:
            try:
                await exit_stack.aclose()
            except Exception:
                pass
            raise

    async def _refresh_tools(self, conn: ServerConnection) -> None:
        if not conn.session:
            raise RuntimeError(f"Server '{conn.name}' not connected")

        tools_result = await conn.session.list_tools()
        conn.tools = [
            MCPTool(
                name=tool.name,
                description=tool.description or "",
                parameters=tool.inputSchema,
                server_name=conn.name,
            )
            for tool in tools_result.tools
        ]

    async def _disconnect_server(self, name: str) -> None:
        conn = self._servers.get(name)
        if not conn:
            return

        if conn._exit_stack:
            try:
                await asyncio.wait_for(conn._exit_stack.aclose(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass

        conn.session = None
        conn.tools = []
        conn._exit_stack = None
        conn.status = StatusFailed(status="failed", error="Disconnected")

    async def disconnect_server(self, name: str) -> None:
        async with self._lock:
            await self._disconnect_server(name)

    async def disconnect_all(self) -> None:
        async with self._lock:
            for name in list(self._servers.keys()):
                await self._disconnect_server(name)
            self._servers.clear()

    async def refresh_all_tools(self) -> None:
        async with self._lock:
            for conn in self._servers.values():
                if conn.session and isinstance(conn.status, StatusConnected):
                    try:
                        await self._refresh_tools(conn)
                    except Exception as exc:
                        conn.status = StatusFailed(
                            status="failed",
                            error=f"Failed to refresh tools: {str(exc)}",
                        )

    def get_all_tools(self) -> List[MCPTool]:
        tools: List[MCPTool] = []
        for conn in self._servers.values():
            if isinstance(conn.status, StatusConnected):
                tools.extend(conn.tools)
        return tools

    def get_all_tools_openai_format(self) -> List[Dict[str, Any]]:
        return [tool.to_openai_function() for tool in self.get_all_tools()]

    def get_server_tools(self, server_name: str) -> List[MCPTool]:
        conn = self._servers.get(server_name)
        if conn and isinstance(conn.status, StatusConnected):
            return conn.tools
        return []

    def get_server_status(self, name: str) -> Optional[Status]:
        conn = self._servers.get(name)
        return conn.status if conn else None

    def get_all_status(self) -> Dict[str, Status]:
        return {name: conn.status for name, conn in self._servers.items()}

    async def call_tool(self, full_tool_name: str, arguments: Dict[str, Any]) -> str:
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

            result = await conn.session.call_tool(tool_name, arguments)
            return self._parse_tool_result(result)

    def _parse_tool_result(self, result: CallToolResult) -> str:
        if result.isError:
            messages = [
                content.text
                for content in result.content
                if getattr(content, "type", None) == "text"
            ]
            raise RuntimeError(f"Tool error: {' '.join(messages)}")

        texts: List[str] = []
        for content in result.content:
            if content.type == "text":
                texts.append(content.text)
            elif content.type == "image":
                texts.append(f"[Image data: {content.mimeType}]")
            elif content.type == "resource":
                resource = content.resource
                if hasattr(resource, "text") and resource.text:  # type: ignore[attr-defined]
                    texts.append(resource.text)  # type: ignore[attr-defined]
                elif hasattr(resource, "blob") and resource.blob:  # type: ignore[attr-defined]
                    texts.append(f"[Binary resource: {resource.mimeType}]")

        return "\n".join(texts) if texts else "(empty result)"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect_all()


def load_mcp_config_from_dict(config_dict: Dict[str, Any]) -> Dict[str, Mcp]:
    servers_config = config_dict.get("mcpServers", {})
    result: Dict[str, Mcp] = {}

    for name, server_config in servers_config.items():
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
                timeout=server_config.get("timeout", 30000),
            )
        elif server_type == "remote":
            result[name] = McpRemote(
                type="remote",
                url=server_config["url"],
                transport=server_config.get("transport"),
                headers=server_config.get("headers"),
                oauth=server_config.get("oauth"),
                enabled=enabled,
                timeout=server_config.get("timeout", 30000),
            )

    return result


def load_mcp_config_from_file(config_path: str) -> Dict[str, Mcp]:
    with open(config_path, "r", encoding="utf-8") as handle:
        config_dict = json.load(handle)
    return load_mcp_config_from_dict(config_dict)


def load_mcp_config_from_env(preferred_path: Optional[str] = None) -> Dict[str, Mcp]:
    """Load MCP config from env, an explicit path, or documented defaults.

    Runtime environments used for benchmarks and CI are often deliberately
    stripped down and may inject placeholder MCP variables or mount incomplete
    config files. Treating those cases as fatal turns an optional integration
    into startup noise. We therefore parse inline/file config defensively and
    fall back to "no MCP servers" when the discovered payload is blank or
    malformed.
    """

    config_json = os.getenv("MCP_CONFIG")
    if config_json and config_json.strip():
        try:
            return load_mcp_config_from_dict(json.loads(config_json))
        except json.JSONDecodeError:
            return {}

    candidate_paths = []
    if preferred_path:
        candidate_paths.append(os.path.expanduser(preferred_path))

    env_config_path = os.getenv("MCP_CONFIG_PATH")
    if env_config_path:
        candidate_paths.append(os.path.expanduser(env_config_path))

    candidate_paths.extend(
        [
            os.path.expanduser("~/.gem_code/mcp-config.json"),
            os.path.expanduser("~/.config/gem-code/mcp.json"),
            "./mcp_config.json",
        ]
    )

    for path in candidate_paths:
        if path and os.path.exists(path):
            try:
                return load_mcp_config_from_file(path)
            except json.JSONDecodeError:
                continue

    return {}


async def create_mcp_client_with_config(config: Optional[Dict[str, Mcp]] = None) -> MCPClient:
    if config is None:
        config = load_mcp_config_from_env()

    client = MCPClient()
    if config:
        await asyncio.gather(
            *(client.connect_server(name, server_config) for name, server_config in config.items()),
            return_exceptions=True,
        )
    return client
