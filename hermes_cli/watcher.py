"""LaunchAgent management for the Hermes JSON-to-Markdown watcher."""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from hermes_cli.config import get_hermes_home


PROJECT_ROOT = Path(__file__).parent.parent.resolve()
WATCHER_LABEL = "com.ryangherardi.hermes-watcher"
PLIST_FILENAME = f"{WATCHER_LABEL}.plist"


@dataclass(frozen=True)
class LaunchdState:
    loaded: bool
    pid: int | None = None
    last_exit_status: int | None = None
    raw: str = ""


def _user_home() -> Path:
    return Path.home()


def _launch_agents_dir() -> Path:
    return _user_home() / "Library" / "LaunchAgents"


def installed_plist_path() -> Path:
    return _launch_agents_dir() / PLIST_FILENAME


def source_plist_path() -> Path:
    return PROJECT_ROOT / "launchd" / PLIST_FILENAME


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _watcher_script_path() -> Path:
    return PROJECT_ROOT / "watcher" / "run_json_to_md.sh"


def watcher_log_paths() -> dict[str, Path]:
    hermes_home = Path(get_hermes_home()).expanduser()
    return {
        "watcher": hermes_home / "watcher.log",
        "stdout": hermes_home / "watcher.launchd.out.log",
        "stderr": hermes_home / "watcher.launchd.err.log",
    }


def watcher_data_paths() -> dict[str, Path]:
    hermes_home = Path(get_hermes_home()).expanduser()
    return {
        "sessions": hermes_home / "sessions",
        "processed": hermes_home / "sessions" / ".processed.json",
        "vault": _user_home() / "Projects" / "Workspace-Monorepo" / "vault",
    }


def build_plist() -> dict[str, Any]:
    logs = watcher_log_paths()
    Path(get_hermes_home()).expanduser().mkdir(parents=True, exist_ok=True)
    return {
        "Label": WATCHER_LABEL,
        "ProgramArguments": [str(_watcher_script_path())],
        "WorkingDirectory": str(PROJECT_ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(logs["stdout"]),
        "StandardErrorPath": str(logs["stderr"]),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "HERMES_HOME": str(Path(get_hermes_home()).expanduser()),
        },
    }


