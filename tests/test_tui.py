import asyncio

from src.config import load_config
from src.tui import GemCodeApp


def test_tui_smoke_startup() -> None:
    """Ensure the TUI can mount without crashing.

    This is a regression test for the class of bugs where the TUI appeared to
    "not start" because startup exceptions were only visible inside Textual's
    alternate screen. Running the app headlessly through `run_test()` gives us
    a stable way to exercise the real startup path in CI.
    """

    async def run() -> None:
        app = GemCodeApp(load_config())
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            assert app.session_manager is not None

    asyncio.run(run())
