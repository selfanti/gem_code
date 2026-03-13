from pathlib import Path

from session_manager import SessionManager


def test_rebind_transcript_updates_session_references(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    manager._rebind_transcript("next")

    assert manager.session.memory_access.filepath == tmp_path / "next.jsonl"
    assert manager.session.transcript_path == tmp_path / "next.jsonl"
