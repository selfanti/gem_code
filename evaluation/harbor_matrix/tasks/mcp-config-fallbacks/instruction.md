Fix `load_mcp_config_from_env()` in `/workspace/evaluation_fixture/src/mcp_config.py`.

Requirements:
- `MCP_CONFIG` should take precedence and be parsed as inline JSON.
- Otherwise load `MCP_CONFIG_PATH` if provided.
- Otherwise search these fallback files in order:
  `~/.gem-code/mcp_config.json`
  `~/.config/gem-code/mcp.json`
  `./mcp_config.json`
- Return `None` when nothing exists.
