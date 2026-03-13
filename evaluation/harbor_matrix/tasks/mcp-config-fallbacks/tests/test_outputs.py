import json
from pathlib import Path

from mcp_config import load_mcp_config_from_env


def test_inline_config_has_highest_priority(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCP_CONFIG", '{"mcpServers":{"inline":{"type":"remote"}}}')
    assert load_mcp_config_from_env() == {"mcpServers": {"inline": {"type": "remote"}}}


def test_fallback_files_are_checked_in_documented_order(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    monkeypatch.delenv("MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))

    first = home_dir / ".gem-code" / "mcp_config.json"
    second = home_dir / ".config" / "gem-code" / "mcp.json"
    third = tmp_path / "mcp_config.json"

    second.parent.mkdir(parents=True, exist_ok=True)
    third.write_text(json.dumps({"mcpServers": {"cwd": {"type": "local"}}}), encoding="utf-8")
    second.write_text(json.dumps({"mcpServers": {"config": {"type": "local"}}}), encoding="utf-8")

    assert load_mcp_config_from_env() == {"mcpServers": {"config": {"type": "local"}}}

    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text(json.dumps({"mcpServers": {"home": {"type": "local"}}}), encoding="utf-8")
    assert load_mcp_config_from_env() == {"mcpServers": {"home": {"type": "local"}}}
