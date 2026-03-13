from context_manager import rehydration


def test_rehydration_restores_summary_and_recent_context() -> None:
    units = [
        {"type": "message", "role": "system", "content": "old system"},
        {"type": "message", "role": "user", "content": "open README"},
        {"type": "message", "role": "assistant", "content": "reading files"},
        {"type": "message", "role": "tool", "content": "README contents"},
        {"type": "compact_boundary"},
        {"type": "summary", "content": "summary text"},
        {"type": "message", "role": "user", "content": "continue after compaction"},
    ]

    restored = rehydration(
        units,
        system_prompt="system prompt",
        recent_messages_before_boundary=3,
        recent_messages_after_boundary=3,
    )

    assert restored[0].role == "system"
    assert restored[0].content == "system prompt"
    assert any(message.role == "assistant" and "summary text" in message.content for message in restored)
    assert any(message.role == "tool" and message.content == "README contents" for message in restored)
    assert any(message.role == "user" and message.content == "continue after compaction" for message in restored)
