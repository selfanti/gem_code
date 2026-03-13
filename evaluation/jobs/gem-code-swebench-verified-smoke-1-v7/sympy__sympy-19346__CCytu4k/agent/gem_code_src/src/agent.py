from __future__ import annotations

from .config import Config
from .session_manager import SessionManager


class Agent:
    """Minimal agent wrapper reserved for future team orchestration.

    The original file contained empty method bodies and broke `compileall`.
    Keeping a small, valid wrapper is better than shipping syntax errors while
    the multi-agent design is still under active development.
    """

    def __init__(self, config: Config):
        self.session = SessionManager(config)

    async def init(self) -> None:
        await self.session.init()

    async def run(self, instruction: str) -> None:
        await self.session.session.chat(instruction)
