class FakeSession:
    def __init__(self):
        self.commands: list[str] = []

    async def chat(self, instruction: str) -> None:
        self.commands.append(instruction)


class SessionManager:
    def __init__(self, config):
        self.config = config
        self.session = FakeSession()
        self.initialized = False

    async def init(self) -> None:
        self.initialized = True


class Agent:
    """Minimal wrapper reserved for future orchestration."""

    def __init__(self, config):
        self.session = SessionManager(config)

    async def init(self) -> None:
        pass

    async def run(self, instruction: str) -> None:
        return None
