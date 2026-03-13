from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    # Allow `python evaluation/run_harbor_matrix.py` to work without requiring
    # callers to set `PYTHONPATH` manually.
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.build_harbor_matrix import TASKS, build_matrix


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local Harbor smoke matrix against gem-code.",
    )
    parser.add_argument(
        "--job-name",
        default="gem-code-smoke-10",
        help="Harbor job name to use for this run.",
    )
    parser.add_argument(
        "--n-concurrent",
        type=int,
        default=1,
        help="Number of concurrent Harbor trials. Defaults to 1 to avoid sharing one venv across multiple mutable repo copies.",
    )
    parser.add_argument(
        "--tasks-path",
        default=None,
        help="Optional path to an existing Harbor task dataset directory.",
    )
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="Only regenerate the task matrix and summarize the latest job directory.",
    )
    parser.add_argument(
        "--task-name",
        action="append",
        default=None,
        help="Optional Harbor task-name filter. Can be passed multiple times for targeted dry runs.",
    )
    return parser


def _load_agent_env(repo_root: Path) -> dict[str, str]:
    """Collect only the variables required by the evaluated agent.

    We intentionally do not forward the whole host environment into Harbor
    trials. Restricting the forwarded set makes runs easier to reproduce and
    avoids accidentally coupling task outcomes to unrelated local variables.
    """

    env = dict(os.environ)
    dotenv_path = repo_root / ".env"
    if dotenv_path.exists():
        for key, value in dotenv_values(dotenv_path).items():
            if value is not None:
                env.setdefault(key, value)

    required = ["OPENAI_API_KEY", "OPENAI_BASE_URL"]
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise RuntimeError(f"Missing required API configuration: {', '.join(missing)}")

    forwarded = {
        "OPENAI_API_KEY": env["OPENAI_API_KEY"],
        "OPENAI_BASE_URL": env["OPENAI_BASE_URL"],
    }
    if env.get("OPENAI_MODEL"):
        forwarded["OPENAI_MODEL"] = env["OPENAI_MODEL"]
    if env.get("OPENAI_API_MODE"):
        forwarded["OPENAI_API_MODE"] = env["OPENAI_API_MODE"]

    return forwarded


def _harbor_command(
    repo_root: Path,
    tasks_path: Path,
    job_name: str,
    n_concurrent: int,
    forwarded_env: dict[str, str],
    task_names: list[str] | None,
) -> list[str]:
    command = [
        "uv",
        "run",
        "harbor",
        "run",
        "--job-name",
        job_name,
        "--jobs-dir",
        str(repo_root / "evaluation" / "jobs"),
        "--agent-import-path",
        "evaluation.my_external_agent:GemCodeInstalledAgent",
        "--ak",
        "agent_workdir=/workspace/evaluation_fixture",
        "--environment-import-path",
        "evaluation.local_harbor_environment:LocalWorkspaceEnvironment",
        "--path",
        str(tasks_path),
        "--n-concurrent",
        str(n_concurrent),
        "--debug",
    ]

    selected_task_count = len(task_names) if task_names else len(TASKS)
    command.extend(["--n-tasks", str(selected_task_count)])

    if forwarded_env.get("OPENAI_MODEL"):
        command.extend(["--model", forwarded_env["OPENAI_MODEL"]])

    for task_name in task_names or []:
        command.extend(["--task-name", task_name])

    for key, value in forwarded_env.items():
        command.extend(["--ae", f"{key}={value}"])

    return command


def _collect_results(job_dir: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for result_path in sorted(job_dir.glob("*/result.json")):
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        trial_name = payload["trial_name"]
        reward = None
        verifier = payload.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        if "reward" in rewards:
            reward = rewards["reward"]

        exception = payload.get("exception_info") or {}
        agent_metadata = (payload.get("agent_result") or {}).get("metadata") or {}
        run_metadata = agent_metadata.get("run_metadata") or {}

        results.append(
            {
                "trial_name": trial_name,
                "task_name": payload["task_name"],
                "reward": reward,
                "exception_type": exception.get("exception_type"),
                "exception_message": exception.get("exception_message"),
                "return_code": agent_metadata.get("return_code"),
                "trajectory_path": agent_metadata.get("trajectory_path"),
                "tool_events": len(run_metadata.get("tool_events") or []),
                "assistant_output_preview": (run_metadata.get("assistant_output") or "")[:200],
            }
        )
    return results


def _write_report(repo_root: Path, job_name: str, results: list[dict[str, Any]]) -> tuple[Path, Path]:
    reports_dir = repo_root / "evaluation" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    passed = sum(1 for item in results if item.get("reward") == 1)
    failed = len(results) - passed

    json_path = reports_dir / f"{job_name}.json"
    md_path = reports_dir / f"{job_name}.md"

    json_payload = {
        "job_name": job_name,
        "total_tasks": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }
    json_path.write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        f"# Harbor Smoke Matrix Report: {job_name}",
        "",
        f"- Total tasks: {len(results)}",
        f"- Passed: {passed}",
        f"- Failed: {failed}",
        "",
        "| Task | Reward | Return Code | Tool Events | Exception |",
        "| --- | --- | --- | --- | --- |",
    ]

    for item in results:
        lines.append(
            "| {task} | {reward} | {return_code} | {tool_events} | {exception} |".format(
                task=item["task_name"],
                reward=item["reward"],
                return_code=item["return_code"],
                tool_events=item["tool_events"],
                exception=item["exception_type"] or "",
            )
        )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path


def main() -> int:
    args = _build_parser().parse_args()
    repo_root = REPO_ROOT
    matrix_root = build_matrix()
    tasks_path = Path(args.tasks_path) if args.tasks_path else matrix_root / "tasks"

    forwarded_env = _load_agent_env(repo_root)
    job_dir = repo_root / "evaluation" / "jobs" / args.job_name

    if not args.skip_run:
        command = _harbor_command(
            repo_root=repo_root,
            tasks_path=tasks_path,
            job_name=args.job_name,
            n_concurrent=args.n_concurrent,
            forwarded_env=forwarded_env,
            task_names=args.task_name,
        )

        run_env = os.environ.copy()
        run_env["UV_CACHE_DIR"] = "/tmp/uv-cache"
        subprocess.run(command, cwd=repo_root, env=run_env, check=True)

    if not job_dir.exists():
        raise FileNotFoundError(f"Harbor job directory not found: {job_dir}")

    results = _collect_results(job_dir)
    md_path, json_path = _write_report(repo_root, args.job_name, results)

    print(md_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
