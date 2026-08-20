from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from blex.adapters import blender
from blex.core.errors import BlexError


def _executable(path: Path) -> Path:
    path.write_text("fake blender\n", encoding="utf-8")
    path.chmod(0o755)
    return path.resolve()


def test_resolve_blender_path_and_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    executable = _executable(root / "fake-blender")

    assert blender.resolve_blender_executable("./fake-blender", root) == executable
    monkeypatch.setattr(
        blender.shutil, "which", lambda value: str(executable) if value == "blender" else None
    )
    assert blender.resolve_blender_executable("blender", root) == executable


def test_resolve_blender_rejects_invalid_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.setattr(blender.shutil, "which", lambda _value: None)
    with pytest.raises(BlexError, match="must not be empty"):
        blender.resolve_blender_executable("", root)
    with pytest.raises(BlexError, match="NUL"):
        blender.resolve_blender_executable("bad\x00blender", root)
    with pytest.raises(BlexError, match="drive-relative"):
        blender.resolve_blender_executable("C:blender.exe", root)
    with pytest.raises(BlexError, match="not found"):
        blender.resolve_blender_executable("blender", root)

    with pytest.raises(BlexError, match="does not exist"):
        blender.resolve_blender_executable("./missing-blender", root)
    monkeypatch.setattr(blender.shutil, "which", lambda _value: str(root / "disappeared"))
    with pytest.raises(BlexError, match="does not exist"):
        blender.resolve_blender_executable("blender", root)

    directory = root / "directory"
    directory.mkdir()
    with pytest.raises(BlexError, match="not a regular file"):
        blender.resolve_blender_executable(str(directory), root)

    app = root / "Blender.app"
    app.mkdir()
    with pytest.raises(BlexError, match=".app"):
        blender.resolve_blender_executable(str(app), root)


def test_blender_command_has_stable_argument_order(tmp_path: Path) -> None:
    executable = tmp_path / "blender"
    blend_file = tmp_path / "scene.blend"
    bootstrap = tmp_path / ".blex" / "bootstrap" / "dev.py"
    command = blender.blender_command(
        executable,
        blend_file=blend_file,
        bootstrap_script=bootstrap,
        extra_arguments=("--background", "--factory-startup"),
    )
    assert command == (
        str(executable),
        str(blend_file),
        "--python-exit-code",
        "1",
        "--python",
        str(bootstrap),
        "--background",
        "--factory-startup",
    )
    assert isinstance(blender.format_command(command), str)


class _NormalProcess:
    pid = 123

    def wait(self, timeout: float | None = None) -> int:
        assert timeout is None
        return 27


def test_run_blender_inherits_stdio_and_creates_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_popen(command: Sequence[str], **kwargs: Any) -> _NormalProcess:
        captured["command"] = tuple(command)
        captured["kwargs"] = kwargs
        return _NormalProcess()

    monkeypatch.setattr(blender.subprocess, "Popen", fake_popen)
    environment: Mapping[str, str] = {"BLEX_TEST": "1"}
    assert blender.run_blender(("blender", "--background"), environment=environment) == 27
    assert captured["command"] == ("blender", "--background")
    kwargs = captured["kwargs"]
    assert kwargs["env"] == environment
    assert kwargs["text"] is True
    assert "stdout" not in kwargs
    assert "stderr" not in kwargs
    if os.name == "nt":
        assert kwargs["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
        assert "start_new_session" not in kwargs
    else:
        assert kwargs["start_new_session"] is True
        assert "creationflags" not in kwargs


class _InterruptedProcess:
    pid = 456

    def __init__(self) -> None:
        self.events: list[object] = []
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        self.events.append(("wait", timeout))
        if timeout is None and len(self.events) == 1:
            raise KeyboardInterrupt
        if timeout is not None:
            raise subprocess.TimeoutExpired("blender", timeout)
        return 0

    def poll(self) -> int | None:
        self.events.append("poll")
        return 0 if self.killed else None

    def send_signal(self, sent_signal: int) -> None:
        self.events.append(("signal", sent_signal))

    def terminate(self) -> None:
        self.events.append("terminate")

    def kill(self) -> None:
        self.events.append("kill")
        self.killed = True


def test_run_blender_interrupts_then_terminates_and_kills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _InterruptedProcess()
    monkeypatch.setattr(blender.subprocess, "Popen", lambda *_args, **_kwargs: process)
    if os.name != "nt":
        monkeypatch.setattr(
            blender.os,
            "killpg",
            lambda pid, sent_signal: process.events.append(("killpg", pid, sent_signal)),
        )

    assert blender.run_blender(("blender",), environment={}) == 130
    assert "terminate" in process.events
    assert "kill" in process.events
    assert ("wait", 5) in process.events
    if os.name == "nt":
        assert ("signal", signal.CTRL_BREAK_EVENT) in process.events
    else:
        assert ("killpg", process.pid, signal.SIGINT) in process.events


def test_run_blender_wraps_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("cannot execute")

    monkeypatch.setattr(blender.subprocess, "Popen", fail)
    with pytest.raises(BlexError, match="failed to start Blender"):
        blender.run_blender(("blender",), environment={})


class _FinishedProcess:
    pid = 789

    def poll(self) -> int:
        return 0


def test_signal_child_ignores_finished_and_disappearing_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blender._signal_child(cast(subprocess.Popen[str], _FinishedProcess()))

    class DisappearingProcess:
        pid = 790

        def poll(self) -> None:
            return None

        def send_signal(self, _sent_signal: int) -> None:
            raise ProcessLookupError

    if os.name == "nt":
        blender._signal_child(cast(subprocess.Popen[str], DisappearingProcess()))
    else:

        def disappear(*_args: object) -> None:
            raise ProcessLookupError

        monkeypatch.setattr(blender.os, "killpg", disappear)
        blender._signal_child(cast(subprocess.Popen[str], DisappearingProcess()))


def test_stop_interrupted_child_returns_after_graceful_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GracefulProcess:
        pid = 791

        def __init__(self) -> None:
            self.signaled = False

        def poll(self) -> int | None:
            return None if not self.signaled else 0

        def send_signal(self, _sent_signal: int) -> None:
            self.signaled = True

        def wait(self, timeout: float | None = None) -> int:
            assert timeout == 5
            return 0

    process = GracefulProcess()
    if os.name != "nt":
        monkeypatch.setattr(
            blender.os,
            "killpg",
            lambda _pid, _sent_signal: setattr(process, "signaled", True),
        )
    blender._stop_interrupted_child(cast(subprocess.Popen[str], process))
    assert process.signaled is True


def test_stop_interrupted_child_warns_when_group_signal_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Process:
        def wait(self, timeout: float | None = None) -> int:
            assert timeout == 5
            return 0

    def fail(_child: subprocess.Popen[str]) -> None:
        raise OSError("signal unavailable")

    monkeypatch.setattr(blender, "_signal_child", fail)
    blender._stop_interrupted_child(cast(subprocess.Popen[str], Process()))

    error = capsys.readouterr().err
    assert "warning: failed to send an interrupt signal to Blender" in error
    assert "signal unavailable" in error
