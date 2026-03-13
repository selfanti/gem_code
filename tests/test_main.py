from main import _should_launch_tui


def test_should_launch_tui_requires_interactive_stdin_and_stdout() -> None:
    """The full-screen TUI should only run on a real interactive terminal."""

    assert _should_launch_tui(stdin_is_tty=True, stdout_is_tty=True) is True
    assert _should_launch_tui(stdin_is_tty=False, stdout_is_tty=True) is False
    assert _should_launch_tui(stdin_is_tty=True, stdout_is_tty=False) is False
