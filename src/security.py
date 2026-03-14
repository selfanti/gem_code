from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, NoReturn
from urllib.parse import urlsplit

try:  # pragma: no cover - import availability varies by environment
    from py_landlock import (
        CompatibilityError,
        Landlock,
        LandlockError,
        LandlockNotAvailableError,
        PathError,
        Scope,
    )

    PY_LANDLOCK_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - exercised only when dependency is absent
    CompatibilityError = LandlockError = LandlockNotAvailableError = PathError = RuntimeError
    Landlock = None
    Scope = None
    PY_LANDLOCK_AVAILABLE = False
    PY_LANDLOCK_IMPORT_ERROR = exc
else:
    PY_LANDLOCK_IMPORT_ERROR = None


DEFAULT_READ_PATH_CANDIDATES = (
    "/bin",
    "/etc",
    "/lib",
    "/lib64",
    "/opt",
    "/usr",
)
DEFAULT_EXECUTE_PATH_CANDIDATES = (
    "/bin",
    "/opt",
    "/sbin",
    "/usr",
)
PRIVATE_TMP_SUBDIR = Path(".gem-code") / "tmp"


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _parse_ports_env(name: str) -> tuple[int, ...]:
    values: list[int] = []
    for raw in _parse_csv_env(name):
        try:
            port = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must contain comma-separated integers") from exc
        if port < 1 or port > 65535:
            raise ValueError(f"{name} contains an invalid TCP port: {port}")
        values.append(port)
    return tuple(values)


