"""Target-aware dependency resolution through uv's public command line."""

from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path
import subprocess
import sys

from blex.core.errors import BlexError


UV_PLATFORMS = {
    "windows-x64": "x86_64-pc-windows-msvc",
    "windows-arm64": "aarch64-pc-windows-msvc",
    "macos-x64": "x86_64-apple-darwin",
    "macos-arm64": "aarch64-apple-darwin",
    "linux-x64": "x86_64-manylinux_2_28",
}


def get_uv_version(executable: Path, *, verbose: bool = False) -> str:
    command = [str(executable), "--version"]
    if verbose:
        print(f"uv version command: {subprocess.list2cmdline(command)}")
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as error:
        raise BlexError(f"failed to execute uv: {executable}: {error}") from error
    if result.returncode or not result.stdout.strip():
        raise BlexError(f"uv --version failed: {result.stdout.strip()} {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_requirements(
    executable: Path,
    *,
    dependencies: Sequence[str],
    python_version: str,
    platform_name: str,
    work_dir: Path,
    verbose: bool,
) -> Path:
    """Write a hash-locked, marker-free dependency closure for one target."""
    source = work_dir / "requirements.in"
    locked = work_dir / "requirements.txt"
    source.write_text("\n".join(dependencies) + "\n", encoding="utf-8", newline="\n")
    command = [
        str(executable),
        "pip",
        "compile",
        str(source),
        "--python",
        sys.executable,
        "--python-version",
        python_version,
        "--python-platform",
        UV_PLATFORMS[platform_name],
        "--only-binary=:all:",
        "--generate-hashes",
        "--no-header",
        "--no-annotate",
        "--no-config",
        "--no-sources",
        "--no-python-downloads",
        "--no-cache",
        "--no-progress",
        "--output-file",
        str(locked),
    ]
    environment = dict(os.environ)
    # Match the deployment baseline used by pip's macOS tags.
    environment["MACOSX_DEPLOYMENT_TARGET"] = "11.0"
    if verbose:
        print(f"uv resolve command: {subprocess.list2cmdline(command)}")
    try:
        result = subprocess.run(command, check=False, env=environment)
    except OSError as error:
        raise BlexError(f"failed to execute uv: {executable}: {error}") from error
    if result.returncode:
        raise BlexError(f"uv dependency resolution failed with exit code {result.returncode}")
    if not locked.is_file() or locked.is_symlink():
        raise BlexError("uv did not create a regular requirements lock file")
    return locked
