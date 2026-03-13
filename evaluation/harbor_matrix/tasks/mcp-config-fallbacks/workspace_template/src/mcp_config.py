import json
import os
from pathlib import Path


def load_mcp_config_from_env(explicit_path: str | None = None):
    """Load MCP config from the environment.

    The current implementation only looks at one path and ignores
    both inline JSON and documented fallback locations.
    """

    candidate = explicit_path or os.getenv("MCP_CONFIG_PATH")
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
