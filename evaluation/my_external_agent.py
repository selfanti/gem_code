from __future__ import annotations

from pathlib import Path

from harbor.agents.installed.base import AgentContext, BaseInstalledAgent, ExecInput


class MyInstalledAgent(BaseInstalledAgent):
    """Minimal Harbor adapter for running Gem Code inside an evaluation image.

    Harbor's installed-agent flow expects three things:
    1. an installation script template,
    2. one or more commands that run the agent non-interactively,
    3. post-run context extraction.

    This adapter intentionally keeps the contract small and explicit so the
    project can start writing Harbor-based smoke tests before the richer
    trajectory format is designed.
    """

    @property
    def _install_agent_template_path(self) -> Path:
        return Path(__file__).with_name("install_gem_code.sh.j2")

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        # Harbor executes shell command strings inside the evaluation
        # environment. We quote the instruction defensively and use the CLI's
        # `--once` mode so the command terminates after a single response.
        quoted_instruction = instruction.replace("\\", "\\\\").replace('"', '\\"')
        return [
            ExecInput(
                command=(
                    'cd /workspace && uv run python main.py --cli --once '
                    f'"{quoted_instruction}"'
                ),
                cwd="/workspace",
                timeout_sec=600,
            )
        ]

    def populate_context_post_run(self, context: AgentContext) -> None:
        command_dir = self.logs_dir / "command-0"
        stdout_path = command_dir / "stdout.txt"
        stderr_path = command_dir / "stderr.txt"
        return_code_path = command_dir / "return-code.txt"

        metadata = dict(context.metadata or {})
        metadata["stdout"] = stdout_path.read_text() if stdout_path.exists() else ""
        metadata["stderr"] = stderr_path.read_text() if stderr_path.exists() else ""
        metadata["return_code"] = (
            int(return_code_path.read_text()) if return_code_path.exists() else None
        )
        context.metadata = metadata
