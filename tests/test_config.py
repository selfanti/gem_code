import os

from src.config import (
    PREDICT_BEFORE_CALL_CLAUSE,
    get_system_prompt,
    load_config,
    resolve_api_mode,
)


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
    # The repository `.env` may set security flags for normal CLI use; clear
    # them so this test really exercises the in-source defaults. We also
    # change cwd so `load_dotenv()` does not re-add the values from the
    # checked-in `.env`.
    monkeypatch.delenv("GEM_CODE_SECURITY_ALLOW_NETWORK", raising=False)
    monkeypatch.delenv("GEM_CODE_SECURITY_ALLOW_CONNECT", raising=False)
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.skills_dir == os.path.join(str(tmp_path), ".agents")
    assert config.mcp_config_path is None
    assert config.memory_compaction_path == "~/.gem_code/projects"
    assert resolve_api_mode(config) == "chat_completions"
    assert config.security.enabled is True
    # Source default for `GEM_CODE_SECURITY_ALLOW_NETWORK` is True (README
    # documents the same). Tests that need a sandboxed default should
    # explicitly set the env to "false".
    assert config.security.allow_network is True
    assert config.security.connect_ports == ()
    assert config.security.best_effort is True


def test_resolve_api_mode_prefers_responses_for_openai(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_MODE", "auto")

    config = load_config()
    assert resolve_api_mode(config) == "responses"


def test_load_config_reads_security_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("GEM_CODE_SECURITY_ENABLED", "true")
    monkeypatch.setenv("GEM_CODE_SECURITY_BEST_EFFORT", "false")
    monkeypatch.setenv("GEM_CODE_SECURITY_ALLOW_NETWORK", "false")
    monkeypatch.setenv("GEM_CODE_SECURITY_ALLOW_CONNECT", "443,8443")

    config = load_config()

    assert config.security.enabled is True
    assert config.security.best_effort is False
    assert config.security.allow_network is False
    assert config.security.connect_ports == (443, 8443)


def test_load_config_default_permission_settings(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.delenv("GEM_CODE_PERMISSION_GATE_ENABLED", raising=False)
    monkeypatch.delenv("GEM_CODE_PREDICT_BEFORE_CALL", raising=False)
    monkeypatch.delenv("GEM_CODE_SELF_DISCOVERY", raising=False)
    monkeypatch.delenv("GEM_CODE_PERMISSION_MODE", raising=False)

    config = load_config()

    assert config.permission_gate_enabled is True
    assert config.predict_before_call_enabled is False
    assert config.self_discovery_enabled is False
    assert config.permission_mode == "strict"


def test_load_config_reads_permission_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("GEM_CODE_PERMISSION_GATE_ENABLED", "true")
    monkeypatch.setenv("GEM_CODE_PREDICT_BEFORE_CALL", "true")
    monkeypatch.setenv("GEM_CODE_SELF_DISCOVERY", "true")
    monkeypatch.setenv("GEM_CODE_PERMISSION_MODE", "auto_deny")

    config = load_config()

    assert config.permission_gate_enabled is True
    assert config.predict_before_call_enabled is True
    assert config.self_discovery_enabled is True
    assert config.permission_mode == "auto_deny"


def test_get_system_prompt_omits_predict_clause_by_default(tmp_path) -> None:
    body = get_system_prompt(str(tmp_path))
    # Comparing against the marker string keeps the test stable when the body
    # text below the clause is updated for unrelated reasons.
    assert "Predict before call" not in body
    assert PREDICT_BEFORE_CALL_CLAUSE.strip() not in body


def test_get_system_prompt_appends_predict_clause_when_enabled(tmp_path) -> None:
    legacy = get_system_prompt(str(tmp_path))
    augmented = get_system_prompt(str(tmp_path), predict_before_call_enabled=True)

    assert augmented != legacy
    # The augmentation MUST only append; no other part of the prompt may shift.
    assert augmented.startswith(legacy)
    assert PREDICT_BEFORE_CALL_CLAUSE.strip() in augmented
