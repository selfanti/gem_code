
from typing import Literal,Optional
from config import Role,ToolCall
import json
import os
from pydantic import BaseModel,Field,ConfigDict
from datetime import datetime
from ulid import ULID
from uuid import UUID
from pathlib import Path
Memory_Message_Role = Literal["message","compact_boundary","summary"]

class Memory_Unit(BaseModel):
    model_config = ConfigDict(
        json_encoders={
            datetime: lambda v: v.strftime('%Y-%m-%d %H:%M:%S')
        },
        from_attributes=True
    )
    type:Memory_Message_Role
    id:UUID=Field(default_factory=ULID().to_uuid)
    timestamp:datetime=Field(default_factory=datetime.now)
    content:Optional[str]=None
    tool_call_id: Optional[str]=None
    tool_calls:Optional[list[ToolCall]]=[]






class JsonlRandomAccess:
    def __init__(self, filepath: Path):
        self.filepath = filepath.expanduser()
        self.offsets = [0]  # 每行起始的字节偏移量
        self.parent = os.path.dirname(self.filepath)
        base_path = Path(self.filepath).with_suffix('')  # 去掉原有扩展名
        self.memory_index_path = f"{base_path}_index.json"
        print(self.memory_index_path)
    def add_line(self,memory_message:str):
        with open(self.filepath, 'a', encoding='utf-8') as f:
            f.writelines(memory_message+"\n")
            line_bytes = (memory_message + "\n").encode('utf-8')
            self.offsets.append(self.offsets[-1] + len(line_bytes))

    def get_line(self, line_index: int) -> dict:
        """O(1) 时间复杂度读取任意行"""
        if line_index < 0 or line_index >= len(self.offsets):
            raise IndexError(f"行号 {line_index} 超出范围 [0, {len(self.offsets)-1}]")
        
        with open(self.filepath, 'r', encoding='utf-8') as f:
            f.seek(self.offsets[line_index])  # 直接跳到指定位置
            line = f.readline()
            return json.loads(line.strip())
    def load_history(self):
        pass
    def save_memory_index(self):
        with open(self.memory_index_path, "w", encoding="utf-8") as f:
            json.dump(self.offsets, f, ensure_ascii=False, indent=2)
        
    def load_memory_index(self):
        with open(self.memory_index_path, "r", encoding="utf-8") as f:
            self.offsets=json.load(f)


if __name__=="__main__":

    # 使用
    accessor = JsonlRandomAccess(Path("/home/tao/gem_code/huge_file.jsonl"))
    message=Memory_Unit(type="message",content="thank you i am gem code!\n")
    for i in range(10):
        accessor.add_line(message.model_dump_json())
    accessor.save_memory_index()
    accessor.load_memory_index()
    for i in range(10):
        print(accessor.get_line(i))
