"""Wheel target validation, cache resolution, and target artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import platform as host_platform
import re
import shutil
from typing import Any
import uuid

from blex.adapters.pip import download_wheels, get_pip_version, resolve_pip_executable
from blex.adapters.uv import get_uv_version
from blex.core.project import resolve_executable
from blex.core.cache import (
    CachedWheel,
    FileLock,
    cache_entry_path,
    cache_lock_path,
    sha256_file,
    validate_cache_entry,
    wheel_fingerprint,
    write_cache_metadata,
)
from blex.core.errors import BlexError


SUPPORTED_PLATFORMS = (
    "windows-x64",
    "windows-arm64",
    "macos-x64",
    "macos-arm64",
    "linux-x64",
)

PIP_PLATFORMS = {
    "windows-x64": ("win_amd64",),
    "windows-arm64": ("win_arm64",),
    "macos-x64": ("macosx_11_0_x86_64",),
    "macos-arm64": ("macosx_11_0_arm64",),
    "linux-x64": tuple(f"manylinux_2_{minor}_x86_64" for minor in range(28, 4, -1))
    + ("manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"),
}

_PYTHON_VERSION = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_ARCHIVE_SUFFIXES = (".whl", ".zip", ".tar", ".tar.gz", ".tar.bz2", ".tgz")


@dataclass(frozen=True)
class WheelArtifact:
    """A Wheel source and its path relative to the Extension root."""

    source: Path
    relative_path: PurePosixPath
    sha256: str

    @property
    def manifest_entry(self) -> str:
        return f"./{self.relative_path.as_posix()}"


def normalize_python_version(value: str) -> str:
    """Validate and normalize the target ``major.minor`` representation."""

    if not isinstance(value, str) or "\n" in value or "\r" in value:
        raise BlexError("Python target must be a single major.minor string")
    normalized = value.strip()
    if _PYTHON_VERSION.fullmatch(normalized) is None:
        raise BlexError(f"invalid Python target: {value!r}; expected major.minor")
    return normalized


def normalize_python_versions(values: Sequence[str]) -> tuple[str, ...]:
    """Validate, reject duplicates, and numerically sort Python targets."""

    normalized = [normalize_python_version(value) for value in values]
    if len(set(normalized)) != len(normalized):
        raise BlexError("Python targets must not contain duplicates")
    return tuple(
        sorted(normalized, key=lambda value: tuple(int(part) for part in value.split(".")))
    )


def stage_id(python_version: str) -> str:
    return f"py{normalize_python_version(python_version)}"


def normalize_platforms(values: Sequence[str]) -> tuple[str, ...]:
    """Validate, reject duplicates, and return the fixed Platform order."""

    platforms = list(values)
    invalid = [value for value in platforms if value not in PIP_PLATFORMS]
    if invalid:
        raise BlexError(f"unsupported Wheel Platform: {invalid[0]}")
    if len(set(platforms)) != len(platforms):
        raise BlexError("Wheel Platforms must not contain duplicates")
    return tuple(name for name in SUPPORTED_PLATFORMS if name in platforms)


def current_platform(
    system: str | None = None,
    machine: str | None = None,
) -> str:
    """Map the current process OS and architecture to a Blender Platform."""

    system_name = host_platform.system() if system is None else system
    machine_name = host_platform.machine() if machine is None else machine
    normalized_machine = machine_name.lower()
    if system_name == "Windows" and normalized_machine in {"amd64", "x86_64"}:
        return "windows-x64"
    if system_name == "Windows" and normalized_machine in {"arm64", "aarch64"}:
        return "windows-arm64"
    if system_name == "Darwin" and normalized_machine == "x86_64":
        return "macos-x64"
    if system_name == "Darwin" and normalized_machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system_name == "Linux" and normalized_machine in {"x86_64", "amd64"}:
        return "linux-x64"
    raise BlexError(f"unsupported host Platform for Wheel targeting: {system_name} {machine_name}")


def validate_dependencies(dependencies: Sequence[str]) -> None:
    """Reject pip-only and non-index requirement forms before pip runs."""

    for dependency in dependencies:
        if (
            not isinstance(dependency, str)
            or not dependency.strip()
            or any(character in dependency for character in "\0\r\n")
        ):
            raise BlexError("project dependencies must contain non-empty strings")
        value = dependency.strip()
        requirement = value.partition(";")[0].strip()
        lowered = requirement.lower()
        if value.startswith("-"):
            raise BlexError(f"pip option requirements are not supported: {dependency}")
        if lowered.startswith(("git+", "hg+", "svn+", "bzr+")):
            raise BlexError(f"VCS requirements are not supported: {dependency}")
        if "@" in requirement or "://" in requirement or lowered.startswith("file:"):
            raise BlexError(f"direct URL requirements are not supported: {dependency}")
        if (
            requirement.startswith((".", "/", "\\", "~"))
            or _WINDOWS_DRIVE.match(requirement)
            or "/" in requirement
            or "\\" in requirement
            or lowered.endswith(_ARCHIVE_SUFFIXES)
        ):
            raise BlexError(f"local dependency requirements are not supported: {dependency}")


def resolve_wheels(
    *,
    project_root: Path,
    source_dir: Path,
    manifest: Mapping[str, Any],
    dependencies: Sequence[str],
    python_version: str | None,
    platform_name: str,
    automatic: bool,
    pip_command: str,
    uv_command: str,
    refresh: bool,
    verbose: bool = False,
) -> tuple[WheelArtifact, ...]:
    """Resolve manual and optionally automatic Wheels for one Python target.

    ``python_version`` may be ``None`` only when automatic collection is
    disabled.  This permits ``dev --no-wheels`` to avoid reading the project's
    ``.python-version`` altogether.
    """

    manual = _manual_wheels(source_dir, manifest)
    if not automatic:
        if refresh:
            raise BlexError("--refresh-wheels cannot be used when automatic Wheels are disabled")
        return _merge_artifacts(manual)

    if python_version is None:
        raise BlexError("a Python target is required when automatic Wheels are enabled")
    normalized_python = normalize_python_version(python_version)
    normalize_platforms((platform_name,))
    validate_dependencies(dependencies)
    if not dependencies:
        return _merge_artifacts(manual)

    pip_executable = resolve_pip_executable(pip_command, project_root)
    pip_version = get_pip_version(pip_executable, verbose=verbose)
    uv_executable = resolve_executable(uv_command, project_root, "uv")
    uv_version = get_uv_version(uv_executable, verbose=verbose)
    cached = _resolve_cached_target(
        project_root=project_root,
        dependencies=dependencies,
        python_version=normalized_python,
        platform_name=platform_name,
        pip_executable=pip_executable,
        pip_version=pip_version,
        uv_executable=uv_executable,
        uv_version=uv_version,
        refresh=refresh,
        verbose=verbose,
    )
    automatic_wheels = [
        WheelArtifact(wheel.path, PurePosixPath("wheels", wheel.filename), wheel.sha256)
        for wheel in cached
    ]
    return _merge_artifacts((*manual, *automatic_wheels))


def _manual_wheels(
    source_dir: Path,
    manifest: Mapping[str, Any],
) -> tuple[WheelArtifact, ...]:
    raw_wheels = manifest.get("wheels")
    if raw_wheels is None:
        return ()
    if (
        not isinstance(raw_wheels, Sequence)
        or isinstance(raw_wheels, (str, bytes))
        or not raw_wheels
        or any(not isinstance(value, str) or not value for value in raw_wheels)
    ):
        raise BlexError("Manifest wheels must be a non-empty array of non-empty strings")

    try:
        source_real = source_dir.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BlexError(f"cannot resolve source directory: {source_dir}") from error
    artifacts = []
    for raw_path in raw_wheels:
        relative = _validate_manual_wheel_path(raw_path)
        wheel_path = source_dir.joinpath(*relative.parts)
        try:
            lexical_relative = wheel_path.relative_to(source_dir)
        except ValueError as error:
            raise BlexError(f"manual Wheel path escapes source-dir: {raw_path}") from error
        if lexical_relative.parts != relative.parts:
            raise BlexError(f"manual Wheel path is not represented exactly: {raw_path}")
        try:
            wheel_real = wheel_path.resolve(strict=True)
            wheel_real.relative_to(source_real)
        except (OSError, RuntimeError, ValueError) as error:
            raise BlexError(
                f"manual Wheel must resolve to a file inside source-dir: {raw_path}"
            ) from error
        if not wheel_real.is_file():
            raise BlexError(f"manual Wheel is not a regular file: {raw_path}")
        artifacts.append(WheelArtifact(wheel_path, relative, sha256_file(wheel_real)))
    return tuple(artifacts)


def _validate_manual_wheel_path(value: str) -> PurePosixPath:
    if not value or "\\" in value or value.startswith("/") or _WINDOWS_DRIVE.match(value):
        raise BlexError(f"invalid manual Wheel path: {value!r}")
    raw = value[2:] if value.startswith("./") else value
    if not raw:
        raise BlexError(f"invalid manual Wheel path: {value!r}")
    segments = raw.split("/")
    if any(not segment or segment in {".", ".."} or ":" in segment for segment in segments):
        raise BlexError(f"invalid manual Wheel path: {value!r}")
    relative = PurePosixPath(*segments)
    if relative.suffix != ".whl":
        raise BlexError(f"manual Wheel path must end in .whl: {value}")
    return relative


def _resolve_cached_target(
    *,
    project_root: Path,
    dependencies: Sequence[str],
    python_version: str,
    platform_name: str,
    pip_executable: Path,
    pip_version: str,
    uv_executable: Path,
    uv_version: str,
    refresh: bool,
    verbose: bool,
) -> tuple[CachedWheel, ...]:
    fingerprint = wheel_fingerprint(
        dependencies,
        python_version,
        platform_name,
        pip_executable,
        pip_version,
        uv_executable=uv_executable,
        uv_version=uv_version,
    )
    entry = cache_entry_path(project_root, python_version, platform_name, fingerprint)
    lock_path = cache_lock_path(project_root, python_version, platform_name, fingerprint)
    ensure_blex_root(project_root)
    ensure_managed_directory(project_root, entry.parent)
    ensure_managed_directory(project_root, lock_path.parent)

    with FileLock(lock_path, "Wheel cache"):
        had_entry = entry.exists()
        if had_entry or _is_link_or_reparse(entry):
            if _is_link_or_reparse(entry):
                raise BlexError(
                    f"Wheel cache entry must not be a symlink or reparse point: {entry}"
                )
            if not refresh:
                wheels = validate_cache_entry(
                    entry,
                    fingerprint=fingerprint,
                    python_version=python_version,
                    platform_name=platform_name,
                    pip_executable=pip_executable,
                    pip_version=pip_version,
                    uv_executable=uv_executable,
                    uv_version=uv_version,
                    dependencies=dependencies,
                )
                print(f"Wheel cache hit: {entry}")
                return wheels

        if verbose:
            action = "refresh" if had_entry else "miss"
            print(f"Wheel cache {action}: {entry}")
        temporary = entry.parent / f".tmp-{uuid.uuid4().hex}"
        backup = entry.parent / f".backup-{uuid.uuid4().hex}" if had_entry else None
        commit_started = False
        try:
            temporary.mkdir()
            abi = "cp" + python_version.replace(".", "")
            downloaded = download_wheels(
                pip_executable,
                dependencies=dependencies,
                python_version=python_version,
                abi=abi,
                pip_platforms=PIP_PLATFORMS[platform_name],
                uv_executable=uv_executable,
                platform_name=platform_name,
                destination=temporary,
                verbose=verbose,
            )
            write_cache_metadata(
                temporary,
                fingerprint=fingerprint,
                python_version=python_version,
                platform_name=platform_name,
                pip_executable=pip_executable,
                pip_version=pip_version,
                uv_executable=uv_executable,
                uv_version=uv_version,
                dependencies=dependencies,
                wheels=downloaded,
            )
            validate_cache_entry(
                temporary,
                fingerprint=fingerprint,
                python_version=python_version,
                platform_name=platform_name,
                pip_executable=pip_executable,
                pip_version=pip_version,
                uv_executable=uv_executable,
                uv_version=uv_version,
                dependencies=dependencies,
            )
            commit_started = True
            if backup is not None:
                entry.rename(backup)
            temporary.rename(entry)
        except BaseException as exc:
            try:
                if commit_started:
                    _rollback_cache_replacement(
                        project_root=project_root,
                        entry=entry,
                        temporary=temporary,
                        backup=backup,
                        had_entry=had_entry,
                    )
                elif temporary.exists() or _is_link_or_reparse(temporary):
                    remove_managed_tree(project_root, temporary)
            except Exception as rollback_exc:
                raise BlexError(
                    f"Wheel cache update and rollback both failed: {rollback_exc}"
                ) from exc
            raise
        if backup is not None:
            try:
                remove_managed_tree(project_root, backup)
            except Exception as exc:
                raise BlexError(
                    "Wheel cache entry was installed, but its previous backup "
                    f"could not be removed: {backup}: {exc}"
                ) from exc
        return validate_cache_entry(
            entry,
            fingerprint=fingerprint,
            python_version=python_version,
            platform_name=platform_name,
            pip_executable=pip_executable,
            pip_version=pip_version,
            uv_executable=uv_executable,
            uv_version=uv_version,
            dependencies=dependencies,
        )


def _rollback_cache_replacement(
    *,
    project_root: Path,
    entry: Path,
    temporary: Path,
    backup: Path | None,
    had_entry: bool,
) -> None:
    """Restore the pre-update cache state from the paths that actually exist."""

    entry_exists = entry.exists() or _is_link_or_reparse(entry)
    backup_exists = backup is not None and (backup.exists() or _is_link_or_reparse(backup))
    if backup_exists:
        if entry_exists:
            if temporary.exists() or _is_link_or_reparse(temporary):
                raise BlexError(
                    "cannot roll back Wheel cache update: both the installed entry "
                    f"and temporary path exist: {entry}, {temporary}"
                )
            entry.rename(temporary)
        assert backup is not None
        backup.rename(entry)
    elif had_entry:
        if not entry_exists:
            raise BlexError(
                "cannot roll back Wheel cache update: both the original entry "
                f"and its backup are missing: {entry}"
            )
    elif entry_exists:
        if temporary.exists() or _is_link_or_reparse(temporary):
            raise BlexError(
                "cannot roll back Wheel cache update: both the installed entry "
                f"and temporary path exist: {entry}, {temporary}"
            )
        entry.rename(temporary)

    if temporary.exists() or _is_link_or_reparse(temporary):
        remove_managed_tree(project_root, temporary)


def _merge_artifacts(artifacts: Sequence[WheelArtifact]) -> tuple[WheelArtifact, ...]:
    merged: dict[str, WheelArtifact] = {}
    for artifact in artifacts:
        key = artifact.relative_path.as_posix()
        previous = merged.get(key)
        if previous is not None:
            if previous.sha256 != artifact.sha256:
                raise BlexError(f"Wheel content conflict at Extension path: ./{key}")
            continue
        merged[key] = artifact
    return tuple(merged[key] for key in sorted(merged))


def ensure_blex_root(project_root: Path) -> None:
    """Create the managed root and its required ignore file, without following links."""
    blex_root = project_root / ".blex"
    if _is_link_or_reparse(blex_root) or (blex_root.exists() and not blex_root.is_dir()):
        raise BlexError(f"managed .blex path must be a regular directory: {blex_root}")
    blex_root.mkdir(exist_ok=True)
    ignore_path = blex_root / ".gitignore"
    required = ("*", "!.gitignore")
    if _is_link_or_reparse(ignore_path):
        raise BlexError(f"managed .blex ignore path must not be a symlink: {ignore_path}")
    if ignore_path.exists():
        if _is_link_or_reparse(ignore_path) or not ignore_path.is_file():
            raise BlexError(f"managed .blex ignore path is invalid: {ignore_path}")
        try:
            existing_text = ignore_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise BlexError(f"cannot read managed ignore file: {ignore_path}") from error
        lines = existing_text.splitlines()
        missing = [line for line in required if line not in lines]
        if missing:
            separator = "" if not existing_text or existing_text.endswith(("\n", "\r")) else "\n"
            ignore_path.write_text(
                existing_text + separator + "\n".join(missing) + "\n",
                encoding="utf-8",
                newline="\n",
            )
    else:
        ignore_path.write_text("*\n!.gitignore\n", encoding="utf-8", newline="\n")


def ensure_managed_directory(project_root: Path, directory: Path) -> None:
    """Create a directory strictly below ``.blex`` after validating each component."""
    managed_root = project_root / ".blex"
    try:
        directory.relative_to(managed_root)
    except ValueError as error:
        raise BlexError(f"managed path escapes .blex: {directory}") from error
    relative = directory.relative_to(managed_root)
    current = managed_root
    for part in relative.parts:
        current = current / part
        if _is_link_or_reparse(current):
            raise BlexError(f"managed directory must not be a symlink: {current}")
        if current.exists() and not current.is_dir():
            raise BlexError(f"managed directory path is not a directory: {current}")
        current.mkdir(exist_ok=True)


def remove_managed_tree(project_root: Path, target: Path) -> None:
    """Remove a real entry only after proving its parent is below ``.blex``."""
    managed_root = (project_root / ".blex").resolve(strict=True)
    try:
        resolved_parent = target.parent.resolve(strict=True)
        resolved_parent.relative_to(managed_root)
    except (OSError, RuntimeError, ValueError) as error:
        raise BlexError(f"refusing to remove path outside managed .blex root: {target}") from error
    if _is_link_or_reparse(target):
        raise BlexError(f"refusing to recursively remove a managed symlink: {target}")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.is_file():
        target.unlink()
    else:
        raise BlexError(f"managed path is not a removable file or directory: {target}")


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x0400)
    except FileNotFoundError:
        return False
