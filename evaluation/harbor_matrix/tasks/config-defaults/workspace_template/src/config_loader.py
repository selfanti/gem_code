from dataclasses import dataclass
from pathlib import Path
import os


@dataclass
class Config:
    api_key: str
    base_url: str
    model: str
    workdir: str
    skills_dir: str | None
    mcp_config_path: str | None
    memory_compaction_path: str | None


def _expand_path(path: str | None) -> str | None:
    if not path:
        return None
    return str(Path(path).expanduser())


def load_config() -> Config:
    """Load runtime configuration.

    This intentionally starts from a buggy baseline: optional
    integrations are left unset even though the application is
    expected to provide stable defaults for local development.
    """

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    model = os.getenv("OPENAI_MODEL", "MiniMax-M2.5")
    workdir = str(Path(os.getenv("WORKDIR") or os.getcwd()).expanduser())

    skills_dir = _expand_path(os.getenv("SKILLS_DIR"))
    mcp_config_path = _expand_path(os.getenv("MCP_CONFIG_PATH"))
    memory_compaction_path = _expand_path(os.getenv("MEMORY_COMPACTION_PATH"))

    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")
    if not base_url:
        raise ValueError("OPENAI_BASE_URL environment variable is not set")

    return Config(
        api_key=api_key,
        base_url=base_url,
        model=model,
        workdir=workdir,
        skills_dir=skills_dir,
        mcp_config_path=mcp_config_path,
        memory_compaction_path=memory_compaction_path,
    )
