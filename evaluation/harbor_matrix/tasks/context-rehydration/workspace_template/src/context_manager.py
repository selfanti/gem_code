from dataclasses import dataclass


@dataclass
class Message:
    role: str
    content: str


def _to_message(unit: dict) -> Message | None:
    if unit.get("type") != "message":
        return None
    return Message(role=unit["role"], content=unit.get("content", ""))


def rehydration(
    units: list[dict],
    system_prompt: str,
    recent_messages_before_boundary: int = 3,
    recent_messages_after_boundary: int = 3,
) -> list[Message]:
    """Rebuild an in-memory history from persisted transcript units.

    The current implementation is intentionally incomplete: it
    only returns messages after the compaction boundary and drops
    both the summary and the useful context immediately before
    compaction.
    """

    boundary_index = -1
    for index, unit in enumerate(units):
        if unit.get("type") == "compact_boundary":
            boundary_index = index

    restored = [Message(role="system", content=system_prompt)]

    tail = units[boundary_index + 1 :]
    for unit in tail[-recent_messages_after_boundary:]:
        message = _to_message(unit)
        if message is not None:
            restored.append(message)

    return restored
