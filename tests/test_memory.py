from pathlib import Path

from src.memory import JsonlRandomAccess, Memory_Unit


def test_jsonl_random_access_does_not_duplicate_first_entry(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    access = JsonlRandomAccess(transcript)

    access.add_line(
        Memory_Unit(type="message", role="user", content="hello").model_dump_json()
    )

    messages = access.load_messages()
    assert len(messages) == 1
    assert messages[0].content == "hello"
