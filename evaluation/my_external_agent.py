from harbor.agents.installed.base import BaseInstalledAgent,BaseModel,AgentContext

class ExecInput(BaseModel):
    command: str
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout_sec: int | None = None

class MyInstalledAgent(BaseInstalledAgent):
    @property
    def _install_agent_template_path(self) -> Path:
        """
        Path to the jinja template script for installing the agent in the container.
        """
        pass

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        """
        Create the commands to run the agent in the container. Usually this is a single
        command that passes the instruction to the agent and executes it in headless
        mode.
        """
        pass

    def populate_context_post_run(self, context: AgentContext) -> None:
        """
        Populate the context with the results of the agent execution. Assumes the run()
        method has already been called. Typically involves parsing a trajectory file.
        """
        pass