def format_tool_start(tool_name: str, args: dict) -> str:
    """Return a string for the "tool started" event.

    The current output is technically readable but too noisy for
    a TUI because dict ordering and long values make the line
    jitter between renders.
    """

    return f"{tool_name}: {args}"


def format_tool_result(tool_name: str, result: str) -> str:
    return result
