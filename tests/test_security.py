import subprocess

import pytest

from src.security import (
    SecuritySettings,
    build_sandbox_runner_command,
    ensure_url_permitted,
    landlock_runtime_supported,
)


def test_ensure_url_permitted_rejects_blocked_network() -> None:
    with pytest.raises(PermissionError):
        ensure_url_permitted(
            "https://example.com",
            SecuritySettings(
                enabled=True,
                best_effort=False,
                allow_network=False,
                connect_ports=(),
            ),
        )


def test_landlock_runner_allows_workspace_writes(tmp_path) -> None:
    if not landlock_runtime_supported():
        pytest.skip("Landlock is not available in this environment")

    workdir = tmp_path / "workspace"
    workdir.mkdir()

    argv, env = build_sandbox_runner_command(
        "printf allowed > note.txt && cat note.txt",
        str(workdir),
        SecuritySettings(enabled=True, best_effort=False),
    )
    result = subprocess.run(
        argv,
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout == "allowed"
    assert (workdir / "note.txt").read_text(encoding="utf-8") == "allowed"


def test_landlock_runner_blocks_workspace_escape(tmp_path) -> None:
    if not landlock_runtime_supported():
        pytest.skip("Landlock is not available in this environment")

    workdir = tmp_path / "workspace"
    workdir.mkdir()
    outside = tmp_path / "escape.txt"

    argv, env = build_sandbox_runner_command(
        "printf blocked > ../escape.txt",
        str(workdir),
        SecuritySettings(enabled=True, best_effort=False),
    )
    result = subprocess.run(
        argv,
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert not outside.exists()
    assert result.returncode != 0
    assert any(
        token in result.stderr.lower()
        for token in ("permission", "denied", "not permitted")
    )
