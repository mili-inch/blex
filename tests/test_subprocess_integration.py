from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from blex.adapters import blender as blender_adapter
from blex.commands import dev as dev_command
from blex.commands import init as init_command
from blex.core import wheels
from blex.core.errors import BlexError, InitError


_FAKE_BLENDER = r"""
import json
import os
import sys
import time
from pathlib import Path

mode = os.environ["BLEX_FAKE_BLENDER_MODE"]
log_path = Path(os.environ["BLEX_FAKE_BLENDER_LOG"])
log_path.write_text(
    json.dumps(
        {
            "argv": sys.argv[1:],
            "user_resources": os.environ.get("BLENDER_USER_RESOURCES"),
        }
    ),
    encoding="utf-8",
)
print("fake Blender stdout", flush=True)
print("fake Blender stderr", file=sys.stderr, flush=True)
if mode == "detect-success":
    print("BLEX_PYTHON_VERSION=3.14", flush=True)
elif mode == "detect-invalid":
    print("BLEX_PYTHON_VERSION=03.14", flush=True)
elif mode == "detect-timeout":
    time.sleep(0.5)
elif mode == "dev":
    raise SystemExit(int(os.environ["BLEX_FAKE_BLENDER_EXIT"]))
else:
    raise RuntimeError(f"unknown fake Blender mode: {mode}")
"""


_FAKE_PIP = r"""
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
log_path = Path(os.environ["BLEX_FAKE_PIP_LOG"])
with log_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(arguments) + "\n")
if arguments == ["--version"]:
    print("pip 99.0 from blex fake pip")
    raise SystemExit(0)
if not arguments or arguments[0] != "download":
    raise RuntimeError(f"unexpected fake pip arguments: {arguments!r}")
platform_name = arguments[arguments.index("--platform") + 1]
print(f"fake pip stdout {platform_name}", flush=True)
print(f"fake pip stderr {platform_name}", file=sys.stderr, flush=True)
if platform_name == os.environ.get("BLEX_FAKE_PIP_FAIL_PLATFORM"):
    raise SystemExit(int(os.environ["BLEX_FAKE_PIP_EXIT"]))
destination = Path(arguments[arguments.index("--dest") + 1])
destination.mkdir(parents=True, exist_ok=True)
(destination / "fake_dependency-1.0-py3-none-any.whl").write_bytes(b"fake wheel")
"""

_FAKE_UV = r"""
import sys
from pathlib import Path
arguments = sys.argv[1:]
if arguments == ["--version"]:
    print("uv 99")
else:
    Path(arguments[arguments.index("--output-file") + 1]).write_text(
        "fake-dependency==1 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8"
    )
"""


def _python_launcher(tmp_path: Path, name: str, program: str) -> Path:
    if os.name == "nt":
        script = tmp_path / f"{name}.py"
        script.write_text(program, encoding="utf-8", newline="\n")
        launcher = tmp_path / f"{name}.cmd"
        python_command = subprocess.list2cmdline((sys.executable, str(script)))
        launcher.write_text(
            f"@echo off\r\n{python_command} %*\r\n",
            encoding="utf-8",
        )
        return launcher.resolve()

    launcher = tmp_path / name
    launcher.write_text(
        f"#!{sys.executable}\n{program.lstrip()}",
        encoding="utf-8",
        newline="\n",
    )
    launcher.chmod(0o755)
    return launcher.resolve()


def _init_args(root: Path, blender: Path) -> argparse.Namespace:
    return argparse.Namespace(
        directory=str(root),
        force=False,
        name=None,
        description=None,
        python=None,
        blender=str(blender),
        source_dir=None,
        verbose=False,
    )


