from __future__ import annotations

from typing import Final, Optional

from .memory import JsonlRandomAccess, Memory_Unit
from .models import Message
from .permissions import is_audit_content

MAX_CONTEXT_SIZE: Final[int] = 200 * 1000
MICRO_COMPACTION_THRESHOLD: Final[int] = int(0.6 * MAX_CONTEXT_SIZE)
AUTO_COMPACTION_THRESHOLD: Final[int] = int(0.8 * MAX_CONTEXT_SIZE)

AUTO_COMPACTION_PROMPT = """
Please summarize the conversation above into a working state that allows
continuation without re-asking questions. Include:
1. User intent
2. Key technical decisions
3. Files touched
4. Errors encountered
5. Pending tasks
6. Current state
7. Exact next step
Focus on: {focus}
"""


class Context_Manager:
    used_context_size: int = 0

    def update_used_context(self, usage: int) -> None:
        self.used_context_size = usage

    def microcompaction(
        self,
        history: list[Message],
        memory_acess: JsonlRandomAccess,
    ) -> None:
        """Replace old tool output with a transcript reference when context is large.

        This keeps the conversational trace intact for the model while moving the
        verbose payload into the JSONL transcript. We only rewrite older tool
        outputs so the most recent observation remains directly visible.
        """

        if self.used_context_size <= MICRO_COMPACTION_THRESHOLD:
            return

        for index, message in enumerate(history):
            if message.role != "tool":
                continue
            if index >= len(history) - 3:
                continue
            if not message.content or "工具输出已经转移到文件" in message.content:
                continue

            message.content = (
                f"工具输出已经转移到文件 {memory_acess.filepath} 中，"
                f"可通过消息 id={message.id} 在 JSONL 中查看完整内容。"
            )

    async def autocompaction(
        self,
        chat_one_step,
        history: list[Message],
        memory_acess: JsonlRandomAccess,
        user_prompt: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        """Summarize old context into a compact checkpoint.

        The previous implementation computed a summary but never actually
        replaced the caller's history because it only rebound the local `history`
        variable. We now mutate the shared list in place so compaction has a
        real effect.
        """

        if self.used_context_size <= AUTO_COMPACTION_THRESHOLD:
            return

        prompt = AUTO_COMPACTION_PROMPT.format(focus=user_prompt or "the latest user request")
        boundary_message = Memory_Unit(type="compact_boundary")
        memory_acess.add_line(boundary_message.model_dump_json())

        compaction_result = await chat_one_step(user_input=prompt)
        summary = Memory_Unit(type="summary", content=compaction_result)
        memory_acess.add_line(summary.model_dump_json())
        history.clear()
        history.append(Message(role="system", timestamp=summary.timestamp, id=summary.id, content=system_prompt+summary.content if system_prompt and summary.content else system_prompt))
        # Rebuild the in-memory history from the transcript instead of keeping
        # only the raw summary. This makes compaction immediately usable because
        # the model regains a compact summary plus a small slice of recent raw
        # context around the compression boundary.
        history.extend(self.rehydration(
            memory_acess,
            system_prompt=system_prompt,
        ))

    def _trim_message_for_rehydration(
        self,
        message: Message,
        *,
        max_chars: int = 1200,
    ) -> Message:
        """Return a copy that is safe to reinsert into the live context.

        Raw tool outputs are often the largest items in the transcript. Reusing
        them verbatim during rehydration would defeat the purpose of compaction,
        so we keep the message structure but trim oversized payloads with an
        explicit marker.
        """

        trimmed = message.model_copy(deep=True)

        if trimmed.content and len(trimmed.content) > max_chars:
            omitted = len(trimmed.content) - max_chars
            trimmed.content = (
                f"{trimmed.content[:max_chars]}\n"
                f"...[{omitted} characters omitted during rehydration]..."
            )

        if trimmed.tool_calls:
            for tool_call in trimmed.tool_calls:
                if len(tool_call.function.arguments) > max_chars:
                    omitted = len(tool_call.function.arguments) - max_chars
                    tool_call.function.arguments = (
                        f"{tool_call.function.arguments[:max_chars]}"
                        f"...[{omitted} characters omitted during rehydration]..."
                    )

        return trimmed

    def rehydration(
        self,
        memory_acess: JsonlRandomAccess,
        *,
        system_prompt: Optional[str] = None,
        recent_tool_messages_before_boundary: int = 3,
        recent_normal_messages_before_boundary: int = 3
    ) -> list[Message]:
        """Reconstruct a compact but operational history from the transcript.

        After compaction or resume, the model should see:
        1. the system prompt,
        2. the latest summary produced at the compaction boundary,
        3. a short tail of raw messages immediately before the boundary, alway guarantee that 
        there are at least 3 results of tool and at least 3 normal messages without tool use.

        This pattern restores actionable local detail such as recent tool calls
        and file reads without replaying the entire conversation.
        """

        offsets=memory_acess.get_offsets()
        lines=len(offsets)
        count_norm=0
        count_tool=0
        message_recent=[]

        for i in reversed(range(lines)):
            data=memory_acess.get_line(i)
            memory_message = Memory_Unit.model_validate(data)
            history_message=memory_message.to_message()
            if history_message is not None:
                # AC-9: `[permission]` audit entries are transcript-only and
                # MUST NOT be rehydrated into model-visible context.
                if is_audit_content(history_message.content):
                    continue
                message_recent.append(self._trim_message_for_rehydration(history_message))
                if history_message.role=="tool":
                    count_tool+=1
                if history_message.role=="user" or "assistant":
                    count_norm+=1
                if i<lines-10 or (count_tool>=recent_tool_messages_before_boundary
                                  and count_norm>=recent_normal_messages_before_boundary):
                    break
        return message_recent




        # units = memory_acess.load_memory_units()
        # if not units:
        #     return [Message(role="system", content=system_prompt)] if system_prompt else []

        # boundary_indexes = [
        #     index
        #     for index, unit in enumerate(units)
        #     if unit.type == "compact_boundary"
        # ]

        # if not boundary_indexes:
        #     messages = [
        #         unit.to_message()
        #         for unit in units
        #         if unit.type == "message" and unit.to_message() is not None
        #     ]
        #     restored = [message for message in messages if message is not None]
        #     if system_prompt:
        #         if restored and restored[0].role == "system":
        #             return restored
        #         return [Message(role="system", content=system_prompt), *restored]
        #     return restored

        # last_boundary_index = boundary_indexes[-1]
        # units_before_boundary = units[:last_boundary_index]
        # units_after_boundary = units[last_boundary_index + 1 :]

        # summary_unit = next(
        #     (
        #         unit
        #         for unit in units_after_boundary
        #         if unit.type == "summary" and unit.content
        #     ),
        #     None,
        # )

        # before_messages = [
        #     unit.to_message()
        #     for unit in units_before_boundary
        #     if unit.type == "message"
        #     and unit.role != "system"
        #     and unit.to_message() is not None
        # ]
        # after_messages = [
        #     unit.to_message()
        #     for unit in units_after_boundary
        #     if unit.type == "message" and unit.to_message() is not None
        # ]

        # restored_history: list[Message] = []
        # if system_prompt:
        #     restored_history.append(Message(role="system", content=system_prompt))

        # if summary_unit and summary_unit.content:
        #     restored_history.append(
        #         Message(
        #             role="assistant",
        #             content=(
        #                 "以下是自动压缩后的会话摘要，请基于它继续工作：\n"
        #                 f"{summary_unit.content}"
        #             ),
        #         )
        #     )

        # if before_messages:
        #     restored_history.append(
        #         Message(
        #             role="assistant",
        #             content=(
        #                 "以下为压缩边界之前保留的最近原始上下文，"
        #                 "用于恢复工具调用、文件读取和待办状态："
        #             ),
        #         )
        #     )
        #     restored_history.extend(
        #         self._trim_message_for_rehydration(message)
        #         for message in before_messages[-recent_messages_before_boundary:]
        #     )

        # if after_messages:
        #     restored_history.extend(
        #         self._trim_message_for_rehydration(message)
        #         for message in after_messages[-recent_messages_after_boundary:]
        #     )

        # return restored_history
