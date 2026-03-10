from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import List, Optional
from enum import Enum
import json

class Status(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class Address(BaseModel):
    city: str
    street: str
    zipcode: str

class User(BaseModel):
    # Pydantic V2 使用 ConfigDict 替代 class Config
    model_config = ConfigDict(
        json_encoders={
            datetime: lambda v: v.strftime('%Y-%m-%d %H:%M:%S')
        },
        from_attributes=True
    )
    
    id: int
    name: str = Field(..., min_length=2, max_length=50)
    email: Optional[str]=None
    is_active: bool = True
    status: Status = Status.ACTIVE
    created_at: datetime = Field(default_factory=datetime.now)
    addresses: List[Address] = []
    metadata: dict = Field(default_factory=dict)

# 使用示例
user = User(
    id=1, 
    name="王五", 
    addresses=[Address(city="北京", street="长安街", zipcode="100000")]
)

# ✅ 方法1：model_dump_json() -> 直接返回 JSON 字符串（推荐）
json_str = user.model_dump_json(ensure_ascii=False, indent=2)
print("方法1结果：")
print(json_str)

# ✅ 方法2：model_dump() + json.dumps() 需要处理 datetime
dict_obj = user.model_dump()
# 方法2a：手动处理 datetime（复杂，不推荐）
# 方法2b：使用 Pydantic 的 json() 方法替代
json_str2 = user.model_dump_json(ensure_ascii=False)  # 直接使用 model_dump_json
print("\n方法2结果：")
print(json_str2)

# 反序列化
user_parsed = User.model_validate_json(json_str)
print(f"\n反序列化后：{user_parsed.name}")