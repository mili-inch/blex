from pathlib import Path
import subprocess
from typing import Any

import pytest

from blex.adapters import uv
from blex.core.errors import BlexError


def test_uv_receives_explicit_target_and_does_not_discover_or_download_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        assert kwargs["env"]["MACOSX_DEPLOYMENT_TARGET"] == "11.0"
        output = Path(command[command.index("--output-file") + 1])
        output.write_text("example==1 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(uv.subprocess, "run", run)
    locked = uv.resolve_requirements(
        tmp_path / "uv",
        dependencies=('example; sys_platform == "darwin"',),
        python_version="3.12",
        platform_name="macos-arm64",
        work_dir=tmp_path,
        verbose=True,
    )
    command = captured[0]
    assert command[command.index("--python-version") + 1] == "3.12"
    assert command[command.index("--python-platform") + 1] == "aarch64-apple-darwin"
    assert {"--no-config", "--no-python-downloads", "--generate-hashes", "--no-sources"} <= set(
        command
    )
    assert "--no-strip-markers" not in command
    assert locked.name == "requirements.txt"


@pytest.mark.parametrize("status", [0, 17])
def test_uv_resolution_rejects_failure_or_missing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setattr(
        uv.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, status)
    )
    with pytest.raises(BlexError):
        uv.resolve_requirements(
            tmp_path / "uv",
            dependencies=("demo",),
            python_version="3.11",
            platform_name="linux-x64",
            work_dir=tmp_path,
            verbose=False,
        )


def test_uv_version_and_execution_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        uv.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "uv 0.12.1\n", ""),
    )
    assert uv.get_uv_version(tmp_path / "uv", verbose=True) == "uv 0.12.1"
    monkeypatch.setattr(
        uv.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "broken"),
    )
    with pytest.raises(BlexError, match="broken"):
        uv.get_uv_version(tmp_path / "uv")

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("not executable")

    monkeypatch.setattr(uv.subprocess, "run", fail)
    with pytest.raises(BlexError, match="failed to execute uv"):
        uv.get_uv_version(tmp_path / "uv")
    with pytest.raises(BlexError, match="failed to execute uv"):
        uv.resolve_requirements(
            tmp_path / "uv",
            dependencies=("demo",),
            python_version="3.11",
            platform_name="linux-x64",
            work_dir=tmp_path,
            verbose=False,
        )
