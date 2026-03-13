import os

from src.config import load_config, resolve_api_mode


def test_load_config_uses_optional_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    # `load_dotenv()` reads the repository `.env`, so tests pin these optional
    # values explicitly to exercise the code-path that falls back to defaults.
    monkeypatch.setenv("SKILLS_DIR", "")
    monkeypatch.setenv("MCP_CONFIG_PATH", "")
    monkeypatch.setenv("MEMORY_COMPACTION_PATH", "")
    monkeypatch.delenv("OPENAI_API_MODE", raising=False)

    config = load_config()

    assert config.skills_dir == os.path.join(str(tmp_path), ".agents")
    assert config.mcp_config_path is None
    assert config.memory_compaction_path == "~/.gem_code/projects"
    assert resolve_api_mode(config) == "chat_completions"


def test_resolve_api_mode_prefers_responses_for_openai(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_MODE", "auto")

    config = load_config()
    assert resolve_api_mode(config) == "responses"
