from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.environment_type import EnvironmentType


class LocalWorkspaceEnvironment(BaseEnvironment):
    """Run Harbor tasks against a local copy of this repository.

    Harbor's built-in environments assume Docker or other remote runtimes.
    That is a good default for benchmark publication, but it is awkward for
    day-to-day agent development because:

    1. we want to evaluate the *current uncommitted checkout* of gem-code,
    2. task setup should be cheap enough to run often while iterating, and
    3. the sandbox used in this coding environment does not expose a writable
       Docker daemon by default.

    This environment keeps Harbor's agent/verifier contracts intact while
    executing commands directly on the host in an isolated temporary directory.
    The task still sees conventional Harbor paths such as `/workspace`,
    `/tests`, and `/logs/...`; we translate those virtual paths to local paths
    before executing commands.
    """

    _STATIC_DIR_NAMES = {
        ".git",
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "__pycache__",
        ".DS_Store",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._repo_root = Path(__file__).resolve().parents[1]
        self._runtime_root: Path | None = None

    @staticmethod
    def type() -> EnvironmentType:
        # Harbor does not currently expose a dedicated "local host" environment
        # enum. Returning `docker` keeps the metadata schema satisfied while the
        # actual runtime behavior is provided by this custom import-path class.
        return EnvironmentType.DOCKER

    @property
    def is_mounted(self) -> bool:
        # We map `/logs/...` directly onto Harbor's host-side trial directories,
        # so verifier outputs and agent artifacts are available immediately
        # without a follow-up download step.
        return True

    @property
    def supports_gpus(self) -> bool:
        return False

    @property
    def can_disable_internet(self) -> bool:
        # Host subprocesses inherit the outer machine's network policy, so this
        # local adapter cannot enforce Harbor's internet isolation contract.
        return False

    def _validate_definition(self):
        # The official Docker environment requires a Dockerfile or compose file.
        # Our local environment instead relies on a repository checkout plus an
        # optional `workspace_template/` directory inside each task.
        if not self.environment_dir.exists():
            raise FileNotFoundError(f"Task environment directory not found: {self.environment_dir}")

    @property
    def _path_map(self) -> dict[str, Path]:
        if self._runtime_root is None:
            raise RuntimeError("Environment has not been started yet")

        return {
            "/workspace": self._runtime_root / "workspace",
            "/tests": self._runtime_root / "tests",
            "/solution": self._runtime_root / "solution",
            "/installed-agent": self._runtime_root / "installed-agent",
            "/logs/agent": self.trial_paths.agent_dir.resolve(),
            "/logs/verifier": self.trial_paths.verifier_dir.resolve(),
            "/logs/artifacts": self.trial_paths.artifacts_dir.resolve(),
            "/logs": self._runtime_root / "logs",
        }

    def _translate_text(self, text: str) -> str:
        translated = text
        for virtual_path, real_path in sorted(
            self._path_map.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            translated = translated.replace(virtual_path, str(real_path))
        return translated

    def _translate_path(self, path: str) -> Path:
        translated = self._translate_text(path)
        return Path(translated)

    def _copy_repo_ignore(self, directory: str, names: list[str]) -> set[str]:
        """Trim obviously irrelevant directories from the copied checkout.

        Each trial gets its own repo copy so the evaluated agent can edit files
        freely without mutating the developer's working tree. Copying caches,
        VCS metadata, and previous evaluation outputs would only waste setup
        time and risk contaminating the task workspace.
        """

        ignored: set[str] = set()
        current_dir = Path(directory)

        for name in names:
            if name in self._STATIC_DIR_NAMES:
                ignored.add(name)

        if ".env" in names:
            # The benchmark runner injects only the specific API variables the
            # agent needs. Excluding the raw `.env` file avoids spreading local
            # secrets into every temporary task workspace.
            ignored.add(".env")

        if current_dir == self._repo_root:
            for name in {"jobs"}:
                if name in names:
                    ignored.add(name)

        if current_dir == self._repo_root / "evaluation":
            for name in {"jobs", "reports", "harbor_runtime"}:
                if name in names:
                    ignored.add(name)

        return ignored

    async def start(self, force_build: bool) -> None:
        del force_build

        runtime_root = Path(
            tempfile.mkdtemp(prefix=f"gem-harbor-{self.session_id.replace('/', '-')}-")
        )
        self._runtime_root = runtime_root

        for path in self._path_map.values():
            path.mkdir(parents=True, exist_ok=True)

        workspace_dir = self._path_map["/workspace"]
        shutil.copytree(
            self._repo_root,
            workspace_dir,
            dirs_exist_ok=True,
            ignore=self._copy_repo_ignore,
            symlinks=True,
            ignore_dangling_symlinks=True,
        )

        host_venv = self._repo_root / ".venv"
        copied_venv = workspace_dir / ".venv"
        if host_venv.exists() and not copied_venv.exists():
            # Reusing the already-provisioned virtual environment keeps local
            # Harbor runs fast and deterministic. We use a symlink rather than a
            # deep copy so each trial does not pay the cost of duplicating the
            # dependency tree.
            copied_venv.symlink_to(host_venv)

        task_template_dir = self.environment_dir.parent / "workspace_template"
        if task_template_dir.exists():
            shutil.copytree(
                task_template_dir,
                workspace_dir / "evaluation_fixture",
                dirs_exist_ok=True,
                symlinks=True,
                ignore_dangling_symlinks=True,
            )

    async def stop(self, delete: bool):
        if delete and self._runtime_root and self._runtime_root.exists():
            shutil.rmtree(self._runtime_root, ignore_errors=True)
        self._runtime_root = None

    async def upload_file(self, source_path: Path | str, target_path: str):
        target = self._translate_path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)

    async def upload_dir(self, source_dir: Path | str, target_dir: str):
        target = self._translate_path(target_dir)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source_dir, target)

    async def download_file(self, source_path: str, target_path: Path | str):
        source = self._translate_path(source_path)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    async def download_dir(self, source_dir: str, target_dir: Path | str):
        source = self._translate_path(source_dir)
        target = Path(target_dir)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
    ) -> ExecResult:
        translated_command = self._translate_text(command)
        translated_cwd = str(self._translate_path(cwd)) if cwd else None

        process_env = os.environ.copy()
        process_env.setdefault("HARBOR_WORKSPACE_PATH", str(self._path_map["/workspace"]))
        process_env.setdefault("HARBOR_TESTS_PATH", str(self._path_map["/tests"]))
        process_env.setdefault(
            "HARBOR_VERIFIER_LOGS_PATH",
            str(self._path_map["/logs/verifier"]),
        )
        process_env.setdefault("HARBOR_AGENT_LOGS_PATH", str(self._path_map["/logs/agent"]))
        if env:
            process_env.update(
                {
                    key: self._translate_text(value) if isinstance(value, str) else value
                    for key, value in env.items()
                }
            )

        process = await asyncio.create_subprocess_shell(
            translated_command,
            cwd=translated_cwd,
            env=process_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            return ExecResult(
                stdout=stdout_bytes.decode(errors="replace"),
                stderr=stderr_bytes.decode(errors="replace"),
                return_code=124,
            )

        return ExecResult(
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            return_code=process.returncode or 0,
        )
