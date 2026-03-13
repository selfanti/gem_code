from dataclasses import dataclass, field


@dataclass
class FunctionCall:
    name: str
    arguments: str


@dataclass
class ToolCall:
    id: str
    function: FunctionCall
    type: str = "function"


@dataclass
class Message:
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = field(default=None)
    tool_call_id: str | None = None


def history_to_responses_input(messages: list[Message]) -> list[dict]:
    """Convert local history into Responses API items.

    The current version drops tool call structure, which makes it
    impossible to replay multi-step conversations after resume.
    """

    items: list[dict] = []
    for message in messages:
        items.append(
            {
                "type": "message",
                "role": message.role,
                "content": message.content or "",
            }
        )
    return items
