import argparse
import plistlib
import subprocess
from pathlib import Path

import pytest

import hermes_cli.watcher as watcher


def _arrange_macos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setattr(watcher.sys, "platform", "darwin")
    monkeypatch.setattr(watcher, "_user_home", lambda: tmp_path)
    monkeypatch.setattr(watcher, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(
        watcher.shutil,
        "which",
        lambda name: "/bin/launchctl" if name == "launchctl" else None,
    )
    return hermes_home


def test_build_plist_points_launchd_at_watcher_wrapper(monkeypatch, tmp_path):
    hermes_home = _arrange_macos(monkeypatch, tmp_path)

    plist = watcher.build_plist()

    assert plist["Label"] == watcher.WATCHER_LABEL
    assert plist["ProgramArguments"] == [str(watcher.PROJECT_ROOT / "watcher" / "run_json_to_md.sh")]
    assert plist["WorkingDirectory"] == str(watcher.PROJECT_ROOT)
    assert plist["EnvironmentVariables"]["HERMES_HOME"] == str(hermes_home)
    assert plist["StandardOutPath"] == str(hermes_home / "watcher.launchd.out.log")
    assert plist["StandardErrorPath"] == str(hermes_home / "watcher.launchd.err.log")


def test_validate_plist_rejects_missing_wrapper(tmp_path):
    plist_path = tmp_path / "bad.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump(
            {
                "Label": watcher.WATCHER_LABEL,
                "ProgramArguments": [str(tmp_path / "missing.sh")],
                "WorkingDirectory": str(tmp_path),
            },
            handle,
        )

    ok, detail = watcher.validate_plist_file(plist_path)

    assert ok is False
    assert "watcher wrapper does not exist" in detail


def test_install_writes_valid_plist_and_loads_launch_agent(monkeypatch, tmp_path, capsys):
    _arrange_macos(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, capture_output=True, text=True, check=False, timeout=30):
        calls.append(tuple(cmd))
        if cmd == ["launchctl", "list", watcher.WATCHER_LABEL]:
            return subprocess.CompletedProcess(cmd, 0, stdout='"PID" = 123;\n"LastExitStatus" = 0;\n', stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(watcher.subprocess, "run", fake_run)

    assert watcher.install_launch_agent(load=True, force=False) == 0

    installed = watcher.installed_plist_path()
    assert installed.exists()
    ok, detail = watcher.validate_plist_file(installed)
    assert ok, detail
    assert ("launchctl", "bootstrap", watcher._launchd_domain(), str(installed)) in calls
    assert any(call[:3] == ("launchctl", "kickstart", f"{watcher._launchd_domain()}/{watcher.WATCHER_LABEL}") for call in calls)
    out = capsys.readouterr().out
    assert "Installed watcher plist" in out
    assert "Loaded com.ryangherardi.hermes-watcher" in out
    assert "Watcher log:" in out
    assert "Launchd stderr:" in out


def test_unload_keeps_plist_but_boots_out_launch_agent(monkeypatch, tmp_path):
    _arrange_macos(monkeypatch, tmp_path)
    watcher.write_plist(watcher.installed_plist_path())
    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, capture_output=True, text=True, check=False, timeout=30):
        calls.append(tuple(cmd))
        if cmd == ["launchctl", "list", watcher.WATCHER_LABEL]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not loaded")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(watcher.subprocess, "run", fake_run)

    assert watcher.unload_launch_agent() == 0

    assert watcher.installed_plist_path().exists()
    assert ("launchctl", "bootout", f"{watcher._launchd_domain()}/{watcher.WATCHER_LABEL}") in calls


def test_register_watcher_subparser_defaults_to_status():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    watcher.register_watcher_subparser(subparsers)

    args = parser.parse_args(["watcher"])

    assert args.command == "watcher"
    assert args.watcher_command is None
    assert args.func is watcher.watcher_command
