from config import Config,createOpenAIClient,Message,ToolCall
from tool import TOOLS,run_tool,parseToolArguments
from skill import Skill,load_skills,format_skill_for_prompt
from openai import AsyncOpenAI
from config import get_system_prompt
from typing import Callable, Optional,Dict,Any
import asyncio
from rich import print
from rich.console import Console
from rich.table import Table
from decorate import pc_gray,pc_blue,pc_cyan,pc_magenta
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
        self._init_task=asyncio.create_task(self._initialize_system_prompt(config.skills_dir))
        
    async def _initialize_system_prompt(self,skills_dir:Optional[str]):
        system_prompt=get_system_prompt()
        if skills_dir:
            self.skills = await load_skills(skills_dir)
            skills_prompt = format_skill_for_prompt(self.skills)
            if skills_prompt:
                system_prompt += "\n\n" + skills_prompt
            self.history = [Message(role="system", content=system_prompt)]
    async def init(self):
        await self._init_task

    async def chat(self,user_input: str, on_chunk: Optional[Callable[[str], None]] = None) -> None:
        self.history.append(Message(role="user", content=user_input))
        while True:
            console.print(pc_gray("🤖 Thinking...")) 
            stream =await self.client.chat.completions.create(
                model=self.model,
                messages=[_message_to_dict(m) for m in self.history], # type: ignore
                #extra_body={"reasoning_split": True},
                stream=True,
                tools=TOOLS,# type: ignore
                tool_choice="auto",
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
    def get_history(self)->list[Message]:
        return self.history
    
    def clear_history(self):
        self.history=[Message(role="system", content=get_system_prompt())]

