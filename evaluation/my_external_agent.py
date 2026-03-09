from harbor.agents.base import BaseAgent,BaseEnvironment,AgentContext

class MyExternalAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        """The name of the agent."""
        pass

    def version(self) -> str | None:
        """The version of the agent."""
        pass

    async def setup(self, environment: BaseEnvironment) -> None:
        """
        Run commands to setup the agent & its tools.
        """
        pass

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """
        Runs the agent in the environment. Be sure to populate the context with the
        results of the agent execution. Ideally, populate the context as the agent
        executes in case of a timeout or other error.

        Args:
            instruction: The task instruction.
            environment: The environment in which to complete the task.
            context: The context to populate with the results of the agent execution.
        """
        pass