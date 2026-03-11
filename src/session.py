from .config import Config, createOpenAIClient
from .models import Message, ToolCall, FunctionCall
from .tool import TOOLS,parse_tool_arguments, set_mcp_client,_mcp_client,formatted_tool_output,\
run_bash,run_fetch_url_to_markdown,run_read_file,run_str_replace_file,run_write_file,run_glob,run_grep
from .skill import Skill,load_skills,format_skill_for_prompt,SkillTool,format_one_skill_for_prompt
from .mcp_client import MCPClient, load_mcp_config_from_env, create_mcp_client_with_config
from openai import AsyncOpenAI
from .config import get_system_prompt
from typing import Callable, Optional,Dict,Any,List
import asyncio
from rich import print
from rich.console import Console
from rich.table import Table
from .decorate import pc_gray,pc_blue,pc_cyan,pc_magenta
from ulid import ULID
from uuid import UUID
from pydantic import Field
from .memory import JsonlRandomAccess, Memory_Unit, message_to_memory_unit
from pathlib import Path
from .context_manager import Context_Manager
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
    def __init__(self, config: Config,memory_access:JsonlRandomAccess):
        self.session_id:UUID=Field(default_factory=ULID().to_uuid)
        self.config:Config = config
        self.workdir: str= config.workdir
        self.history: list[Message] =[]
        self.client:AsyncOpenAI = createOpenAIClient(config.api_key,config.base_url)
        self.model: str = config.model
        self.skills:list[Skill]=[]
        self.mcp_client: Optional[MCPClient] = None
        self._all_tools: List[Dict[str, Any]] = list(TOOLS)  # 复制内置工具
        self.encoded_cwd = str(Path(config.workdir).expanduser()).replace("/", "-").lstrip("-")
        self.storage_dir = Path(config.memory_compaction_path).joinpath(self.encoded_cwd).expanduser()
        self.transcript_path = self.storage_dir / f"{self.session_id}.jsonl"
        self.memory_acess=memory_access
        self.used_context:int=0
        self.context_manager=Context_Manager()
        self._init_task=asyncio.create_task(self._initialize_system_prompt(config.skills_dir))
    async def _initialize_system_prompt(self,skills_dir:Optional[str]):
        system_prompt=get_system_prompt()
        if skills_dir:
            self.skills = await load_skills(skills_dir)
            skill_tools=[SkillTool(skill.name,skill.description).to_openai_function() for skill in self.skills]
            self._all_tools.extend(skill_tools)
        
        # 初始化 MCP 客户端
        await self._init_mcp_client()
        message=Message(role="system", content=system_prompt)
        self.history = [message]
        memory_unit = message_to_memory_unit(message, "message")
        assert memory_unit is not None
        self.memory_acess.add_line(memory_unit.model_dump_json())
    
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

    async def chat(
        self,
        user_input: str,
        on_reasoning: Optional[Callable[[str], None]] = None,
        on_content: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, dict], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
        on_turn_end: Optional[Callable[[str, str, bool], None]] = None
    ) :
        """
        处理聊天请求，支持 reasoning 和 content 分离输出
        
        Args:
            user_input: 用户输入
            on_reasoning: reasoning content 回调（思考过程），可为 None
            on_content: 正式输出内容回调，可为 None
            on_tool_start: tool 开始调用回调 (tool_name, args)，可为 None
            on_tool_result: tool 执行结果回调 (tool_name, result)，可为 None
            on_turn_end: 每次 API 调用结束时回调 (content, reasoning, has_more)，
                        has_more=True 表示还有 tool 调用需要处理，TUI 应该等待
        """
        message=Message(role="user", content=user_input)
        self.history.append(message)
        memory_unit = message_to_memory_unit(message, "message")
        assert memory_unit is not None
        self.memory_acess.add_line(memory_unit.model_dump_json())
        while True:
            console.print(pc_gray("🤖 Thinking...")) 
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=[_message_to_dict(m) for m in self.history],#type: ignore
                extra_body={"reasoning_split": True},
                stream=True,
                #实际上，claude code针对工具调用进行了优化，除了预先加载的tools，所有的tools（包括mcp tools和skill tools），
                #都使用懒加载的方式，通过tool search工具获得，然而claude code的tool search是通过服务器端实现的，
                #也可以通过客户端实现，即tools=tool_search(self._all_tools,user_input)
                tools=self._all_tools,#type: ignore
                tool_choice="auto",
                max_tokens=1024*32
            ) #type: ignore
            has_tool_calls = False
            content_buffer = ""
            reasoning_buffer = ""
            tool_calls_map: Dict[str, ToolCall] = {}
            
            async for chunk in stream:
                delta = chunk.choices[0].delta
                # 流式响应中，usage 只在最后一个 chunk 返回
                if chunk.usage is not None:
                    self.used_context = chunk.usage.total_tokens
                    self.context_manager.update_used_context(self.used_context)
                # 处理 reasoning content（思考过程）
                # MiniMax API 返回格式: reasoning_details = [{"text": "..."}]
                if hasattr(delta, "reasoning_details") and delta.reasoning_details:
                    for detail in delta.reasoning_details:
                        if isinstance(detail, dict) and "text" in detail:
                            reasoning_delta = detail["text"]
                            if reasoning_delta and on_reasoning:
                                on_reasoning(reasoning_delta)
                            reasoning_buffer += reasoning_delta
                
                # 处理正式 content 输出
                if delta.content:
                    content_delta = delta.content
                    if content_delta:
                        if on_content:
                            on_content(content_delta)
                        content_buffer += content_delta

                # 处理 tool calls
                if delta.tool_calls:
                    has_tool_calls = True
                    for tc in delta.tool_calls:
                        if tc.id:
                            is_existing = tool_calls_map.get(tc.id) is not None
                            if is_existing:
                                if tc.function.arguments:
                                    tool_calls_map[tc.id].function.arguments += tc.function.arguments
                            else:
                                # 手动提取 function 属性，避免 Pydantic 验证问题
                                func_call = FunctionCall(
                                    name=tc.function.name if tc.function.name else "",
                                    arguments=tc.function.arguments if tc.function.arguments else ""
                                )
                                tool_calls_map[tc.id] = ToolCall(
                                    id=tc.id,
                                    function=func_call,
                                    type=tc.type or "function"
                                )
                        elif tc.function.arguments:
                            entries = list(tool_calls_map.values())
                            if len(entries) > 0:
                                lastentry = entries[-1]
                                lastentry.function.arguments += tc.function.arguments
            
            # 构建 assistant message，保存到历史记录
            # 注意：只保存正式 content，reasoning 是过程不保存
            message = Message(
                role="assistant",
                content=content_buffer,
                tool_calls=list(tool_calls_map.values()) if has_tool_calls else None
            )
            self.history.append(message)
            memory_unit = message_to_memory_unit(message, "message")
            assert memory_unit is not None
            self.memory_acess.add_line(memory_unit.model_dump_json())

            self.context_manager.microcompaction(self.history,self.memory_acess)
            await self.context_manager.autocompaction(self.chat_one_step,self.history,self.memory_acess)
            # 通知本轮结束 (content, reasoning, has_more)
            if on_turn_end:
                on_turn_end(content_buffer, reasoning_buffer, has_tool_calls)
            if has_tool_calls and message.tool_calls:
                for toolcall in message.tool_calls:
                    args = parse_tool_arguments(toolcall)
                    tool_name = toolcall.function.name
                    
                    # 通知 tool 开始调用
                    if on_tool_start:
                        on_tool_start(tool_name, args)
                    else:
                        # 默认输出到 console（CLI 模式）
                        console.print("")
                        console.print(pc_blue(f"🛠️  Executing tool: {tool_name}"))
                    
                    result = await self.run_tool(tool_name, args, self.workdir)
                    
                    # 通知 tool 执行结果
                    if on_tool_result:
                        on_tool_result(tool_name, result)
                    else:
                        # 默认输出到 console（CLI 模式）
                        console.print(pc_blue("👁 OBSERVE"))
                        console.print(pc_blue(f"Result:\n{result}"))
                        console.print("")
                    
                    message=Message(role="tool", content=result, tool_call_id=toolcall.id)

                    self.history.append(message)
                    memory_unit = message_to_memory_unit(message, "message")
                    assert memory_unit is not None
                    self.memory_acess.add_line(memory_unit.model_dump_json())
                if not on_tool_start and not on_tool_result:
                    console.print(pc_magenta("🔄 REPEAT"))
                continue
            break
    async def chat_one_step(self,user_input:str):
        message=Message(role="user", content=user_input)
        self.history.append(message)
        memory_unit = message_to_memory_unit(message, "message")
        assert memory_unit is not None
        self.memory_acess.add_line(memory_unit.model_dump_json())
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[_message_to_dict(m) for m in self.history],#type: ignore
            extra_body={"reasoning_split": True},
            tools=self._all_tools,#type: ignore
            tool_choice="none",
            max_tokens=1024*32
        )
        if hasattr(response,"choices") and response.choices[0].message.content: #type: ignore
            message=Message(role="assistant", content=response.choices[0].message.content)#type: ignore
            self.history.append(message)
            memory_unit = message_to_memory_unit(message, "message")
            assert memory_unit is not None
            self.memory_acess.add_line(memory_unit.model_dump_json())

        return response.choices[0].message.content#type: ignore

    async def run_tool(self,name: str, args: Dict[str, Any], workdir: str) -> str:
        """
        Execute a tool call (async)
        
        Args:
            name: Tool name ("bash", "read_file", "write_file", "StrReplaceFile","fetch_url", or "mcp__server__tool")
            args: Argument dictionary
            workdir: Working directory
        Returns:
            Formatted result string
        """
        try:
            # Skill 工具调用 - 返回 skill 内容，由调用方添加到 history
            if name.startswith("skill__"):
                skill_name = name.replace("skill__", "")
                # 在 self.skills 中查找匹配的 skill，找不到返回 None
                skill = next((s for s in self.skills if s.name == skill_name), None)
                if skill:
                    return format_one_skill_for_prompt(skill)
                else:
                    return f"Error: Can't find the skill {skill_name}"
            if name.startswith("mcp__"):
                if _mcp_client is None:
                    return f"Error: MCP client not initialized, cannot call tool: {name}"
                try:
                    result = await _mcp_client.call_tool(name, args)
                    return formatted_tool_output(result)
                except Exception as e:
                    return f"Error calling MCP tool {name}: {str(e)}"
            
            # 内置工具调用
            if name == "bash":
                command = args.get("command", "")
                output=await run_bash(command, workdir)
                return formatted_tool_output(output)
            elif name == "read_file":
                path = args.get("path", "")
                output=await run_read_file(path,workdir)
                return formatted_tool_output(output)
            elif name == "write_file":
                path = args.get("path", "")
                content = args.get("content", "")
                output = await run_write_file(path, content, workdir)
                return formatted_tool_output(output)
            elif name == "StrReplaceFile":
                path = args.get("path", "")
                edits = args.get("edits", [])
                output=await run_str_replace_file(path, edits, workdir)
                return formatted_tool_output(output)
            elif name == "fetch_url":
                url=args.get("url","")
                output=await run_fetch_url_to_markdown(url)
                return formatted_tool_output(output)
            elif name == "Glob":
                output = await run_glob(pattern=args.get("pattern"), path=args.get("path"), workdir=workdir)
                return formatted_tool_output(output)
            elif name == "Grep":
                output = await run_grep(
                    pattern=args.get("pattern"),
                    path=args.get("path"),
                    glob_pattern=args.get("glob"),
                    file_type=args.get("type"),
                    output_mode=args.get("output_mode", "files_with_matches"),
                    case_insensitive=args.get("-i", False),
                    show_line_numbers=args.get("-n", False),
                    before_context=args.get("-B", 0),
                    after_context=args.get("-A", 0),
                    context=args.get("-C", 0),
                    head_limit=args.get("head_limit"),
                    multiline=args.get("multiline", False),
                    workdir=workdir
                )
                return formatted_tool_output(output)
            else:
                return f"Error: Unknown tool: {name}"
        except Exception as e:
            return f"Error executing tool {name}: {str(e)}"

    
    async def cleanup(self) -> None:
        """清理资源，断开 MCP 连接"""
        if self.mcp_client:
            try:
                await self.mcp_client.disconnect_all()
            except Exception:
                # 忽略清理过程中的错误
                pass
            finally:
                set_mcp_client(None)
                self.mcp_client = None
    
    def get_history(self)->list[Message]:
        return self.history
    def set_history(self,messages:list[Message]|None):
        if messages==None:
            self.history=[]
        else:
            self.history=messages
    def clear_history(self):
        self.history=[Message(role="system", content=get_system_prompt())]
