def _should_launch_tui(*, stdin_is_tty: bool, stdout_is_tty: bool) -> bool:
    """Return whether the environment can support a full-screen TUI.

    This buggy baseline lets the UI start even when only one side
    of the terminal is interactive, which mirrors the classic
    "TUI flashes and exits" failure mode.
    """

    return stdin_is_tty or stdout_is_tty
