import asyncio

import pytest

from src.tool import run_read_file, run_write_file


def test_read_file_rejects_path_escape(tmp_path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError):
        asyncio.run(run_read_file("../secret.txt", str(workdir)))


def test_read_file_reads_requested_line_range(tmp_path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    target = workdir / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")

    result = asyncio.run(run_read_file("notes.txt", str(workdir), start_line=2, end_line=3))

    assert result == "2: beta\n3: gamma"


def test_read_file_reads_from_start_when_only_end_line_is_set(tmp_path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    target = workdir / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = asyncio.run(run_read_file("notes.txt", str(workdir), end_line=2))

    assert result == "1: alpha\n2: beta"


def test_read_file_rejects_invalid_line_range(tmp_path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    target = workdir / "notes.txt"
    target.write_text("alpha\n", encoding="utf-8")

    with pytest.raises(ValueError):
        asyncio.run(run_read_file("notes.txt", str(workdir), start_line=3, end_line=2))


def test_write_file_stays_inside_workspace(tmp_path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()

    result = asyncio.run(run_write_file("nested/file.txt", "payload", str(workdir)))

    assert "Successfully wrote" in result
    assert (workdir / "nested" / "file.txt").read_text(encoding="utf-8") == "payload"
