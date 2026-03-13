from __future__ import annotations

import asyncio
import json
import math
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from rich.console import Console
from ulid import ULID
from uuid import UUID

from .config import (
    Config,
    create_openai_client,
    get_system_prompt,
    resolve_api_mode,
)
from .context_manager import Context_Manager
from .mcp_client import MCPClient, create_mcp_client_with_config, load_mcp_config_from_env
from .memory import JsonlRandomAccess, message_to_memory_unit
from .models import ContextUsageSnapshot, FunctionCall, Message, ToolCall
from .skill import Skill, SkillTool, format_one_skill_for_prompt, load_skills
from .tool import (
    clone_tools,
    formatted_tool_output,
    get_mcp_client,
    parse_tool_arguments,
    run_bash,
    run_fetch_url_to_markdown,
    run_glob,
    run_grep,
    run_read_file,
    run_str_replace_file,
    run_write_file,
    set_mcp_client,
)

console = Console()

try:
    import tiktoken
except ImportError:  # pragma: no cover - exercised only when tiktoken is absent
    tiktoken = None


def _message_to_chat_dict(message: Message) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": tool_call.type,
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
            for tool_call in message.tool_calls
        ]
    if message.tool_call_id:
        msg["tool_call_id"] = message.tool_call_id
    return msg


def _text_to_response_input(text: Optional[str]) -> List[Dict[str, str]]:
    if not text:
        return []
    return [{"type": "input_text", "text": text}]


def _assistant_text_to_response_output(text: Optional[str]) -> List[Dict[str, Any]]:
    if not text:
        return []
    return [{"type": "output_text", "text": text, "annotations": []}]