def write_plist(path: Path, plist: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(plist or build_plist(), handle, sort_keys=False)


def validate_plist_file(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"plist not found: {path}"
    try:
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
    except Exception as exc:
        return False, f"plist parse failed: {exc}"

    problems: list[str] = []
    if plist.get("Label") != WATCHER_LABEL:
        problems.append(f"Label must be {WATCHER_LABEL}")
    args = plist.get("ProgramArguments")
    if not isinstance(args, list) or not args:
        problems.append("ProgramArguments must contain the watcher wrapper")
    elif not Path(str(args[0])).exists():
        problems.append(f"watcher wrapper does not exist: {args[0]}")
    workdir = plist.get("WorkingDirectory")
    if not workdir or not Path(str(workdir)).exists():
        problems.append(f"WorkingDirectory does not exist: {workdir}")

    if problems:
        return False, "; ".join(problems)

    plutil = shutil.which("plutil")
    if plutil:
        try:
            result = subprocess.run(
                [plutil, "-lint", str(path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return False, "plutil timed out"
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "plutil failed").strip()
    return True, "valid"


def validate_generated_plist() -> tuple[bool, str]:
    with NamedTemporaryFile(suffix=".plist", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        write_plist(tmp_path)
        return validate_plist_file(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _run_launchctl(args: list[str], *, check: bool = False, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _ensure_macos() -> None:
    if sys.platform != "darwin":
        raise SystemExit("The Hermes watcher LaunchAgent is only supported on macOS.")
    if shutil.which("launchctl") is None:
        raise SystemExit("launchctl was not found on PATH.")


def load_launch_agent() -> int:
    _ensure_macos()
    plist_path = installed_plist_path()
    if not plist_path.exists():
        raise SystemExit(f"Watcher plist is not installed: {plist_path}")
    ok, detail = validate_plist_file(plist_path)
    if not ok:
        raise SystemExit(f"Watcher plist is invalid: {detail}")

    target = f"{_launchd_domain()}/{WATCHER_LABEL}"
    _run_launchctl(["bootout", target], timeout=90)
    result = _run_launchctl(["bootstrap", _launchd_domain(), str(plist_path)])
    if result.returncode != 0:
        legacy = _run_launchctl(["load", str(plist_path)])
        if legacy.returncode != 0:
            detail = (result.stderr or result.stdout or legacy.stderr or legacy.stdout).strip()
            raise SystemExit(f"Failed to load watcher LaunchAgent: {detail}")
    _run_launchctl(["kickstart", target])
    print(f"Loaded {WATCHER_LABEL}")
    _print_status_summary()
    return 0


def unload_launch_agent() -> int:
    _ensure_macos()
    target = f"{_launchd_domain()}/{WATCHER_LABEL}"
    result = _run_launchctl(["bootout", target], timeout=90)
    if result.returncode != 0 and installed_plist_path().exists():
        _run_launchctl(["unload", str(installed_plist_path())])
    print(f"Unloaded {WATCHER_LABEL}")
    _print_status_summary()
    return 0


def install_launch_agent(load: bool = True, force: bool = False) -> int:
    _ensure_macos()
    plist_path = installed_plist_path()
    if plist_path.exists() and not force:
        print(f"Watcher plist already installed: {plist_path}")
        print("Use --force to rewrite it.")
    else:
        write_plist(plist_path)
        print(f"Installed watcher plist: {plist_path}")

    ok, detail = validate_plist_file(plist_path)
    if not ok:
        raise SystemExit(f"Installed watcher plist is invalid: {detail}")
    print(f"Plist validation: {detail}")

    if load:
        return load_launch_agent()
    _print_status_summary()
    return 0


def _parse_launchctl_list_output(text: str) -> tuple[int | None, int | None]:
    pid: int | None = None
    status: int | None = None

    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] == WATCHER_LABEL:
            try:
                pid = int(parts[0])
            except ValueError:
                pid = None
            try:
                status = int(parts[1])
            except ValueError:
                status = None
            return pid, status

    pid_match = re.search(r'"?PID"?\s*=\s*(\d+)', text)
    if pid_match:
        pid = int(pid_match.group(1))
    status_match = re.search(r'"?(?:LastExitStatus|ExitStatus)"?\s*=\s*(-?\d+)', text)
    if status_match:
        status = int(status_match.group(1))
    return pid, status


def read_launchd_state() -> LaunchdState:
    if sys.platform != "darwin" or shutil.which("launchctl") is None:
        return LaunchdState(loaded=False)
    try:
        result = _run_launchctl(["list", WATCHER_LABEL], timeout=10)
    except subprocess.TimeoutExpired:
        return LaunchdState(loaded=False, raw="launchctl timed out")
    if result.returncode != 0:
        return LaunchdState(loaded=False, raw=(result.stderr or result.stdout).strip())
    pid, status = _parse_launchctl_list_output(result.stdout)
    return LaunchdState(loaded=True, pid=pid, last_exit_status=status, raw=result.stdout.strip())


def _print_status_summary() -> None:
    plist_path = installed_plist_path()
    state = read_launchd_state()
    logs = watcher_log_paths()
    data = watcher_data_paths()
    plist_status = "installed" if plist_path.exists() else "not installed"
    valid, validation = validate_plist_file(plist_path) if plist_path.exists() else (False, "not installed")

    print()
    print("Hermes JSON-to-Markdown watcher")
    print(f"  Label:          {WATCHER_LABEL}")
    print(f"  Plist:          {plist_path} ({plist_status}, {'valid' if valid else validation})")
    print(f"  Source plist:   {source_plist_path()}")
    print(f"  Loaded:         {'yes' if state.loaded else 'no'}")
    if state.pid is not None:
        print(f"  PID:            {state.pid}")
    if state.last_exit_status is not None:
        print(f"  Last exit:      {state.last_exit_status}")
    print(f"  Sessions dir:   {data['sessions']}")
    print(f"  Processed log:  {data['processed']}")
    print(f"  Vault output:   {data['vault'] / 'Logs' / 'Hermes'}")
    print(f"  Watcher log:    {logs['watcher']}")
    print(f"  Launchd stdout: {logs['stdout']}")
    print(f"  Launchd stderr: {logs['stderr']}")
    if not state.loaded:
        print("  Next step:      hermes watcher load")


def status_command() -> int:
    _print_status_summary()
    return 0


def validate_command(args: argparse.Namespace) -> int:
    path = Path(args.plist).expanduser() if args.plist else installed_plist_path()
    if not path.exists() and not args.plist:
        ok, detail = validate_generated_plist()
        path_label = "generated plist"
    else:
        ok, detail = validate_plist_file(path)
        path_label = str(path)
    print(f"{path_label}: {detail}")
    return 0 if ok else 1


def watcher_command(args: argparse.Namespace) -> int:
    command = getattr(args, "watcher_command", None) or "status"
    if command == "install":
        return install_launch_agent(load=not args.no_load, force=args.force)
    if command == "load":
        return load_launch_agent()
    if command == "unload":
        return unload_launch_agent()
    if command == "status":
        return status_command()
    if command == "validate":
        return validate_command(args)
    raise SystemExit(f"Unknown watcher command: {command}")


def register_watcher_subparser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "watcher",
        help="Manage the JSON-to-Markdown watcher LaunchAgent",
        description=(
            "Install, load, unload, validate, and inspect the macOS LaunchAgent "
            "that converts Hermes JSON session snapshots into Obsidian Markdown."
        ),
    )
    watcher_subparsers = parser.add_subparsers(dest="watcher_command")

    install = watcher_subparsers.add_parser("install", help="Install and load the LaunchAgent")
    install.add_argument("--force", action="store_true", help="Rewrite the installed plist")
    install.add_argument("--no-load", action="store_true", help="Install only; do not load launchd")

    watcher_subparsers.add_parser("load", help="Load or reload the installed LaunchAgent")
    watcher_subparsers.add_parser("unload", help="Unload the LaunchAgent without deleting the plist")
    watcher_subparsers.add_parser("status", help="Show LaunchAgent, log, and output paths")

    validate = watcher_subparsers.add_parser("validate", help="Validate the generated or installed plist")
    validate.add_argument("--plist", help="Validate a specific plist path")

    parser.set_defaults(func=watcher_command)
    return parser
