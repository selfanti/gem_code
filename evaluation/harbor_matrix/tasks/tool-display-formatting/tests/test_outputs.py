from tool_rendering import format_tool_result, format_tool_start


def test_format_tool_start_is_stable_and_multiline() -> None:
    rendered = format_tool_start(
        "bash",
        {
            "description": "run tests",
            "command": "pytest -q " + "x" * 120,
        },
    )
    assert rendered.startswith("[tool] bash")
    assert "\n  command:" in rendered
    assert "\n  description:" in rendered
    assert "..." in rendered


def test_format_tool_result_mentions_tool_name() -> None:
    rendered = format_tool_result("read_file", "line1\nline2")
    assert rendered.startswith("[result] read_file")
    assert "line1" in rendered
