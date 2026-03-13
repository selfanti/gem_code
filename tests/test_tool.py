import pytest

from src.tool import run_read_file, run_write_file


@pytest.mark.asyncio
async def test_read_file_rejects_path_escape(tmp_path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError):
        await run_read_file("../secret.txt", str(workdir))


@pytest.mark.asyncio
async def test_write_file_stays_inside_workspace(tmp_path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()

    result = await run_write_file("nested/file.txt", "payload", str(workdir))

    assert "Successfully wrote" in result
    assert (workdir / "nested" / "file.txt").read_text(encoding="utf-8") == "payload"
