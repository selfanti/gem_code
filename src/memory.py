from typing import Literal, Optional
from .models import Role, ToolCall, Message, Memory_Message_Role
import json
import os
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from ulid import ULID
from uuid import UUID
from pathlib import Path


class Memory_Unit(BaseModel):
    model_config = ConfigDict(
        json_encoders={
            datetime: lambda v: v.strftime('%Y-%m-%d %H:%M:%S')
        },
        from_attributes=True
    )
    type: Memory_Message_Role
    role: Optional[Role] = None
    id: UUID = Field(default_factory=ULID().to_uuid)
    timestamp: datetime = Field(default_factory=datetime.now)
    content: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None

    def to_message(self) -> Message | None:
        assert self.type == "message"
        if self.role == "system":
            return Message(role="system", timestamp=self.timestamp, id=self.id, content=self.content)
        elif self.role == "user":
            return Message(role="user", timestamp=self.timestamp, id=self.id, content=self.content)
        elif self.role == "assistant":
            return Message(role="assistant", timestamp=self.timestamp, id=self.id, content=self.content, tool_calls=self.tool_calls)
        elif self.role == "tool":
            return Message(role="tool", timestamp=self.timestamp, id=self.id, content=self.content, tool_call_id=self.tool_call_id)
        return None


def message_to_memory_unit(message: Message, type: Memory_Message_Role) -> Memory_Unit | None:
    """将 Message 转换为 Memory_Unit"""
    if type == "compact_boundary":
        return Memory_Unit(type="compact_boundary", timestamp=message.timestamp, id=message.id)
    elif type == "summary":
        return Memory_Unit(type="summary", timestamp=message.timestamp, content=message.content, id=message.id)
    elif type == "message":
        if message.role in ("system", "user"):
            return Memory_Unit(type="message", timestamp=message.timestamp, id=message.id, role=message.role, content=message.content)
        elif message.role == "assistant":
            return Memory_Unit(type="message", timestamp=message.timestamp, id=message.id, role=message.role, content=message.content, tool_calls=message.tool_calls)
        elif message.role == "tool":
            return Memory_Unit(type="message", timestamp=message.timestamp, id=message.id, role=message.role, content=message.content, tool_call_id=message.tool_call_id)
    return None


class JsonlRandomAccess:
    def __init__(self, filepath: Path):
        self.filepath = filepath.expanduser()
        self.offsets = [0]  # 每行起始的字节偏移量
        self.parent = os.path.dirname(self.filepath)
        base_path = Path(self.filepath).with_suffix('')  # 去掉原有扩展名
        self.memory_index_path = f"{base_path}_index.json"
        # 如果文件存在，重建索引或加载索引
        if self.filepath.exists():
            if Path(self.memory_index_path).exists():
                self.load_memory_index()

    def add_line(self, memory_message: str):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        line = memory_message + "\n"
        line_bytes = line.encode('utf-8')
        with open(self.filepath, 'ab') as f:  # ✅ 二进制追加模式
            start_offset = f.tell()           # 获取当前字节位置
            f.write(line_bytes)
        self.save_memory_index()
        self.offsets.append(start_offset)
    def get_line(self, line_index: int) -> dict:
        """O(1) 时间复杂度读取任意行"""
        if line_index < 0 or line_index >= len(self.offsets):
            raise IndexError(f"行号 {line_index} 超出范围 [0, {len(self.offsets)-1}]")

        with open(self.filepath, 'r', encoding='utf-8') as f:
            f.seek(self.offsets[line_index])  # 直接跳到指定位置
            line = f.readline()
            
            return json.loads(line.strip())

    def load_messages(self) -> list[Message] | None:
        messages = []
        with open(self.filepath, 'r', encoding='utf-8') as f:
            for offset in reversed(self.offsets):
                f.seek(offset)  # 直接跳到指定位置
                line = f.readline()
                data = json.loads(line.strip())
                msg = Memory_Unit.model_validate(data)
                if msg.type == "compact_boundary":
                    break
                else:
                    messages.append(msg.to_message())

        messages.reverse()
        return messages

    def save_memory_index(self):
        with open(self.memory_index_path, "w", encoding="utf-8") as f:
            json.dump(self.offsets, f, ensure_ascii=False, indent=2)

    def load_memory_index(self):
        with open(self.memory_index_path, "r", encoding="utf-8") as f:
            self.offsets = json.load(f)


if __name__ == "__main__":
    # 使用
    accessor = JsonlRandomAccess(Path("/home/tao/gem_code/huge_file.jsonl"))
    message = Memory_Unit(type="message", content="thank you i am gem code!\n")
    for i in range(10):
        accessor.add_line(message.model_dump_json())
    accessor.save_memory_index()
    accessor.load_memory_index()
    for i in range(10):
        print(accessor.get_line(i))
