from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from ulid import ULID

from .models import Memory_Message_Role, Message, Role, ToolCall


class Memory_Unit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Memory_Message_Role
    role: Optional[Role] = None
    id: UUID = Field(default_factory=ULID().to_uuid)
    timestamp: datetime = Field(default_factory=datetime.now)
    content: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        # Use ISO 8601 so persisted transcripts stay standards-based and avoid
        # Pydantic V2's deprecated `json_encoders` path.
        return value.isoformat()

    def to_message(self) -> Message | None:
        if self.type != "message":
            return None
        if self.role == "system":
            return Message(role="system", timestamp=self.timestamp, id=self.id, content=self.content)
        if self.role == "user":
            return Message(role="user", timestamp=self.timestamp, id=self.id, content=self.content)
        if self.role == "assistant":
            return Message(
                role="assistant",
                timestamp=self.timestamp,
                id=self.id,
                content=self.content,
                tool_calls=self.tool_calls,
            )
        if self.role == "tool":
            return Message(
                role="tool",
                timestamp=self.timestamp,
                id=self.id,
                content=self.content,
                tool_call_id=self.tool_call_id,
            )
        return None


def message_to_memory_unit(message: Message, unit_type: Memory_Message_Role) -> Memory_Unit | None:
    if unit_type == "compact_boundary":
        return Memory_Unit(type="compact_boundary", timestamp=message.timestamp, id=message.id)
    if unit_type == "summary":
        return Memory_Unit(type="summary", timestamp=message.timestamp, content=message.content, id=message.id)
    if unit_type == "message":
        if message.role in ("system", "user"):
            return Memory_Unit(
                type="message",
                timestamp=message.timestamp,
                id=message.id,
                role=message.role,
                content=message.content,
            )
        if message.role == "assistant":
            return Memory_Unit(
                type="message",
                timestamp=message.timestamp,
                id=message.id,
                role=message.role,
                content=message.content,
                tool_calls=message.tool_calls,
            )
        if message.role == "tool":
            return Memory_Unit(
                type="message",
                timestamp=message.timestamp,
                id=message.id,
                role=message.role,
                content=message.content,
                tool_call_id=message.tool_call_id,
            )
    return None


class JsonlRandomAccess:
    def __init__(self, filepath: Path):
        self.filepath = filepath.expanduser()
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.offsets: list[int] = []
        base_path = self.filepath.with_suffix("")
        self.memory_index_path = Path(f"{base_path}_index.json")

        if self.filepath.exists():
            if self.memory_index_path.exists():
                self.load_memory_index()
            else:
                self.rebuild_memory_index()

    def rebuild_memory_index(self) -> None:
        """Re-scan the transcript and rebuild offsets from raw bytes.

        Keeping this fallback avoids corrupting resume behavior when the index
        file is missing or stale. The old implementation silently initialized the
        index with `[0]`, which duplicated the first entry on every load.
        """

        self.offsets = []
        if not self.filepath.exists():
            return

        offset = 0
        with open(self.filepath, "rb") as handle:
            for line in handle:
                self.offsets.append(offset)
                offset += len(line)
        self.save_memory_index()

    def add_line(self, memory_message: str) -> None:
        line = memory_message + "\n"
        line_bytes = line.encode("utf-8")
        with open(self.filepath, "ab") as handle:
            start_offset = handle.tell()
            handle.write(line_bytes)
        self.offsets.append(start_offset)
        self.save_memory_index()

    def get_line(self, line_index: int) -> dict:
        if line_index < 0 or line_index >= len(self.offsets):
            raise IndexError(f"行号 {line_index} 超出范围 [0, {len(self.offsets) - 1}]")

        with open(self.filepath, "r", encoding="utf-8") as handle:
            handle.seek(self.offsets[line_index])
            line = handle.readline()
            return json.loads(line.strip())

    def load_messages(self) -> list[Message]:
        if not self.filepath.exists():
            return []

        if not self.offsets:
            self.rebuild_memory_index()

        messages: list[Message] = []
        with open(self.filepath, "r", encoding="utf-8") as handle:
            for offset in reversed(self.offsets):
                handle.seek(offset)
                line = handle.readline()
                if not line.strip():
                    continue
                data = json.loads(line.strip())
                memory_message = Memory_Unit.model_validate(data)
                if memory_message.type == "compact_boundary":
                    break
                message = memory_message.to_message()
                if message is not None:
                    messages.append(message)

        messages.reverse()
        return messages

    def load_memory_units(self) -> list[Memory_Unit]:
        """Load the full transcript as typed memory units.

        Rehydration needs access to `summary` and `compact_boundary` entries, not
        just ordinary chat messages. Keeping this helper in the persistence layer
        avoids re-parsing JSONL in multiple places and guarantees that resume and
        compaction logic see the same transcript ordering.
        """

        if not self.filepath.exists():
            return []

        if not self.offsets:
            self.rebuild_memory_index()

        units: list[Memory_Unit] = []
        with open(self.filepath, "r", encoding="utf-8") as handle:
            for offset in self.offsets:
                handle.seek(offset)
                line = handle.readline()
                if not line.strip():
                    continue
                units.append(Memory_Unit.model_validate_json(line))

        return units

    def save_memory_index(self) -> None:
        with open(self.memory_index_path, "w", encoding="utf-8") as handle:
            json.dump(self.offsets, handle, ensure_ascii=False, indent=2)

    def load_memory_index(self) -> None:
        with open(self.memory_index_path, "r", encoding="utf-8") as handle:
            self.offsets = json.load(handle)
