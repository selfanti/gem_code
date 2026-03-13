Beautify the tool display helpers in `/workspace/evaluation_fixture/src/tool_rendering.py`.

Requirements:
- `format_tool_start()` should produce a compact multi-line layout.
- Arguments should be rendered in a stable order so snapshots stay deterministic.
- Very long values should be truncated with an ellipsis.
- `format_tool_result()` should include the tool name and a short body preview.
