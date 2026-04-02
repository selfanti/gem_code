from src import cli


def test_cli_separates_reasoning_and_content_lines(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    def fake_print(renderable="", *, end="\n", **kwargs) -> None:
        text = getattr(renderable, "plain", renderable)
        events.append((text, end))

    monkeypatch.setattr(cli.console, "print", fake_print)
    cli._stream_phase = None

    cli.on_reasoning("thinking")
    cli.on_content("answer")

    assert events == [
        ("thinking", ""),
        ("", "\n"),
        ("answer", ""),
    ]


def test_cli_does_not_insert_extra_breaks_within_same_phase(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    def fake_print(renderable="", *, end="\n", **kwargs) -> None:
        text = getattr(renderable, "plain", renderable)
        events.append((text, end))

    monkeypatch.setattr(cli.console, "print", fake_print)
    cli._stream_phase = None

    cli.on_reasoning("a")
    cli.on_reasoning("b")
    cli.on_content("c")

    assert events == [
        ("a", ""),
        ("b", ""),
        ("", "\n"),
        ("c", ""),
    ]
