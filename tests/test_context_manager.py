from pathlib import Path

from src.context_manager import Context_Manager
from src.memory import JsonlRandomAccess, Memory_Unit, message_to_memory_unit
from src.models import Message


def test_rehydration_restores_summary_and_recent_context(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    access = JsonlRandomAccess(transcript)

    before_messages = [
        Message(role="system", content="system prompt"),
        Message(role="user", content="open README"),
        Message(role="assistant", content="reading files"),
        Message(role="tool", content="README contents", tool_call_id="tool-1"),
    ]
    for message in before_messages:
        access.add_line(message_to_memory_unit(message, "message").model_dump_json())  # type: ignore[union-attr]

    access.add_line(Memory_Unit(type="compact_boundary").model_dump_json())
    access.add_line(Memory_Unit(type="summary", content="summary text").model_dump_json())
    access.add_line(
        message_to_memory_unit(
            Message(role="user", content="continue after compaction"),
            "message",
        ).model_dump_json()  # type: ignore[union-attr]
    )

    manager = Context_Manager()
    restored = manager.rehydration(
        access,
        system_prompt="system prompt",
        recent_messages_before_boundary=3,
        recent_messages_after_boundary=3,
    )

    assert restored[0].role == "system"
    assert any(
        message.role == "assistant" and message.content and "summary text" in message.content
        for message in restored
    )
    assert any(
        message.role == "tool" and message.content == "README contents"
        for message in restored
    )
    assert any(
        message.role == "user" and message.content == "continue after compaction"
        for message in restored
    )
