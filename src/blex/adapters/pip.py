"""External pip command adapter."""

from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import tempfile

from blex.adapters.uv import resolve_requirements
from blex.core.cache import CachedWheel, sha256_file
from blex.core.errors import BlexError


def validate_pip_executable_value(value: str) -> str:
    """Validate a pip executable value without resolving or executing it."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise BlexError("pip executable must not be empty or have surrounding whitespace")
    if "\x00" in value:
        raise BlexError("pip executable must not contain NUL")
    windows_path = PureWindowsPath(value)
    if windows_path.drive and not windows_path.is_absolute():
        raise BlexError(f"drive-relative pip path is not supported: {value}")
    return value


def resolve_pip_executable(value: str, project_root: Path) -> Path:
    """Resolve a pip path or command name without a fallback."""

    value = validate_pip_executable_value(value)

    is_path = Path(value).is_absolute() or "/" in value or "\\" in value
    if is_path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise BlexError(f"pip executable does not exist: {candidate}") from error
    else:
        located = shutil.which(value)
        if located is None:
            raise BlexError(f"pip executable was not found on PATH: {value}")
        resolved = Path(located).resolve()

    if not resolved.is_file():
        raise BlexError(f"pip executable is not a regular file: {resolved}")
    if os.name != "nt" and not os.access(resolved, os.X_OK):
        raise BlexError(f"pip file is not executable: {resolved}")
    return resolved


def get_pip_version(executable: Path, *, verbose: bool = False) -> str:
    """Return the complete, stripped output of ``pip --version``."""

    command = [str(executable), "--version"]
    if verbose:
        print(f"pip version command: {subprocess.list2cmdline(command)}")
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as error:
        raise BlexError(f"failed to execute pip: {executable}: {error}") from error
    output = result.stdout.strip()
    if result.returncode != 0 or not output:
        details = _process_details(result.stdout, result.stderr)
        raise BlexError(f"pip --version failed with exit code {result.returncode}{details}")
    return output


def download_wheels(
    executable: Path,
    *,
    dependencies: Sequence[str],
    python_version: str,
    abi: str,
    pip_platforms: Sequence[str],
    uv_executable: Path,
    platform_name: str,
    destination: Path,
    verbose: bool = False,
) -> tuple[CachedWheel, ...]:
    """Download binary Wheels for exactly one Python/Platform target."""

    with tempfile.TemporaryDirectory(prefix=".resolve-", dir=destination.parent) as work:
        requirements = resolve_requirements(
            uv_executable,
            dependencies=dependencies,
            python_version=python_version,
            platform_name=platform_name,
            work_dir=Path(work),
            verbose=verbose,
        )
        _download_locked(
            executable,
            requirements=requirements,
            python_version=python_version,
            abi=abi,
            pip_platforms=pip_platforms,
            destination=destination,
            verbose=verbose,
        )

    wheels: list[CachedWheel] = []
    for child in sorted(destination.iterdir(), key=lambda path: path.name):
        if child.is_symlink() or not child.is_file() or not child.name.endswith(".whl"):
            raise BlexError(f"pip created an unexpected download artifact: {child}")
        wheels.append(CachedWheel(child, child.name, sha256_file(child)))
    return tuple(wheels)


def _download_locked(
    executable: Path,
    *,
    requirements: Path,
    python_version: str,
    abi: str,
    pip_platforms: Sequence[str],
    destination: Path,
    verbose: bool,
) -> None:
    command = [
        str(executable),
        "download",
        "--only-binary=:all:",
        "--python-version",
        python_version,
        "--implementation",
        "cp",
        "--abi",
        abi,
        "--abi",
        "abi3",
        "--abi",
        "none",
        *[argument for platform in pip_platforms for argument in ("--platform", platform)],
        "--dest",
        str(destination),
        "--disable-pip-version-check",
        "--no-input",
        "--no-cache-dir",
        "--no-deps",
        "--require-hashes",
        "--requirement",
        str(requirements),
    ]
    if verbose:
        print(f"pip download command: {subprocess.list2cmdline(command)}")
    try:
        result = subprocess.run(command, check=False)
    except OSError as error:
        raise BlexError(f"failed to execute pip: {executable}: {error}") from error
    if result.returncode != 0:
        raise BlexError(f"pip download failed with exit code {result.returncode}")


def _process_details(stdout: str, stderr: str) -> str:
    sections = []
    if stdout.strip():
        sections.append(f"stdout:\n{stdout.rstrip()}")
    if stderr.strip():
        sections.append(f"stderr:\n{stderr.rstrip()}")
    if not sections:
        return ""
    return "\n" + "\n".join(sections)