class Session:
    def __init__(self, config: Config, memory_access: JsonlRandomAccess):
        self.session_id: UUID = ULID().to_uuid()
        self.config = config
        self.workdir = config.workdir
        self.history: List[Message] = []
        self.client = create_openai_client(config)
        self.model = config.model
        self.api_mode = resolve_api_mode(config)
        self.skills: List[Skill] = []
        self.mcp_client: Optional[MCPClient] = None
        self._all_tools: List[Dict[str, Any]] = clone_tools()
        self.encoded_cwd = str(Path(config.workdir).expanduser()).replace("/", "-").lstrip("-")
        self.storage_dir = Path(config.memory_compaction_path).expanduser() / self.encoded_cwd
        self.transcript_path = self.storage_dir / f"{self.session_id}.jsonl"
        self.memory_acess = memory_access
        self.max_context_tokens = 200000
        self.used_context = 0
        self.context_manager = Context_Manager()
        self._token_encoder = self._build_token_encoder()
        self._tool_schema_token_estimate = 0
        self.context_usage = ContextUsageSnapshot(
            used_tokens=0,
            max_tokens=self.max_context_tokens,
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            tool_schema_tokens=0,
            source="estimated",
            server_tokens=None,
        )
        self._init_task = asyncio.create_task(
            self._initialize_system_prompt(config.skills_dir)
        )

    def _build_token_encoder(self):
        """Best-effort token encoder for live context estimation.

        `tiktoken` gives the closest local approximation for OpenAI-family
        tokenization. We keep the import optional and fall back to a documented
        heuristic for other providers or environments where `tiktoken` is not
        installed.
        """

        if tiktoken is None:
            return None
        try:
            return tiktoken.encoding_for_model(self.model)
        except Exception:
            return tiktoken.get_encoding("cl100k_base")

    def _estimate_text_tokens(self, text: str) -> int:
        if not text:
            return 0

        if self._token_encoder is not None:
            try:
                return len(self._token_encoder.encode(text))
            except Exception:
                pass

        # Heuristic fallback:
        # - CJK characters usually map closer to 1 token each.
        # - ASCII-heavy code and prose are often around 4 chars/token.
        # - Other Unicode text tends to sit between those two extremes.
        cjk_chars = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
        ascii_chars = sum(1 for char in text if ord(char) < 128)
        other_chars = max(len(text) - cjk_chars - ascii_chars, 0)
        ascii_tokens = math.ceil(ascii_chars / 4)
        other_tokens = math.ceil(other_chars / 2)
        return cjk_chars + ascii_tokens + other_tokens

    def _estimate_message_tokens(self, message: Message) -> int:
        """Estimate a single message's contribution to the context window."""

        total = 6  # role/header framing overhead
        total += self._estimate_text_tokens(message.role)
        total += self._estimate_text_tokens(message.content or "")

        if message.tool_calls:
            for tool_call in message.tool_calls:
                total += 12
                total += self._estimate_text_tokens(tool_call.id)
                total += self._estimate_text_tokens(tool_call.function.name)
                total += self._estimate_text_tokens(tool_call.function.arguments)

        if message.tool_call_id:
            total += 4 + self._estimate_text_tokens(message.tool_call_id)

        return total

    def _estimate_streaming_tool_call_tokens(
        self,
        tool_calls: Optional[List[ToolCall]],
    ) -> int:
        if not tool_calls:
            return 0
        return sum(
            12
            + self._estimate_text_tokens(tool_call.id)
            + self._estimate_text_tokens(tool_call.function.name)
            + self._estimate_text_tokens(tool_call.function.arguments)
            for tool_call in tool_calls
        )

    def _estimate_history_tokens(self) -> int:
        request_wrapper_tokens = 12
        return (
            request_wrapper_tokens
            + self._tool_schema_token_estimate
            + sum(self._estimate_message_tokens(message) for message in self.history)
        )

    def _recalculate_context_usage(
        self,
        *,
        streaming_content: str = "",
        streaming_reasoning: str = "",
        streaming_tool_calls: Optional[List[ToolCall]] = None,
        server_total_tokens: Optional[int] = None,
    ) -> None:
        """Recompute the live context snapshot used by the session and TUI.

        Providers usually emit token usage only after a response finishes, which
        is too late for a responsive interface. We therefore estimate the active
        prompt + streamed output locally during generation and overwrite the
        display with provider-reported totals when they arrive.
        """

        estimated_input_tokens = self._estimate_history_tokens()
        estimated_output_tokens = (
            self._estimate_text_tokens(streaming_content)
            + self._estimate_text_tokens(streaming_reasoning)
            + self._estimate_streaming_tool_call_tokens(streaming_tool_calls)
        )
        estimated_total = estimated_input_tokens + estimated_output_tokens

        used_tokens = server_total_tokens if server_total_tokens is not None else estimated_total
        self.used_context = used_tokens
        self.context_manager.update_used_context(used_tokens)
        self.context_usage = ContextUsageSnapshot(
            used_tokens=used_tokens,
            max_tokens=self.max_context_tokens,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            tool_schema_tokens=self._tool_schema_token_estimate,
            source="server" if server_total_tokens is not None else "estimated",
            server_tokens=server_total_tokens,
        )

    def get_context_usage_snapshot(self) -> ContextUsageSnapshot:
        return self.context_usage

    async def _initialize_system_prompt(self, skills_dir: Optional[str]) -> None:
        system_prompt = get_system_prompt(self.workdir)
        if skills_dir:
            self.skills = await load_skills(skills_dir)
            skill_tools = [
                SkillTool(skill.name, skill.description).to_openai_function()
                for skill in self.skills
            ]
            self._all_tools.extend(skill_tools)

        await self._init_mcp_client()

        message = Message(role="system", content=system_prompt)
        self.history = [message]
        memory_unit = message_to_memory_unit(message, "message")
        assert memory_unit is not None
        self.memory_acess.add_line(memory_unit.model_dump_json())

        # Tool schemas can take a non-trivial slice of the context window,
        # especially once MCP and skill tools are loaded. We therefore estimate
        # them once initialization is complete and include them in every live
        # usage snapshot.
        self._tool_schema_token_estimate = self._estimate_text_tokens(
            json.dumps(self._all_tools, ensure_ascii=False)
        )
        self._recalculate_context_usage()

    async def _init_mcp_client(self) -> None:
        """Initialize MCP and load tools declared by connected servers.

        The client now accepts an explicit config path from `Config` and supports
        both Streamable HTTP and legacy SSE transports. This keeps the project
        aligned with the current MCP transport guidance without breaking existing
        `/sse` configurations.
        """

        try:
            mcp_config = load_mcp_config_from_env(self.config.mcp_config_path)
            if not mcp_config:
                console.print("🔌 No MCP servers configured")
                return

            console.print(f"🔌 Connecting to {len(mcp_config)} MCP servers...")
            self.mcp_client = await create_mcp_client_with_config(mcp_config)
            set_mcp_client(self.mcp_client)

            self._all_tools.extend(self.mcp_client.get_all_tools_openai_format())

            for server_name, status in self.mcp_client.get_all_status().items():
                if getattr(status, "status", None) == "connected":
                    tool_count = len(self.mcp_client.get_server_tools(server_name))
                    console.print(f"  ✓ {server_name}: connected ({tool_count} tools)")
                else:
                    console.print(
                        f"  ✗ {server_name}: failed - {getattr(status, 'error', 'Unknown error')}"
                    )
        except Exception as exc:
            console.print(f"⚠️  MCP initialization failed: {exc}")
            self.mcp_client = None
            set_mcp_client(None)

    async def init(self) -> None:
        await self._init_task

    def _history_to_responses_input(self) -> List[Dict[str, Any]]:
        """Convert local message history into Responses API input items.

        We intentionally reconstruct the conversation on every request instead of
        relying on `previous_response_id`. That makes session restore and local
        compaction deterministic because the server never becomes the only source
        of truth for conversation state.
        """

        items: List[Dict[str, Any]] = []
        for message in self.history:
            if message.role == "system":
                items.append(
                    {
                        "type": "message",
                        "role": "system",
                        "content": _text_to_response_input(message.content),
                    }
                )
            elif message.role == "user":
                items.append(
                    {
                        "type": "message",
                        "role": "user",
                        "content": _text_to_response_input(message.content),
                    }
                )
            elif message.role == "assistant":
                if message.content:
                    items.append(
                        {
                            "type": "message",
                            "id": str(message.id),
                            "role": "assistant",
                            "status": "completed",
                            "content": _assistant_text_to_response_output(message.content),
                        }
                    )
                if message.tool_calls:
                    for tool_call in message.tool_calls:
                        items.append(
                            {
                                "type": "function_call",
                                "id": tool_call.id,
                                "call_id": tool_call.id,
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments,
                                "status": "completed",
                            }
                        )
            elif message.role == "tool" and message.tool_call_id:
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content or "",
                    }
                )
        return items

    async def chat(
        self,
        user_input: str,
        on_reasoning: Optional[Callable[[str], None]] = None,
        on_content: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, dict], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
        on_turn_end: Optional[Callable[[str, str, bool], None]] = None,
    ) -> None:
        if self.api_mode == "responses":
            await self._chat_with_responses(
                user_input,
                on_reasoning=on_reasoning,
                on_content=on_content,
                on_tool_start=on_tool_start,
                on_tool_result=on_tool_result,
                on_turn_end=on_turn_end,
            )
            return

        await self._chat_with_chat_completions(
            user_input,
            on_reasoning=on_reasoning,
            on_content=on_content,
            on_tool_start=on_tool_start,
            on_tool_result=on_tool_result,
            on_turn_end=on_turn_end,
        )

    async def _chat_with_chat_completions(
        self,
        user_input: str,
        on_reasoning: Optional[Callable[[str], None]] = None,
        on_content: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, dict], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
        on_turn_end: Optional[Callable[[str, str, bool], None]] = None,
    ) -> None:
        message = Message(role="user", content=user_input)
        self.history.append(message)
        memory_unit = message_to_memory_unit(message, "message")
        assert memory_unit is not None
        self.memory_acess.add_line(memory_unit.model_dump_json())
        self._recalculate_context_usage()

        while True:
            console.print("🤖 Thinking...")
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=[_message_to_chat_dict(m) for m in self.history],  # type: ignore[arg-type]
                extra_body={"reasoning_split": True},
                stream=True,
                tools=self._all_tools,  # type: ignore[arg-type]
                tool_choice="auto",
                max_tokens=1024 * 32,
            )

            has_tool_calls = False
            content_buffer = ""
            reasoning_buffer = ""
            tool_calls_map: Dict[str, ToolCall] = {}

            server_total_tokens: Optional[int] = None

            async for chunk in stream:
                delta = chunk.choices[0].delta
                if chunk.usage is not None:
                    server_total_tokens = chunk.usage.total_tokens

                if hasattr(delta, "reasoning_details") and delta.reasoning_details:
                    for detail in delta.reasoning_details:
                        if isinstance(detail, dict) and "text" in detail:
                            reasoning_delta = detail["text"]
                            if reasoning_delta and on_reasoning:
                                on_reasoning(reasoning_delta)
                            reasoning_buffer += reasoning_delta

                if delta.content:
                    content_delta = delta.content
                    if content_delta:
                        if on_content:
                            on_content(content_delta)
                        content_buffer += content_delta

                if delta.tool_calls:
                    has_tool_calls = True
                    for tool_call in delta.tool_calls:
                        if tool_call.id:
                            existing = tool_calls_map.get(tool_call.id)
                            if existing and tool_call.function.arguments:
                                existing.function.arguments += tool_call.function.arguments
                            else:
                                tool_calls_map[tool_call.id] = ToolCall(
                                    id=tool_call.id,
                                    function=FunctionCall(
                                        name=tool_call.function.name or "",
                                        arguments=tool_call.function.arguments or "",
                                    ),
                                    type=tool_call.type or "function",
                                )
                        elif tool_call.function.arguments and tool_calls_map:
                            last_tool_call = list(tool_calls_map.values())[-1]
                            last_tool_call.function.arguments += tool_call.function.arguments

                self._recalculate_context_usage(
                    streaming_content=content_buffer,
                    streaming_reasoning=reasoning_buffer,
                    streaming_tool_calls=list(tool_calls_map.values()) if tool_calls_map else None,
                    server_total_tokens=server_total_tokens,
                )

            await self._handle_model_turn(
                content_buffer=content_buffer,
                reasoning_buffer=reasoning_buffer,
                has_tool_calls=has_tool_calls,
                tool_calls=list(tool_calls_map.values()) if has_tool_calls else None,
                user_prompt=user_input,
                on_turn_end=on_turn_end,
                on_tool_start=on_tool_start,
                on_tool_result=on_tool_result,
            )

            if not has_tool_calls:
                break

    async def _chat_with_responses(
        self,
        user_input: str,
        on_reasoning: Optional[Callable[[str], None]] = None,
        on_content: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, dict], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
        on_turn_end: Optional[Callable[[str, str, bool], None]] = None,
    ) -> None:
        message = Message(role="user", content=user_input)
        self.history.append(message)
        memory_unit = message_to_memory_unit(message, "message")
        assert memory_unit is not None
        self.memory_acess.add_line(memory_unit.model_dump_json())
        self._recalculate_context_usage()

        while True:
            console.print("🤖 Thinking...")
            stream = await self.client.responses.create(
                model=self.model,
                input=self._history_to_responses_input(),
                tools=self._all_tools,  # type: ignore[arg-type]
                tool_choice="auto",
                max_output_tokens=1024 * 32,
                stream=True,
            )

            content_buffer = ""
            reasoning_buffer = ""
            tool_calls_by_id: Dict[str, ToolCall] = {}

            server_total_tokens: Optional[int] = None

            async for event in stream:
                event_type = getattr(event, "type", "")

                if event_type in {
                    "response.reasoning_text.delta",
                    "response.reasoning_summary_text.delta",
                }:
                    reasoning_delta = getattr(event, "delta", "")
                    if reasoning_delta and on_reasoning:
                        on_reasoning(reasoning_delta)
                    reasoning_buffer += reasoning_delta
                    self._recalculate_context_usage(
                        streaming_content=content_buffer,
                        streaming_reasoning=reasoning_buffer,
                        streaming_tool_calls=list(tool_calls_by_id.values()) if tool_calls_by_id else None,
                        server_total_tokens=server_total_tokens,
                    )
                    continue

                if event_type == "response.output_text.delta":
                    content_delta = getattr(event, "delta", "")
                    if content_delta and on_content:
                        on_content(content_delta)
                    content_buffer += content_delta
                    self._recalculate_context_usage(
                        streaming_content=content_buffer,
                        streaming_reasoning=reasoning_buffer,
                        streaming_tool_calls=list(tool_calls_by_id.values()) if tool_calls_by_id else None,
                        server_total_tokens=server_total_tokens,
                    )
                    continue

                if event_type == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if getattr(item, "type", None) == "function_call":
                        tool_calls_by_id[item.call_id] = ToolCall(
                            id=item.call_id,
                            function=FunctionCall(
                                name=item.name,
                                arguments=item.arguments or "",
                            ),
                            type="function",
                        )
                    self._recalculate_context_usage(
                        streaming_content=content_buffer,
                        streaming_reasoning=reasoning_buffer,
                        streaming_tool_calls=list(tool_calls_by_id.values()) if tool_calls_by_id else None,
                        server_total_tokens=server_total_tokens,
                    )
                    continue

                if event_type == "response.function_call_arguments.delta":
                    item_id = getattr(event, "item_id", "")
                    # `output_item.added` usually gives us the final `call_id`. If a
                    # provider emits deltas before the item is added, we still want
                    # deterministic accumulation, so we fall back to the opaque
                    # stream item id until the final item arrives.
                    target_id = item_id
                    if target_id not in tool_calls_by_id:
                        tool_calls_by_id[target_id] = ToolCall(
                            id=target_id,
                            function=FunctionCall(name="", arguments=""),
                            type="function",
                        )
                    tool_calls_by_id[target_id].function.arguments += getattr(event, "delta", "")
                    self._recalculate_context_usage(
                        streaming_content=content_buffer,
                        streaming_reasoning=reasoning_buffer,
                        streaming_tool_calls=list(tool_calls_by_id.values()) if tool_calls_by_id else None,
                        server_total_tokens=server_total_tokens,
                    )
                    continue

                if event_type == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if getattr(item, "type", None) == "function_call":
                        tool_calls_by_id[item.call_id] = ToolCall(
                            id=item.call_id,
                            function=FunctionCall(
                                name=item.name,
                                arguments=item.arguments or "",
                            ),
                            type="function",
                        )
                    self._recalculate_context_usage(
                        streaming_content=content_buffer,
                        streaming_reasoning=reasoning_buffer,
                        streaming_tool_calls=list(tool_calls_by_id.values()) if tool_calls_by_id else None,
                        server_total_tokens=server_total_tokens,
                    )
                    continue

                if event_type == "response.completed":
                    usage = getattr(event.response, "usage", None)
                    total_tokens = getattr(usage, "total_tokens", None)
                    if total_tokens is not None:
                        server_total_tokens = total_tokens
                    self._recalculate_context_usage(
                        streaming_content=content_buffer,
                        streaming_reasoning=reasoning_buffer,
                        streaming_tool_calls=list(tool_calls_by_id.values()) if tool_calls_by_id else None,
                        server_total_tokens=server_total_tokens,
                    )
                    continue

                if event_type == "response.error":
                    raise RuntimeError(getattr(event, "message", "Unknown Responses API error"))

                if event_type == "response.failed":
                    raise RuntimeError(
                        f"Responses API call failed with status {getattr(event.response, 'status', 'unknown')}"
                    )

            self._recalculate_context_usage(
                streaming_content=content_buffer,
                streaming_reasoning=reasoning_buffer,
                streaming_tool_calls=list(tool_calls_by_id.values()) if tool_calls_by_id else None,
                server_total_tokens=server_total_tokens,
            )

            normalized_tool_calls = [
                tool_call
                for tool_call in tool_calls_by_id.values()
                if tool_call.function.name
            ]

            await self._handle_model_turn(
                content_buffer=content_buffer,
                reasoning_buffer=reasoning_buffer,
                has_tool_calls=bool(normalized_tool_calls),
                tool_calls=normalized_tool_calls or None,
                user_prompt=user_input,
                on_turn_end=on_turn_end,
                on_tool_start=on_tool_start,
                on_tool_result=on_tool_result,
            )

            if not normalized_tool_calls:
                break

    async def _handle_model_turn(
        self,
        *,
        content_buffer: str,
        reasoning_buffer: str,
        has_tool_calls: bool,
        tool_calls: Optional[List[ToolCall]],
        user_prompt: str,
        on_turn_end: Optional[Callable[[str, str, bool], None]],
        on_tool_start: Optional[Callable[[str, dict], None]],
        on_tool_result: Optional[Callable[[str, str], None]],
    ) -> None:
        message = Message(
            role="assistant",
            content=content_buffer,
            tool_calls=tool_calls if has_tool_calls else None,
        )
        self.history.append(message)
        memory_unit = message_to_memory_unit(message, "message")
        assert memory_unit is not None
        self.memory_acess.add_line(memory_unit.model_dump_json())

        self.context_manager.microcompaction(self.history, self.memory_acess)
        await self.context_manager.autocompaction(
            self.chat_one_step,
            self.history,
            self.memory_acess,
            user_prompt=user_prompt,
            system_prompt=get_system_prompt(self.workdir),
        )
        self._recalculate_context_usage()

        if on_turn_end:
            on_turn_end(content_buffer, reasoning_buffer, has_tool_calls)

        if not has_tool_calls or not message.tool_calls:
            return

        for tool_call in message.tool_calls:
            args = parse_tool_arguments(tool_call)
            tool_name = tool_call.function.name

            if on_tool_start:
                on_tool_start(tool_name, args)
            else:
                console.print("")
                console.print(f"🛠️  Executing tool: {tool_name}")

            result = await self.run_tool(tool_name, args, self.workdir)

            if on_tool_result:
                on_tool_result(tool_name, result)
            else:
                console.print("👁 OBSERVE")
                console.print(f"Result:\n{result}")
                console.print("")

            tool_message = Message(role="tool", content=result, tool_call_id=tool_call.id)
            self.history.append(tool_message)
            memory_unit = message_to_memory_unit(tool_message, "message")
            assert memory_unit is not None
            self.memory_acess.add_line(memory_unit.model_dump_json())
            self._recalculate_context_usage()

        if not on_tool_start and not on_tool_result:
            console.print("🔄 REPEAT")

    async def chat_one_step(self, user_input: str) -> str:
        """Run a non-tool model step used by automatic compaction.

        Auto-compaction should not recursively trigger more tool calls. We keep
        the implementation backend-aware so the same summary path works whether
        the main session uses Chat Completions or Responses.
        """

        user_message = Message(role="user", content=user_input)
        self.history.append(user_message)
        memory_unit = message_to_memory_unit(user_message, "message")
        assert memory_unit is not None
        self.memory_acess.add_line(memory_unit.model_dump_json())
        self._recalculate_context_usage()

        if self.api_mode == "responses":
            response = await self.client.responses.create(
                model=self.model,
                input=self._history_to_responses_input(),
                tool_choice="none",
                max_output_tokens=1024 * 16,
            )
            output_parts: List[str] = []
            for item in response.output:
                if getattr(item, "type", None) != "message":
                    continue
                for content_item in getattr(item, "content", []):
                    if getattr(content_item, "type", None) == "output_text":
                        output_parts.append(content_item.text)
            assistant_content = "".join(output_parts)
        else:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[_message_to_chat_dict(m) for m in self.history],  # type: ignore[arg-type]
                extra_body={"reasoning_split": True},
                tools=self._all_tools,  # type: ignore[arg-type]
                tool_choice="none",
                max_tokens=1024 * 16,
            )
            assistant_content = response.choices[0].message.content or ""

        assistant_message = Message(role="assistant", content=assistant_content)
        self.history.append(assistant_message)
        memory_unit = message_to_memory_unit(assistant_message, "message")
        assert memory_unit is not None
        self.memory_acess.add_line(memory_unit.model_dump_json())
        self._recalculate_context_usage()
        return assistant_content

    async def run_tool(self, name: str, args: Dict[str, Any], workdir: str) -> str:
        try:
            if name.startswith("skill__"):
                skill_name = name.replace("skill__", "")
                skill = next((skill for skill in self.skills if skill.name == skill_name), None)
                if skill:
                    return format_one_skill_for_prompt(skill)
                return f"Error: Can't find the skill {skill_name}"

            if name.startswith("mcp__"):
                mcp_client = get_mcp_client()
                if mcp_client is None:
                    return f"Error: MCP client not initialized, cannot call tool: {name}"
                try:
                    result = await mcp_client.call_tool(name, args)
                    return formatted_tool_output(result)
                except Exception as exc:
                    return f"Error calling MCP tool {name}: {str(exc)}"

            if name == "bash":
                output = await run_bash(
                    args.get("command", ""),
                    workdir,
                    timeout_ms=args.get("timeout_ms", 120000),
                )
                return formatted_tool_output(output)
            if name == "read_file":
                return formatted_tool_output(await run_read_file(args.get("path", ""), workdir))
            if name == "write_file":
                return formatted_tool_output(
                    await run_write_file(args.get("path", ""), args.get("content", ""), workdir)
                )
            if name == "StrReplaceFile":
                return formatted_tool_output(
                    await run_str_replace_file(args.get("path", ""), args.get("edits", []), workdir)
                )
            if name == "fetch_url":
                return formatted_tool_output(await run_fetch_url_to_markdown(args.get("url", "")))
            if name == "Glob":
                return formatted_tool_output(
                    await run_glob(args.get("pattern", ""), workdir, args.get("path"))
                )
            if name == "Grep":
                return formatted_tool_output(
                    await run_grep(
                        pattern=args.get("pattern", ""),
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
                        workdir=workdir,
                    )
                )

            return f"Error: Unknown tool: {name}"
        except Exception as exc:
            return f"Error executing tool {name}: {str(exc)}"

    async def cleanup(self) -> None:
        if self.mcp_client:
            try:
                await self.mcp_client.disconnect_all()
            except Exception:
                pass
            finally:
                set_mcp_client(None)
                self.mcp_client = None

    def get_history(self) -> List[Message]:
        return self.history

    def set_history(self, messages: Optional[List[Message]]) -> None:
        self.history = messages or []
        self._recalculate_context_usage()

    def clear_history(self) -> None:
        self.history = [Message(role="system", content=get_system_prompt(self.workdir))]
        self._recalculate_context_usage()