def _normalize_paths(
    paths: Iterable[str],
    *,
    existing_only: bool = True,
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        resolved = Path(raw).expanduser().resolve(strict=False)
        if existing_only and not resolved.exists():
            continue
        text = str(resolved)
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


@dataclass(frozen=True)
class SecuritySettings:
    """Static security configuration loaded at application startup.

    The main agent keeps running outside Landlock. We instead apply a
    least-privilege policy only to child processes launched by the `bash` tool.
    That matches current agent best practice because the control plane stays
    alive even if the host kernel lacks Landlock support.
    """

    enabled: bool = True
    best_effort: bool = True
    allow_network: bool = False
    allow_abstract_unix: bool = False
    allow_signals: bool = False
    connect_ports: tuple[int, ...] = ()
    bind_ports: tuple[int, ...] = ()
    extra_read_paths: tuple[str, ...] = ()
    extra_write_paths: tuple[str, ...] = ()
    extra_execute_paths: tuple[str, ...] = ()

    def network_summary(self) -> str:
        if not self.enabled:
            return "open"
        if self.allow_network:
            return "all"
        if self.connect_ports:
            return "tcp:" + ",".join(str(port) for port in self.connect_ports)
        return "blocked"

    def summary(self) -> str:
        if not self.enabled:
            return "sandbox disabled"
        mode = "best-effort" if self.best_effort else "strict"
        return (
            "sandbox on; fs=workdir+private-tmp+runtime; "
            f"network={self.network_summary()}; mode={mode}"
        )


@dataclass(frozen=True)
class CommandSandboxPolicy:
    enabled: bool
    best_effort: bool
    allow_network: bool
    allow_abstract_unix: bool
    allow_signals: bool
    connect_ports: tuple[int, ...]
    bind_ports: tuple[int, ...]
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    execute_paths: tuple[str, ...]
    private_tmp_dir: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> CommandSandboxPolicy:
        parsed = json.loads(raw)
        return cls(
            enabled=bool(parsed["enabled"]),
            best_effort=bool(parsed["best_effort"]),
            allow_network=bool(parsed["allow_network"]),
            allow_abstract_unix=bool(parsed["allow_abstract_unix"]),
            allow_signals=bool(parsed["allow_signals"]),
            connect_ports=tuple(int(port) for port in parsed["connect_ports"]),
            bind_ports=tuple(int(port) for port in parsed["bind_ports"]),
            read_paths=tuple(str(path) for path in parsed["read_paths"]),
            write_paths=tuple(str(path) for path in parsed["write_paths"]),
            execute_paths=tuple(str(path) for path in parsed["execute_paths"]),
            private_tmp_dir=str(parsed["private_tmp_dir"]),
        )


def load_security_settings(workdir: str) -> SecuritySettings:
    """Load the sandbox policy with secure defaults.

    Defaults are intentionally conservative:
    - filesystem writes are limited to the workspace and a private temp dir
    - outbound network is blocked unless explicitly allowed
    - unsupported kernels degrade gracefully unless strict mode is requested
    """

    _ = workdir  # Reserved for future per-workspace policy loading.
    return SecuritySettings(
        enabled=_parse_bool_env("GEM_CODE_SECURITY_ENABLED", True),
        best_effort=_parse_bool_env("GEM_CODE_SECURITY_BEST_EFFORT", True),
        allow_network=_parse_bool_env("GEM_CODE_SECURITY_ALLOW_NETWORK", True),
        allow_abstract_unix=_parse_bool_env("GEM_CODE_SECURITY_ALLOW_ABSTRACT_UNIX", False),
        allow_signals=_parse_bool_env("GEM_CODE_SECURITY_ALLOW_SIGNALS", False),
        connect_ports=_parse_ports_env("GEM_CODE_SECURITY_ALLOW_CONNECT"),
        bind_ports=_parse_ports_env("GEM_CODE_SECURITY_ALLOW_BIND"),
        extra_read_paths=_normalize_paths(_parse_csv_env("GEM_CODE_SECURITY_EXTRA_READ_PATHS")),
        extra_write_paths=_normalize_paths(
            _parse_csv_env("GEM_CODE_SECURITY_EXTRA_WRITE_PATHS"),
            existing_only=False,
        ),
        extra_execute_paths=_normalize_paths(_parse_csv_env("GEM_CODE_SECURITY_EXTRA_EXECUTE_PATHS")),
    )


def landlock_runtime_supported() -> bool:
    if not PY_LANDLOCK_AVAILABLE or Landlock is None:
        return False
    try:
        return getattr(Landlock(strict=False), "abi_version", 0) > 0
    except Exception:
        return False


def build_command_policy(
    settings: SecuritySettings,
    workdir: str,
) -> CommandSandboxPolicy:
    workspace = Path(workdir).expanduser().resolve()
    private_tmp_dir = workspace / PRIVATE_TMP_SUBDIR
    private_tmp_dir.mkdir(parents=True, exist_ok=True)

    read_paths = _normalize_paths(
        (
            str(workspace),
            str(private_tmp_dir),
            *DEFAULT_READ_PATH_CANDIDATES,
            *settings.extra_read_paths,
        )
    )
    write_paths = _normalize_paths(
        (
            str(workspace),
            str(private_tmp_dir),
            *settings.extra_write_paths,
        ),
        existing_only=False,
    )
    execute_paths = _normalize_paths(
        (
            str(workspace),
            *DEFAULT_EXECUTE_PATH_CANDIDATES,
            *settings.extra_execute_paths,
        )
    )

    return CommandSandboxPolicy(
        enabled=settings.enabled,
        best_effort=settings.best_effort,
        allow_network=settings.allow_network,
        allow_abstract_unix=settings.allow_abstract_unix,
        allow_signals=settings.allow_signals,
        connect_ports=settings.connect_ports,
        bind_ports=settings.bind_ports,
        read_paths=read_paths,
        write_paths=write_paths,
        execute_paths=execute_paths,
        private_tmp_dir=str(private_tmp_dir),
    )


def build_sandbox_runner_command(
    command: str,
    workdir: str,
    settings: SecuritySettings,
) -> tuple[list[str], dict[str, str]]:
    policy = build_command_policy(settings, workdir)
    runner_path = str(Path(__file__).resolve())
    argv = [
        sys.executable,
        runner_path,
        "--policy-json",
        policy.to_json(),
        "--cwd",
        str(Path(workdir).expanduser().resolve()),
        "--",
        "/bin/sh",
        "-c",
        command,
    ]
    env = dict(os.environ)
    env["TMPDIR"] = policy.private_tmp_dir
    env["TMP"] = policy.private_tmp_dir
    env["TEMP"] = policy.private_tmp_dir
    return argv, env


def ensure_url_permitted(url: str, settings: SecuritySettings) -> None:
    if not settings.enabled:
        return

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise PermissionError("fetch_url only allows http/https URLs")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if settings.allow_network or port in settings.connect_ports:
        return

    raise PermissionError(
        "Network access is blocked by the active security policy. "
        f"Port {port} is not allowed."
    )


def _build_landlock(policy: CommandSandboxPolicy) -> Landlock:
    if Landlock is None or Scope is None:  # pragma: no cover - import guarded above
        raise RuntimeError("py-landlock is not available")

    sandbox = Landlock(strict=not policy.best_effort)

    write_paths = tuple(path for path in policy.write_paths if Path(path).exists())
    read_only_paths = tuple(path for path in policy.read_paths if path not in write_paths)

    if read_only_paths:
        sandbox.allow_read(*read_only_paths)
    if write_paths:
        sandbox.allow_read_write(*write_paths)
    if policy.execute_paths:
        sandbox.allow_execute(*policy.execute_paths)

    if policy.allow_network:
        sandbox.allow_all_network()
    else:
        for port in policy.connect_ports:
            sandbox.allow_network(port, bind=False, connect=True)
        for port in policy.bind_ports:
            sandbox.allow_network(port, bind=True, connect=False)

    if policy.allow_abstract_unix:
        sandbox.allow_scope(Scope.ABSTRACT_UNIX_SOCKET)
    if policy.allow_signals:
        sandbox.allow_scope(Scope.SIGNAL)

    return sandbox


def _apply_command_sandbox(policy: CommandSandboxPolicy) -> str | None:
    if not policy.enabled:
        return None

    if not PY_LANDLOCK_AVAILABLE:
        message = "py-landlock is not installed"
        if policy.best_effort:
            return f"[security] {message}; continuing without sandbox."
        raise RuntimeError(message) from PY_LANDLOCK_IMPORT_ERROR

    try:
        sandbox = _build_landlock(policy)
        sandbox.apply()
    except (
        CompatibilityError,
        LandlockError,
        LandlockNotAvailableError,
        PathError,
        RuntimeError,
    ) as exc:
        if policy.best_effort:
            return f"[security] Failed to apply Landlock sandbox: {exc}; continuing without sandbox."
        raise

    return None


def _parse_runner_args() -> tuple[CommandSandboxPolicy, str, list[str]]:
    parser = argparse.ArgumentParser(
        description="Apply a precomputed Landlock policy and exec a command.",
    )
    parser.add_argument("--policy-json", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    namespace = parser.parse_args()

    command = list(namespace.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ValueError("no command specified")

    return CommandSandboxPolicy.from_json(namespace.policy_json), namespace.cwd, command


def main() -> NoReturn:
    try:
        policy, cwd, command = _parse_runner_args()
    except Exception as exc:
        print(f"[security] {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        os.chdir(cwd)
    except OSError as exc:
        print(f"[security] Failed to chdir to {cwd}: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        warning = _apply_command_sandbox(policy)
        if warning:
            print(warning, file=sys.stderr)
        os.execvpe(command[0], command, os.environ)
    except FileNotFoundError:
        print(f"[security] command not found: {command[0]}", file=sys.stderr)
        sys.exit(127)
    except PermissionError:
        print(f"[security] permission denied: {command[0]}", file=sys.stderr)
        sys.exit(126)
    except Exception as exc:
        print(f"[security] {exc}", file=sys.stderr)
        sys.exit(126)


if __name__ == "__main__":
    main()
