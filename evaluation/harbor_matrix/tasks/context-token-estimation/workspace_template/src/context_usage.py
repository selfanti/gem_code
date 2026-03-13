import math
import re
from dataclasses import dataclass


@dataclass
class Message:
    role: str
    content: str
    tool_call_id: str | None = None


def estimate_text_tokens(text: str) -> int:
    """Very naive baseline that currently overestimates everything."""
    return len(text)


def estimate_message_tokens(message: Message) -> int:
    return estimate_text_tokens(message.role) + estimate_text_tokens(message.content)
