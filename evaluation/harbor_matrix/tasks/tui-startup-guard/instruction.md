Fix `_should_launch_tui()` in `/workspace/evaluation_fixture/src/main.py`.

The function should only allow the TUI to start when both stdin and
stdout are attached to an interactive terminal.
