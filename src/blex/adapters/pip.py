"""Resolve and download binary dependencies with the selected Python's pip."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import subprocess

from blex.core.cache import CachedWheel, sha256_file
from blex.core.errors import BlexError


def get_pip_version(
    python_executable: Path, *, environment: Mapping[str, str], verbose: bool = False
) -> str:
    command = [str(python_executable), "-m", "pip", "--version"]
    if verbose:
        print(f"pip version command: {subprocess.list2cmdline(command)}")
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, env=dict(environment)
        )
    except OSError as error:
        raise BlexError(f"failed to execute pip with {python_executable}: {error}") from error
    output = result.stdout.strip()
    if result.returncode != 0 or not output:
        details = _process_details(result.stdout, result.stderr)
        raise BlexError(
            f"pip is unavailable in {python_executable} "
            f"(exit code {result.returncode}); install pip in this Python environment{details}"
        )
    return output


def download_wheels(
    python_executable: Path,
    *,
    dependencies: Sequence[str],
    destination: Path,
    environment: Mapping[str, str],
    verbose: bool = False,
) -> tuple[CachedWheel, ...]:
    """Resolve the full dependency closure in this interpreter's environment."""
    command = [
        str(python_executable),
        "-m",
        "pip",
        "download",
        "--only-binary=:all:",
        "--dest",
        str(destination),
        "--disable-pip-version-check",
        "--no-input",
        "--no-cache-dir",
        *dependencies,
    ]
    if verbose:
        print(f"pip download command: {subprocess.list2cmdline(command)}")
    try:
        result = subprocess.run(command, check=False, env=dict(environment))
    except OSError as error:
        raise BlexError(f"failed to execute pip with {python_executable}: {error}") from error
    if result.returncode != 0:
        raise BlexError(f"pip download failed with exit code {result.returncode}")

    wheels: list[CachedWheel] = []
    for child in sorted(destination.iterdir(), key=lambda path: path.name):
        if child.is_symlink() or not child.is_file() or not child.name.endswith(".whl"):
            raise BlexError(f"pip created an unexpected download artifact: {child}")
        wheels.append(CachedWheel(child, child.name, sha256_file(child)))
    return tuple(wheels)


def _process_details(stdout: str, stderr: str) -> str:
    sections = []
    if stdout.strip():
        sections.append(f"stdout:\n{stdout.rstrip()}")
    if stderr.strip():
        sections.append(f"stderr:\n{stderr.rstrip()}")
    if not sections:
        return ""
    return "\n" + "\n".join(sections)
