from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path
from typing import Any

from harbor.agents.installed.base import AgentContext, BaseInstalledAgent, ExecInput
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from harbor.models.trial.paths import EnvironmentPaths
from harbor.utils.trajectory_utils import format_trajectory_json
from jinja2 import Environment


class GemCodeInstalledAgent(BaseInstalledAgent):
    """Harbor installed-agent adapter for the current gem-code checkout.

    The official Harbor adapters, especially `codex.py`, do three important
    things beyond simply "run a command":

    1. they inject agent-specific environment variables explicitly,
    2. they persist a machine-readable execution artifact for later parsing, and
    3. they convert the agent's native session format into Harbor's ATIF
       trajectory schema so benchmark results remain inspectable.

    This adapter follows the same pattern for gem-code.
    """

    SUPPORTS_ATIF: bool = True

    def __init__(self, agent_workdir: str = "/workspace", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._agent_workdir = agent_workdir

    @staticmethod
    def name() -> str:
        return "gem-code"

    @property
    def _install_agent_template_path(self) -> Path:
        return Path(__file__).with_name("install_gem_code.sh.j2")

    @property
    def _run_metadata_path(self) -> Path:
        return self.logs_dir / "gem_code_run.json"

    @property
    def _trajectory_output_path(self) -> Path:
        return self.logs_dir / "trajectory.json"

    @property
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _stage_source_bundle(self) -> Path:
        """Create a small uploaded source tree for Harbor environments.

        Uploading the entire repository made sense for the local smoke runner
        but is wasteful for Docker-backed benchmarks such as SWE-bench
        Verified. The full checkout contains cached job outputs, large fixture
        files, and development-only directories that significantly slow down
        Harbor's `upload_dir()` step and pushed agent setup past the default
        timeout.

        A minimal allowlist is safer here than a growing ignore list: the agent
        only needs the executable entrypoint, runtime source, a tiny evaluation
        helper, dependency metadata, and optional local skills.
        """

        staged_root = self.logs_dir / "gem_code_src"
        if staged_root.exists():
            shutil.rmtree(staged_root)

        staged_root.mkdir(parents=True, exist_ok=True)

        # Deliberately exclude the repo-local `mcp_config.json`. That file is a
        # workstation runtime override rather than application code, and
        # bundling it into Harbor would make benchmark behavior depend on the
        # evaluator's local tool setup instead of the task environment itself.
        required_files = [
            "main.py",
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "AGENTS.md",
            ".env_example",
            "mcp_config.example.json",
        ]
        required_dirs = [
            "src",
            ".agents",
        ]

        for relative_path in required_files:
            source = self._repo_root / relative_path
            if not source.exists():
                continue
            destination = staged_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        for relative_path in required_dirs:
            source = self._repo_root / relative_path
            if not source.exists():
                continue
            shutil.copytree(
                source,
                staged_root / relative_path,
                symlinks=True,
                ignore_dangling_symlinks=True,
                dirs_exist_ok=True,
            )

        evaluation_dir = staged_root / "evaluation"
        evaluation_dir.mkdir(parents=True, exist_ok=True)
        for relative_path in [
            "evaluation/__init__.py",
            "evaluation/run_gem_code_once.py",
        ]:
            source = self._repo_root / relative_path
            destination = staged_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        return staged_root

    async def setup(self, environment) -> None:
        await environment.exec(command="mkdir -p /installed-agent")

        staged_source_dir = self._stage_source_bundle()
        await environment.upload_dir(
            source_dir=staged_source_dir,
            target_dir="/installed-agent/gem_code_src",
        )

        if not self._install_agent_template_path.exists():
            raise FileNotFoundError(
                f"Install agent template file not found: {self._install_agent_template_path}"
            )

        env = Environment()
        template = env.from_string(self._install_agent_template_path.read_text())
        rendered_script = template.render(**self._template_variables)

        script_path = self.logs_dir / "install.sh"
        script_path.write_text(rendered_script)

        await environment.upload_file(
            source_path=script_path,
            target_path="/installed-agent/install.sh",
        )

        result = await environment.exec(
            command="bash /installed-agent/install.sh",
            env=self._setup_env(),
        )

        setup_dir = self.logs_dir / "setup"
        setup_dir.mkdir(parents=True, exist_ok=True)
        (setup_dir / "return-code.txt").write_text(str(result.return_code))

        if result.stdout:
            (setup_dir / "stdout.txt").write_text(result.stdout)

        if result.stderr:
            (setup_dir / "stderr.txt").write_text(result.stderr)

        if result.return_code != 0:
            raise RuntimeError(
                f"Agent setup failed with exit code {result.return_code}. "
                f"See logs in {setup_dir}"
            )

    def _find_latest_transcript(self) -> Path | None:
        memory_root = self.logs_dir / "memory"
        candidates = [
            path
            for path in memory_root.rglob("*.jsonl")
            if not path.name.endswith("_index.jsonl")
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _message_source(role: str) -> str:
        if role == "assistant":
            return "agent"
        return role

    def _convert_transcript_to_trajectory(
        self,
        transcript_path: Path,
        run_metadata: dict[str, Any] | None,
    ) -> Trajectory | None:
        from src.memory import Memory_Unit

        units: list[Memory_Unit] = []
        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                units.append(Memory_Unit.model_validate_json(line))
            except Exception:
                self.logger.warning("Skipping malformed transcript line in %s", transcript_path)

        if not units:
            return None

        steps: list[Step] = []
        pending_tool_steps: dict[str, Step] = {}

        for unit in units:
            if unit.type == "compact_boundary":
                continue

            if unit.type == "summary":
                steps.append(
                    Step(
                        step_id=len(steps) + 1,
                        timestamp=unit.timestamp.isoformat(),
                        source="system",
                        message=f"[Compaction summary]\n{unit.content or ''}",
                        extra={"memory_unit_type": "summary"},
                    )
                )
                continue

            if unit.type != "message" or unit.role is None:
                continue

            if unit.role == "tool":
                matching_step = pending_tool_steps.get(unit.tool_call_id or "")
                if matching_step is None:
                    steps.append(
                        Step(
                            step_id=len(steps) + 1,
                            timestamp=unit.timestamp.isoformat(),
                            source="system",
                            message=f"[Detached tool output]\n{unit.content or ''}",
                            extra={
                                "memory_unit_type": "tool_output",
                                "tool_call_id": unit.tool_call_id,
                            },
                        )
                    )
                    continue

                if matching_step.observation is None:
                    matching_step.observation = Observation(results=[])

                matching_step.observation.results.append(
                    ObservationResult(
                        source_call_id=unit.tool_call_id,
                        content=unit.content or "",
                    )
                )
                continue

            tool_calls = None
            if unit.role == "assistant" and unit.tool_calls:
                tool_calls = [
                    ToolCall(
                        tool_call_id=tool_call.id,
                        function_name=tool_call.function.name,
                        arguments=json.loads(tool_call.function.arguments)
                        if tool_call.function.arguments
                        else {},
                    )
                    for tool_call in unit.tool_calls
                ]

            step = Step(
                step_id=len(steps) + 1,
                timestamp=unit.timestamp.isoformat(),
                source=self._message_source(unit.role),
                message=unit.content or "",
                tool_calls=tool_calls,
                model_name=self.model_name if unit.role == "assistant" else None,
            )
            steps.append(step)

            if tool_calls:
                for tool_call in tool_calls:
                    pending_tool_steps[tool_call.tool_call_id] = step

        if not steps:
            return None

        final_metrics = None
        if run_metadata and isinstance(run_metadata.get("context_usage"), dict):
            usage = run_metadata["context_usage"]
            final_metrics = FinalMetrics(
                total_prompt_tokens=usage.get("estimated_input_tokens"),
                total_completion_tokens=usage.get("estimated_output_tokens"),
                total_steps=len(steps),
                extra={
                    "usage_source": usage.get("source"),
                    "displayed_total_tokens": usage.get("used_tokens"),
                    "max_tokens": usage.get("max_tokens"),
                },
            )

        return Trajectory(
            schema_version="ATIF-v1.5",
            session_id=str(run_metadata.get("session_id")) if run_metadata else transcript_path.stem,
            agent=Agent(
                name=self.name(),
                version=self.version() or "workspace",
                model_name=self.model_name,
                extra={
                    "transcript_path": str(transcript_path),
                    "metadata_path": str(self._run_metadata_path),
                },
            ),
            steps=steps,
            final_metrics=final_metrics,
        )

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        escaped_instruction = shlex.quote(instruction)
        env = {
            # SWE-bench mounts the checked-out task repository at `/testbed`.
            # The runner injects the correct value via `agent_workdir`, but we
            # still centralize the actual environment variable here so every
            # Harbor execution path uses the same contract that gem-code uses
            # outside Harbor: `WORKDIR` is the authoritative repo root.
            "WORKDIR": self._agent_workdir,
            # Harbor evaluations should be hermetic with respect to the host
            # machine. If we let gem-code fall back to `./mcp_config.json` or a
            # user-level config copied into the staged source tree, benchmark
            # runs can try to boot unrelated tools such as Playwright, fail due
            # to missing `npx`, and waste both context budget and wall-clock
            # time. The dedicated disable flag is the primary switch; the empty
            # config values are a second line of defense for any code paths
            # that still inspect the environment directly.
            "GEM_CODE_DISABLE_MCP": "1",
            "MCP_CONFIG": '{"mcpServers": {}}',
            "MCP_CONFIG_PATH": "",
            "SKILLS_DIR": "/installed-agent/gem_code_src/.agents",
            "MEMORY_COMPACTION_PATH": str(EnvironmentPaths.agent_dir / "memory"),
            # The agent process runs inside the Harbor environment, so it must
            # write metadata to the in-container mounted logs path rather than a
            # host-side absolute path from the orchestrator process. Harbor then
            # syncs `/logs/agent/...` back into `self.logs_dir`, which is why
            # `populate_context_post_run()` reads the host mirror afterwards.
            "HARBOR_GEM_CODE_RUN_METADATA_PATH": str(
                EnvironmentPaths.agent_dir / "gem_code_run.json"
            ),
            "PYTHONUNBUFFERED": "1",
        }

        if self.model_name:
            # Harbor's `--model` flag should influence both result metadata and
            # the actual evaluated agent, so we forward it into gem-code's
            # runtime configuration explicitly.
            env["OPENAI_MODEL"] = self.model_name

        return [
            ExecInput(
                command=(
                    'export PATH="$HOME/.local/bin:$PATH"; '
                    "uv run python evaluation/run_gem_code_once.py "
                    f"--instruction {escaped_instruction}"
                ),
                cwd="/installed-agent/gem_code_src",
                env=env,
                timeout_sec=900,
            )
        ]

    def _setup_env(self) -> dict[str, str]:
        env = super()._setup_env()
        env.update(
            {
                # The install script resolves its working directory through this
                # variable so custom Harbor environments can translate it to a
                # host path without editing the rendered shell script itself.
                "HARBOR_GEM_CODE_WORKSPACE": "/workspace",
                "HARBOR_GEM_CODE_ROOT": "/installed-agent/gem_code_src",
                "UV_CACHE_DIR": "/tmp/uv-cache",
            }
        )
        return env

    def populate_context_post_run(self, context: AgentContext) -> None:
        command_dir = self.logs_dir / "command-0"
        stdout_path = command_dir / "stdout.txt"
        stderr_path = command_dir / "stderr.txt"
        return_code_path = command_dir / "return-code.txt"

        run_metadata = self._load_json(self._run_metadata_path)
        transcript_path = self._find_latest_transcript()

        metadata = dict(context.metadata or {})
        metadata["stdout"] = stdout_path.read_text(encoding="utf-8") if stdout_path.exists() else ""
        metadata["stderr"] = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
        metadata["return_code"] = (
            int(return_code_path.read_text().strip()) if return_code_path.exists() else None
        )
        metadata["run_metadata_path"] = str(self._run_metadata_path) if self._run_metadata_path.exists() else None
        metadata["transcript_path"] = str(transcript_path) if transcript_path else None
        if run_metadata is not None:
            metadata["run_metadata"] = run_metadata

        if run_metadata and isinstance(run_metadata.get("context_usage"), dict):
            usage = run_metadata["context_usage"]
            context.n_input_tokens = usage.get("estimated_input_tokens")
            context.n_output_tokens = usage.get("estimated_output_tokens")
            context.n_cache_tokens = 0

        if transcript_path is not None:
            trajectory = self._convert_transcript_to_trajectory(
                transcript_path=transcript_path,
                run_metadata=run_metadata,
            )
            if trajectory is not None:
                self._trajectory_output_path.write_text(
                    format_trajectory_json(trajectory.to_json_dict()),
                    encoding="utf-8",
                )
                metadata["trajectory_path"] = str(self._trajectory_output_path)

        context.metadata = metadata


# Keep the original class name as a compatibility alias because the README and
# previous experiments already reference `evaluation.my_external_agent:MyInstalledAgent`.
MyInstalledAgent = GemCodeInstalledAgent
