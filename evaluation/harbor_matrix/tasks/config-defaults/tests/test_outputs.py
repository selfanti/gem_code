import os

from config_loader import load_config


def test_load_config_uses_repo_friendly_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("SKILLS_DIR", "")
    monkeypatch.setenv("MCP_CONFIG_PATH", "")
    monkeypatch.setenv("MEMORY_COMPACTION_PATH", "")

    config = load_config()

    assert config.skills_dir == os.path.join(str(tmp_path), ".agents")
    assert config.mcp_config_path is None
    assert config.memory_compaction_path == "~/.gem_code/projects"