def _dev_project(root: Path) -> Path:
    source = root / "src" / "x"
    source.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """[project]
name = "x"
dependencies = []

[tool.blex]
source-dir = "src/x"

[tool.blex.dev]
link-mode = "copy"
wheels = false
""",
        encoding="utf-8",
    )
    (source / "blender_manifest.toml").write_text(
        """schema_version = "1.0.0"
id = "x"
version = "1.0.0"
name = "Subprocess Extension"
type = "add-on"
""",
        encoding="utf-8",
    )
    (source / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    return source


def _dev_args(root: Path, blender: Path, blend_file: Path) -> argparse.Namespace:
    return argparse.Namespace(
        directory=str(root),
        source_dir=None,
        blender=str(blender),
        blend_file=str(blend_file),
        user_resources=None,
        link_mode=None,
        reload_panel=None,
        debugpy=None,
        debugpy_host=None,
        debugpy_port=None,
        wheels=None,
        python=None,
        pip=None,
        uv=None,
        refresh_wheels=False,
        blender_args=["--background", "--factory-startup"],
        verbose=False,
    )


def _pip_project(tmp_path: Path, name: str) -> tuple[Path, Path]:
    root = tmp_path / name
    source = root / "src" / "extension"
    source.mkdir(parents=True)
    return root, source


def _read_json_lines(path: Path) -> list[list[str]]:
    return [
        cast(list[str], json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_fake_blender_detection_writes_cache_json_and_exact_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "fake"
    fake_root.mkdir()
    executable = _python_launcher(fake_root, "fake-blender", _FAKE_BLENDER)
    log = tmp_path / "blender-detect.json"
    root = tmp_path / "project"
    monkeypatch.setenv("BLEX_FAKE_BLENDER_MODE", "detect-success")
    monkeypatch.setenv("BLEX_FAKE_BLENDER_LOG", str(log))

    assert init_command.run(_init_args(root, executable)) == 0
    assert (root / ".python-version").read_text(encoding="utf-8") == "3.14\n"
    cache = json.loads(
        (root / ".blex" / "cache" / "blender-python.json").read_text(encoding="utf-8")
    )
    assert cache["format"] == 1
    assert list(cache["entries"].values()) == [
        {"executable": executable.as_posix(), "python": "3.14"}
    ]
    invocation = json.loads(log.read_text(encoding="utf-8"))
    assert invocation["argv"][:3] == ["--background", "--factory-startup", "--python-expr"]
    assert "BLEX_PYTHON_VERSION=" in invocation["argv"][3]


@pytest.mark.parametrize(
    ("mode", "message"),
    (("detect-invalid", "invalid version"), ("detect-timeout", "timed out")),
)
def test_fake_blender_detection_invalid_and_timeout_are_handled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    message: str,
) -> None:
    fake_root = tmp_path / "fake"
    fake_root.mkdir()
    executable = _python_launcher(fake_root, "fake-blender", _FAKE_BLENDER)
    root = tmp_path / "project"
    monkeypatch.setenv("BLEX_FAKE_BLENDER_MODE", mode)
    monkeypatch.setenv("BLEX_FAKE_BLENDER_LOG", str(tmp_path / "blender-detect.json"))
    if mode == "detect-timeout":
        real_run = subprocess.run

        def short_timeout(
            command: Sequence[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            assert kwargs["timeout"] == 30
            kwargs["timeout"] = 0.1
            return cast(subprocess.CompletedProcess[str], real_run(command, **kwargs))

        monkeypatch.setattr(init_command.subprocess, "run", short_timeout)

    with pytest.raises(InitError, match=message):
        init_command.run(_init_args(root, executable))
    assert not root.exists()


def test_dev_real_fake_blender_process_receives_argv_environment_and_propagates_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    fake_root = tmp_path / "fake"
    fake_root.mkdir()
    executable = _python_launcher(fake_root, "fake-blender", _FAKE_BLENDER)
    root = tmp_path / "project"
    _dev_project(root)
    blend_file = root / "scene.blend"
    blend_file.write_bytes(b"BLENDER")
    log = tmp_path / "blender-dev.json"
    monkeypatch.setenv("BLEX_FAKE_BLENDER_MODE", "dev")
    monkeypatch.setenv("BLEX_FAKE_BLENDER_EXIT", "23")
    monkeypatch.setenv("BLEX_FAKE_BLENDER_LOG", str(log))

    assert dev_command.run(_dev_args(root, executable, blend_file)) == 23
    invocation = json.loads(log.read_text(encoding="utf-8"))
    bootstrap = (root / ".blex" / "bootstrap" / "dev.py").resolve()
    assert invocation["argv"] == [
        str(blend_file.resolve()),
        "--python-exit-code",
        "1",
        "--python",
        str(bootstrap),
        "--background",
        "--factory-startup",
    ]
    assert invocation["user_resources"] == str((root / ".blex" / "runtime_environment").resolve())
    captured = capfd.readouterr()
    assert "fake Blender stdout" in captured.out
    assert "fake Blender stderr" in captured.err


def test_real_fake_blender_process_receives_supervised_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = tmp_path / "ready"
    signal_log = tmp_path / "signal"
    fake_blender = tmp_path / "fake_blender_signal.py"
    fake_blender.write_text(
        """from __future__ import annotations

import os
import signal
import time
from pathlib import Path

ready = Path(os.environ["BLEX_FAKE_BLENDER_READY"])
signal_log = Path(os.environ["BLEX_FAKE_BLENDER_SIGNAL_LOG"])
handled_signal = signal.SIGBREAK if os.name == "nt" else signal.SIGINT


def stop(signum: int, _frame: object) -> None:
    signal_log.write_text(signal.Signals(signum).name, encoding="utf-8")
    raise SystemExit(0)


signal.signal(handled_signal, stop)
ready.write_text("ready", encoding="utf-8")
while True:
    time.sleep(0.05)
""",
        encoding="utf-8",
        newline="\n",
    )
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[str]] = []

    class InterruptOnceProcess:
        def __init__(self, child: subprocess.Popen[str]) -> None:
            self._child = child
            self._interrupt = True

        @property
        def pid(self) -> int:
            return self._child.pid

        def poll(self) -> int | None:
            return self._child.poll()

        def wait(self, timeout: float | None = None) -> int:
            if self._interrupt:
                self._interrupt = False
                deadline = time.monotonic() + 3
                while not ready.exists():
                    return_code = self._child.poll()
                    if return_code is not None:
                        raise AssertionError(
                            f"fake Blender exited before becoming ready: {return_code}"
                        )
                    if time.monotonic() >= deadline:
                        raise AssertionError("fake Blender did not become ready")
                    time.sleep(0.01)
                raise KeyboardInterrupt
            effective_timeout = min(timeout, 1) if timeout is not None else None
            return self._child.wait(timeout=effective_timeout)

        def send_signal(self, sent_signal: int) -> None:
            self._child.send_signal(sent_signal)

        def terminate(self) -> None:
            self._child.terminate()

        def kill(self) -> None:
            self._child.kill()

    def interrupting_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        child = cast("subprocess.Popen[str]", real_popen(*args, **kwargs))
        children.append(child)
        return cast("subprocess.Popen[str]", InterruptOnceProcess(child))

    monkeypatch.setattr(blender_adapter.subprocess, "Popen", interrupting_popen)
    environment = dict(os.environ)
    environment["BLEX_FAKE_BLENDER_READY"] = str(ready)
    environment["BLEX_FAKE_BLENDER_SIGNAL_LOG"] = str(signal_log)
    try:
        assert (
            blender_adapter.run_blender(
                (sys.executable, str(fake_blender)),
                environment=environment,
            )
            == 130
        )
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    expected_signal = "SIGBREAK" if os.name == "nt" else "SIGINT"
    assert signal_log.read_text(encoding="utf-8") == expected_signal
    assert children[0].poll() == 0


def test_real_fake_pip_exercises_platform_matrix_command_and_inherited_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    executable = _python_launcher(tmp_path, "fake-pip", _FAKE_PIP)
    uv_executable = _python_launcher(tmp_path, "fake-uv", _FAKE_UV)
    log = tmp_path / "pip.jsonl"
    root, source = _pip_project(tmp_path, "pip-success")
    monkeypatch.setenv("BLEX_FAKE_PIP_LOG", str(log))
    monkeypatch.delenv("BLEX_FAKE_PIP_FAIL_PLATFORM", raising=False)
    monkeypatch.delenv("BLEX_FAKE_PIP_EXIT", raising=False)

    platforms = ("windows-x64", "windows-arm64", "macos-x64", "macos-arm64", "linux-x64")
    artifacts = []
    for platform_name in platforms:
        artifacts.extend(
            wheels.resolve_wheels(
                project_root=root,
                source_dir=source,
                manifest={},
                dependencies=("fake-dependency==1",),
                python_version="3.11",
                platform_name=platform_name,
                automatic=True,
                pip_command=str(executable),
                uv_command=str(uv_executable),
                refresh=False,
            )
        )
    assert {artifact.relative_path.as_posix() for artifact in artifacts} == {
        "wheels/fake_dependency-1.0-py3-none-any.whl"
    }
    cached_artifacts = wheels.resolve_wheels(
        project_root=root,
        source_dir=source,
        manifest={},
        dependencies=("fake-dependency==1",),
        python_version="3.11",
        platform_name="windows-x64",
        automatic=True,
        pip_command=str(executable),
        uv_command=str(uv_executable),
        refresh=False,
    )
    assert cached_artifacts[0].sha256 == artifacts[0].sha256

    invocations = _read_json_lines(log)
    assert invocations[0] == ["--version"]
    assert invocations.count(["--version"]) == 6
    downloads = [command for command in invocations if command and command[0] == "download"]
    assert [command[command.index("--platform") + 1] for command in downloads] == [
        "win_amd64",
        "win_arm64",
        "macosx_11_0_x86_64",
        "macosx_11_0_arm64",
        "manylinux_2_28_x86_64",
    ]
    for command in downloads:
        assert command[0] == "download"
        assert command[1:14] == [
            "--only-binary=:all:",
            "--python-version",
            "3.11",
            "--implementation",
            "cp",
            "--abi",
            "cp311",
            "--abi",
            "abi3",
            "--abi",
            "none",
            "--platform",
            command[13],
        ]
        assert command[-7:-1] == [
            "--disable-pip-version-check",
            "--no-input",
            "--no-cache-dir",
            "--no-deps",
            "--require-hashes",
            "--requirement",
        ]
    captured = capfd.readouterr()
    for pip_platform in (
        "win_amd64",
        "win_arm64",
        "macosx_11_0_x86_64",
        "macosx_11_0_arm64",
        "manylinux_2_28_x86_64",
    ):
        assert f"fake pip stdout {pip_platform}" in captured.out
        assert f"fake pip stderr {pip_platform}" in captured.err


def test_real_fake_pip_failure_propagates_exit_and_stops_platform_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    executable = _python_launcher(tmp_path, "fake-pip", _FAKE_PIP)
    uv_executable = _python_launcher(tmp_path, "fake-uv", _FAKE_UV)
    log = tmp_path / "pip-failure.jsonl"
    root, source = _pip_project(tmp_path, "pip-failure")
    monkeypatch.setenv("BLEX_FAKE_PIP_LOG", str(log))
    monkeypatch.setenv("BLEX_FAKE_PIP_FAIL_PLATFORM", "win_arm64")
    monkeypatch.setenv("BLEX_FAKE_PIP_EXIT", "17")

    with pytest.raises(BlexError, match="exit code 17"):
        for platform_name in ("windows-x64", "windows-arm64", "linux-x64"):
            wheels.resolve_wheels(
                project_root=root,
                source_dir=source,
                manifest={},
                dependencies=("fake-dependency==1",),
                python_version="3.12",
                platform_name=platform_name,
                automatic=True,
                pip_command=str(executable),
                uv_command=str(uv_executable),
                refresh=False,
            )
    invocations = _read_json_lines(log)
    assert invocations[0] == ["--version"]
    assert [
        command[command.index("--platform") + 1]
        for command in invocations
        if command[0] == "download"
    ] == [
        "win_amd64",
        "win_arm64",
    ]
    assert all("manylinux_2_28_x86_64" not in command for command in invocations)
    captured = capfd.readouterr()
    assert "fake pip stdout win_arm64" in captured.out
    assert "fake pip stderr win_arm64" in captured.err
