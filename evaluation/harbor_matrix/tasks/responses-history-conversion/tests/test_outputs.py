from responses_adapter import FunctionCall, Message, ToolCall, history_to_responses_input


def test_history_to_responses_input_preserves_tool_structure() -> None:
    items = history_to_responses_input(
        [
            Message(role="system", content="system"),
            Message(role="user", content="list files"),
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        function=FunctionCall(name="bash", arguments='{"command":"ls"}'),
                    )
                ],
            ),
            Message(role="tool", content="README.md", tool_call_id="call-1"),
        ]
    )

    assert items[0]["type"] == "message"
    assert any(item["type"] == "function_call" for item in items)
    assert any(item["type"] == "function_call_output" for item in items)
