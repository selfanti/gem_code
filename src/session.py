from .config import Config,createOpenAIClient,Message,ToolCall
from .tool import TOOLS,run_tool,parseToolArguments, set_mcp_client
from .skill import Skill,load_skills,format_skill_for_prompt
from .mcp_client import MCPClient, load_mcp_config_from_env, create_mcp_client_with_config
from openai import AsyncOpenAI
from .config import get_system_prompt
from typing import Callable, Optional,Dict,Any,List
import asyncio
from rich import print
from rich.console import Console
from rich.table import Table
from .decorate import pc_gray,pc_blue,pc_cyan,pc_magenta
console=Console()

def _message_to_dict(m: Message) -> Dict[str, Any]:
    """Convert Message to API-compatible dict format"""
    msg: Dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        # Convert ToolCall dataclass objects to dicts
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                }
            }
            for tc in m.tool_calls
        ]
    if m.tool_call_id:
        msg["tool_call_id"] = m.tool_call_id
    return msg


class Session:
    def __init__(self, config: Config):
        self.config:Config = config
        self.workdir: str= config.workdir
        self.history: list[Message] =[]
        self.client:AsyncOpenAI = createOpenAIClient(config.api_key,config.base_url)
        self.model: str = config.model
        self.skills:list[Skill]=[]
        self.mcp_client: Optional[MCPClient] = None
        self._all_tools: List[Dict[str, Any]] = list(TOOLS)  # 复制内置工具
        self._init_task=asyncio.create_task(self._initialize_system_prompt(config.skills_dir))
        
    async def _initialize_system_prompt(self,skills_dir:Optional[str]):
        system_prompt=get_system_prompt()
        if skills_dir:
            self.skills = await load_skills(skills_dir)
            skills_prompt = format_skill_for_prompt(self.skills)
            if skills_prompt:
                system_prompt += "\n\n" + skills_prompt
        
        # 初始化 MCP 客户端
        await self._init_mcp_client()
        
        self.history = [Message(role="system", content=system_prompt)]
    
    async def _init_mcp_client(self) -> None:
        """初始化 MCP 客户端并加载工具"""
        try:
            # 从环境变量或配置文件加载 MCP 配置
            mcp_config = load_mcp_config_from_env()
            
            if mcp_config:
                console.print(pc_gray(f"🔌 Connecting to {len(mcp_config)} MCP servers..."))
                self.mcp_client = await create_mcp_client_with_config(mcp_config)
                
                # 设置全局 MCP 客户端供 tool.py 使用
                set_mcp_client(self.mcp_client)
                
                # 合并 MCP 工具到工具列表
                mcp_tools = self.mcp_client.get_all_tools_openai_format()
                self._all_tools.extend(mcp_tools)
                
                # 打印连接状态
                status_map = self.mcp_client.get_all_status()
                for server_name, status in status_map.items():
                    if hasattr(status, 'status') and status.status == "connected":
                        server_tools = self.mcp_client.get_server_tools(server_name)
                        console.print(pc_gray(f"  ✓ {server_name}: connected ({len(server_tools)} tools)"))
                    else:
                        error_msg = getattr(status, 'error', 'Unknown error')
                        console.print(pc_gray(f"  ✗ {server_name}: failed - {error_msg}"))
            else:
                console.print(pc_gray("🔌 No MCP servers configured"))
                
        except Exception as e:
            console.print(pc_gray(f"⚠️  MCP initialization failed: {e}"))
            self.mcp_client = None
            set_mcp_client(None)
    
    async def init(self):
        await self._init_task

    async def chat(self,user_input: str, on_chunk: Optional[Callable[[str], None]] = None) -> None:
        self.history.append(Message(role="user", content=user_input))
        while True:
            console.print(pc_gray("🤖 Thinking...")) 
            stream =await self.client.chat.completions.create(
                model=self.model,
                messages=[_message_to_dict(m) for m in self.history], # type: ignore
                extra_body={"reasoning_split": True},
                stream=True,
                tools=self._all_tools,# type: ignore
                tool_choice="auto",
                max_tokens=1024*32
            )  # type: ignore
            has_tool_calls = False
            full_content = ""
            tool_calls_map:Dict[str,ToolCall] = {}
            async for chunk in stream:
                delta=chunk.choices[0].delta
                if(delta.content):
                    full_content+=delta.content
                    if on_chunk:
                        on_chunk(delta.content)
                if delta.tool_calls:
                    has_tool_calls = True
                    for tc in delta.tool_calls:
                        if(tc.id):
                            is_existing=tool_calls_map.get(tc.id) is not None
                            if(is_existing):
                                if tc.function.arguments:
                                    tool_calls_map[tc.id].function.arguments+=tc.function.arguments
                            else:
                                tool_calls_map[tc.id]=ToolCall(
                                    id=tc.id,
                                    function=tc.function,
                                    type=tc.type
                                )
                        elif (tc.function.arguments):
                            entries=list(tool_calls_map.values())
                            if len(entries) > 0:
                                lastentry = entries[-1]
                                lastentry.function.arguments += tc.function.arguments
            message=Message(role="assistant", content=full_content, tool_calls=list(tool_calls_map.values()) if has_tool_calls else None)
            self.history.append(message)
            if has_tool_calls and  message.tool_calls:
                console.print("")
                for toolcall in message.tool_calls:
                    args=parseToolArguments(toolcall)
                    result=await run_tool(toolcall.function.name,args,self.workdir)
                    console.print(pc_blue("👁 OBSERVE"))
                    console.print(pc_blue(f"🛠️  Tool '{toolcall.function.name}' executed with result:\n{result}"))
                    console.print("")
                    self.history.append(Message(role="tool", content=result, tool_call_id=toolcall.id))

                console.print(pc_magenta("🔄 REPEAT"))
                continue
            break
    
    async def cleanup(self) -> None:
        """清理资源，断开 MCP 连接"""
        if self.mcp_client:
            await self.mcp_client.disconnect_all()
            set_mcp_client(None)
            self.mcp_client = None
    
    def get_history(self)->list[Message]:
        return self.history
    
    def clear_history(self):
        self.history=[Message(role="system", content=get_system_prompt())]
