"""
基础数据模型模块
存放核心类型定义，避免循环导入
"""
from dataclasses import dataclass
from typing import Literal, Optional, List
from datetime import datetime
from uuid import UUID
from ulid import ULID
from pydantic import Field, BaseModel

# Role 类型定义
Role = Literal["system", "user", "assistant", "tool"]


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    function: FunctionCall
    type: str = "function"


class Message(BaseModel):
    """聊天消息模型"""
    role: Role
    content: Optional[str]
    id: UUID = Field(default_factory=ULID().to_uuid)
    timestamp: datetime = Field(default_factory=datetime.now)
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None


# Memory 相关类型
Memory_Message_Role = Literal["message", "compact_boundary", "summary"]


ContextUsageSource = Literal["estimated", "server"]


@dataclass
class ContextUsageSnapshot:
    """Shared context-usage view for the session layer and the TUI.

    `used_tokens` is the number currently shown to the user. During streaming it
    is often an estimate because providers typically report `usage` only at the
    end of a response. `server_tokens` stores the authoritative total when the
    provider supplies it, while `estimated_input_tokens` and
    `estimated_output_tokens` explain how the local estimate was produced.
    """

    used_tokens: int
    max_tokens: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    tool_schema_tokens: int
    source: ContextUsageSource
    server_tokens: Optional[int] = None

    @property
    def percentage(self) -> float:
        if self.max_tokens <= 0:
            return 0.0
        return (self.used_tokens / self.max_tokens) * 100
