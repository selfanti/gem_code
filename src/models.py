"""
基础数据模型模块
存放核心类型定义，避免循环导入
"""
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
