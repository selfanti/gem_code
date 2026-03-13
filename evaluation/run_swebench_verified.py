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
    sys.path.insert(0, str(REPO_ROOT))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Harbor against the official swebench-verified dataset.",
    )
    parser.add_argument(
        "--job-name",
        default="gem-code-swebench-verified-smoke-10",
        help="Harbor job name to use for this evaluation run.",
    )
    parser.add_argument(
        "--dataset",
        default="swebench-verified@1.0",
        help="Harbor dataset spec in the form name@version.",
    )
    parser.add_argument(
        "--n-tasks",
        type=int,
        default=10,
        help="How many SWE-bench Verified tasks to run for the initial sample.",
    )
    parser.add_argument(
        "--n-concurrent",
        type=int,
        default=1,
        help="Concurrent Harbor trials. Defaults to 1 because each task is heavyweight.",
    )
    parser.add_argument(
        "--agent-workdir",
        default="/testbed",
        help=(
            "Repository root inside the benchmark environment. SWE-bench Docker "
            "tasks expose the checked-out target repo at /testbed."
        ),
    )
    parser.add_argument(
        "--task-name",
        action="append",
        default=None,
        help="Optional task-name filter. Can be passed multiple times.",
    )
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="Skip Harbor execution and only summarize an existing job directory.",
    )
    return parser


def _load_agent_env(repo_root: Path) -> dict[str, str]:
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


def _harbor_command(args, forwarded_env: dict[str, str]) -> list[str]:
    command = [
        "uv",
        "run",
        "harbor",
        "run",
        "--job-name",
        args.job_name,
        "--jobs-dir",
        str(REPO_ROOT / "evaluation" / "jobs"),
        "--agent-import-path",
        "evaluation.my_external_agent:GemCodeInstalledAgent",
        # Harbor's official docs show the generic `--agent-import-path` entry,
        # but custom installed agents can still accept constructor kwargs via
        # `--ak`. We use that extension point to align gem-code's `WORKDIR`
        # with the dataset's actual repo mount instead of baking `/workspace`
        # into the adapter and silently pointing the agent at the wrong tree.
        "--ak",
        f"agent_workdir={args.agent_workdir}",
        "--dataset",
        args.dataset,
        "--n-tasks",
        str(args.n_tasks),
        "--n-concurrent",
        str(args.n_concurrent),
        "--agent-setup-timeout-multiplier",
        "4",
        "--debug",
    ]

    if forwarded_env.get("OPENAI_MODEL"):
        command.extend(["--model", forwarded_env["OPENAI_MODEL"]])

    for task_name in args.task_name or []:
        command.extend(["--task-name", task_name])

    for key, value in forwarded_env.items():
        command.extend(["--ae", f"{key}={value}"])

    return command


def _collect_results(job_dir: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for result_path in sorted(job_dir.glob("*/result.json")):
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        verifier = payload.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        agent_metadata = (payload.get("agent_result") or {}).get("metadata") or {}
        run_metadata = agent_metadata.get("run_metadata") or {}
        exception = payload.get("exception_info") or {}

        results.append(
            {
                "task_name": payload["task_name"],
                "trial_name": payload["trial_name"],
                "reward": rewards.get("reward"),
                "return_code": agent_metadata.get("return_code"),
                "exception_type": exception.get("exception_type"),
                "exception_message": exception.get("exception_message"),
                "trajectory_path": agent_metadata.get("trajectory_path"),
                "tool_events": len(run_metadata.get("tool_events") or []),
                "assistant_output_preview": (run_metadata.get("assistant_output") or "")[:200],
            }
        )
    return results


def _write_report(job_name: str, dataset: str, results: list[dict[str, Any]]) -> tuple[Path, Path]:
    reports_dir = REPO_ROOT / "evaluation" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    passed = sum(1 for item in results if item.get("reward") == 1)
    failed = len(results) - passed

    json_path = reports_dir / f"{job_name}.json"
    md_path = reports_dir / f"{job_name}.md"

    payload = {
        "job_name": job_name,
        "dataset": dataset,
        "total_tasks": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# SWE-bench Verified Report: {job_name}",
        "",
        f"- Dataset: `{dataset}`",
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
    forwarded_env = _load_agent_env(REPO_ROOT)
    job_dir = REPO_ROOT / "evaluation" / "jobs" / args.job_name

    if not args.skip_run:
        command = _harbor_command(args, forwarded_env)
        run_env = os.environ.copy()
        run_env["UV_CACHE_DIR"] = "/tmp/uv-cache"
        subprocess.run(command, cwd=REPO_ROOT, env=run_env, check=True)

    if not job_dir.exists():
        raise FileNotFoundError(f"Harbor job directory not found: {job_dir}")

    results = _collect_results(job_dir)
    md_path, json_path = _write_report(args.job_name, args.dataset, results)
    print(md_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
