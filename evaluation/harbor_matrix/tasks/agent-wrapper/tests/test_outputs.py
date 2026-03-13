import asyncio

from agent import Agent


def test_agent_wrapper_forwards_to_session_manager() -> None:
    async def run() -> None:
        agent = Agent(config={"name": "demo"})
        await agent.init()
        await agent.run("inspect README")
        assert agent.session.initialized is True
        assert agent.session.session.commands == ["inspect README"]

    asyncio.run(run())
