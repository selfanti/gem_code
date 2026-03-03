#!/usr/bin/env python3
"""
MCP Client 测试脚本

测试 MCP 客户端的连接、工具发现和调用功能。
需要先安装 MCP 服务器才能运行完整测试。

运行方式:
  uv run python test_mcp_client.py
"""

import asyncio
import json
import os

from src.mcp_client import (
    MCPClient, 
    MCPTool, 
    load_mcp_config_from_dict,
    create_mcp_client_with_config,
    McpLocal
)


async def test_basic_functionality():
    """测试基本功能（无需外部服务器）"""
    print("=" * 50)
    print("测试 1: MCPClient 初始化和状态管理")
    print("=" * 50)
    
    client = MCPClient()
    
    # 测试空客户端
    tools = client.get_all_tools()
    assert len(tools) == 0, "New client should have no tools"
    print("✓ 空客户端工具列表正确")
    
    status = client.get_all_status()
    assert len(status) == 0, "New client should have no servers"
    print("✓ 空客户端状态正确")
    
    # 测试上下文管理器
    async with MCPClient() as client2:
        print("✓ 上下文管理器工作正常")
    
    print("\n测试 1 通过!\n")


async def test_config_loading():
    """测试配置加载"""
    print("=" * 50)
    print("测试 2: 配置加载")
    print("=" * 50)
    
    # 测试从字典加载
    config_dict = {
        "mcpServers": {
            "test-server": {
                "type": "local",
                "command": ["echo", "test"],
                "enabled": True
            },
            "disabled-server": {
                "type": "local",
                "command": ["echo", "disabled"],
                "enabled": False
            }
        }
    }
    
    config = load_mcp_config_from_dict(config_dict)
    assert "test-server" in config, "Should load enabled server"
    assert "disabled-server" not in config, "Should skip disabled server"
    print("✓ 配置加载正确（已启用/禁用筛选）")
    
    # 验证配置类型
    assert isinstance(config["test-server"], McpLocal)
    assert config["test-server"].type == "local"
    print("✓ 配置类型正确")
    
    print("\n测试 2 通过!\n")


async def test_tool_format_conversion():
    """测试工具格式转换"""
    print("=" * 50)
    print("测试 3: 工具格式转换")
    print("=" * 50)
    
    tool = MCPTool(
        name="test_tool",
        description="A test tool",
        parameters={
            "type": "object",
            "properties": {
                "arg1": {"type": "string"}
            }
        },
        server_name="test-server"
    )
    
    # 测试 full_name
    assert tool.full_name == "mcp__test-server__test_tool"
    print("✓ Tool full_name 正确")
    
    # 测试 OpenAI 格式转换
    openai_format = tool.to_openai_function()
    assert openai_format["type"] == "function"
    assert openai_format["function"]["name"] == "mcp__test-server__test_tool"
    assert "test-server" in openai_format["function"]["description"]
    print("✓ OpenAI 格式转换正确")
    
    print("\n测试 3 通过!\n")


async def test_with_mcp_server():
    """测试实际的 MCP 服务器连接（需要安装 server-everything）"""
    print("=" * 50)
    print("测试 4: MCP 服务器连接（可选）")
    print("=" * 50)
    
    # 检查是否有 MCP 配置
    config_path = "mcp_config.json"
    if not os.path.exists(config_path) or os.path.getsize(config_path) == 0:
        print("⚠️  未找到 mcp_config.json 或文件为空，跳过实际服务器测试")
        print("   复制 mcp_config.example.json 并启用测试服务器以运行完整测试")
        return
    
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"⚠️  配置文件格式错误: {e}")
        return
    
    servers = config.get("mcpServers", {})
    enabled_servers = {
        name: cfg for name, cfg in servers.items() 
        if cfg.get("enabled", True)
    }
    
    if not enabled_servers:
        print("⚠️  没有启用的 MCP 服务器，跳过实际服务器测试")
        return
    
    # 尝试连接启用的服务器
    print(f"发现 {len(enabled_servers)} 个启用的 MCP 服务器")
    
    async with MCPClient() as client:
        for name, cfg in enabled_servers.items():
            print(f"\n  连接服务器: {name}")
            try:
                from src.mcp_client import load_mcp_config_from_dict
                mcp_config = load_mcp_config_from_dict({"mcpServers": {name: cfg}})
                if name in mcp_config:
                    status = await client.connect_server(name, mcp_config[name])
                    if hasattr(status, 'status') and status.status == "connected":
                        tools = client.get_server_tools(name)
                        print(f"  ✓ 连接成功，发现 {len(tools)} 个工具")
                        for tool in tools[:3]:  # 只显示前3个
                            print(f"    - {tool.name}: {tool.description[:50]}...")
                    else:
                        error = getattr(status, 'error', 'Unknown error')
                        print(f"  ✗ 连接失败: {error}")
            except Exception as e:
                print(f"  ✗ 连接异常: {e}")
    
    print("\n测试 4 完成!\n")


async def main():
    """运行所有测试"""
    print("\n" + "=" * 50)
    print("MCP Client 测试套件")
    print("=" * 50 + "\n")
    
    try:
        await test_basic_functionality()
        await test_config_loading()
        await test_tool_format_conversion()
        await test_with_mcp_server()
        
        print("=" * 50)
        print("所有测试完成!")
        print("=" * 50)
        
    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
    except Exception as e:
        print(f"\n✗ 测试异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
