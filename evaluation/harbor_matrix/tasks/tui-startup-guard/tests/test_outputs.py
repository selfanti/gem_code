from main import _should_launch_tui


def test_tui_requires_interactive_stdin_and_stdout() -> None:
    assert _should_launch_tui(stdin_is_tty=True, stdout_is_tty=True) is True
    assert _should_launch_tui(stdin_is_tty=False, stdout_is_tty=True) is False
    assert _should_launch_tui(stdin_is_tty=True, stdout_is_tty=False) is False
