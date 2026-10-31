"""Wheel target validation, cache resolution, and immutable artifact publication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import shutil
import uuid

from blex.adapters.pip import download_wheels, get_pip_version
from blex.core.environment import NativeEnvironment
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
from blex.core.filesystem import ensure_blex_root, ensure_managed_directory, remove_managed_tree
from blex.core.project import is_symlink_or_reparse


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
    wheel_paths: Sequence[str],
    dependencies: Sequence[str],
    native: NativeEnvironment,
    environment: Mapping[str, str],
    automatic: bool,
    refresh: bool,
    verbose: bool = False,
) -> tuple[WheelArtifact, ...]:
    """Resolve manual and native automatic Wheels for the running interpreter."""

    manual = _manual_wheels(source_dir, wheel_paths)
    if not automatic:
        if refresh:
            raise BlexError("--refresh-wheels cannot be used when automatic Wheels are disabled")
        return _merge_artifacts(manual)

    validate_dependencies(dependencies)
    if not dependencies:
        return _merge_artifacts(manual)

    pip_version = get_pip_version(
        native.python_executable, environment=environment, verbose=verbose
    )
    cached = _resolve_cached_target(
        project_root=project_root,
        dependencies=dependencies,
        python_version=native.python_version,
        platform_name=native.platform_name,
        python_executable=native.python_executable,
        pip_version=pip_version,
        runtime=native.runtime,
        environment=environment,
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
    wheel_paths: Sequence[str],
) -> tuple[WheelArtifact, ...]:
    """Resolve already schema-validated Manifest paths inside the source tree."""
    if not wheel_paths:
        return ()

    try:
        source_real = source_dir.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BlexError(f"cannot resolve source directory: {source_dir}") from error
    artifacts = []
    for raw_path in wheel_paths:
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
    python_executable: Path,
    pip_version: str,
    runtime: Mapping[str, str],
    environment: Mapping[str, str],
    refresh: bool,
    verbose: bool,
) -> tuple[CachedWheel, ...]:
    fingerprint = wheel_fingerprint(
        dependencies,
        python_version,
        platform_name,
        python_executable,
        pip_version,
        environment,
        runtime=runtime,
    )
    entry = cache_entry_path(project_root, python_version, platform_name, fingerprint)
    lock_path = cache_lock_path(project_root, python_version, platform_name, fingerprint)
    ensure_blex_root(project_root)
    ensure_managed_directory(project_root, entry.parent)
    ensure_managed_directory(project_root, lock_path.parent)

    with FileLock(lock_path, "Wheel cache"):
        had_entry = entry.exists()
        if had_entry or is_symlink_or_reparse(entry):
            if is_symlink_or_reparse(entry):
                raise BlexError(
                    f"Wheel cache entry must not be a symlink or reparse point: {entry}"
                )
            if not refresh:
                wheels = validate_cache_entry(
                    entry,
                    fingerprint=fingerprint,
                    python_version=python_version,
                    platform_name=platform_name,
                    python_executable=python_executable,
                    pip_version=pip_version,
                    runtime=runtime,
                    dependencies=dependencies,
                )
                print(f"Wheel cache hit: {entry}")
                return _freeze_wheels(project_root, wheels)

        if verbose:
            action = "refresh" if had_entry else "miss"
            print(f"Wheel cache {action}: {entry}")
        temporary = entry.parent / f".tmp-{uuid.uuid4().hex}"
        backup = entry.parent / f".backup-{uuid.uuid4().hex}" if had_entry else None
        commit_started = False
        try:
            temporary.mkdir()
            downloaded = download_wheels(
                python_executable,
                dependencies=dependencies,
                destination=temporary,
                environment=environment,
                verbose=verbose,
            )
            write_cache_metadata(
                temporary,
                fingerprint=fingerprint,
                python_version=python_version,
                platform_name=platform_name,
                python_executable=python_executable,
                pip_version=pip_version,
                runtime=runtime,
                dependencies=dependencies,
                wheels=downloaded,
            )
            validate_cache_entry(
                temporary,
                fingerprint=fingerprint,
                python_version=python_version,
                platform_name=platform_name,
                python_executable=python_executable,
                pip_version=pip_version,
                runtime=runtime,
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
                elif temporary.exists() or is_symlink_or_reparse(temporary):
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
        installed = validate_cache_entry(
            entry,
            fingerprint=fingerprint,
            python_version=python_version,
            platform_name=platform_name,
            python_executable=python_executable,
            pip_version=pip_version,
            runtime=runtime,
            dependencies=dependencies,
        )
        return _freeze_wheels(project_root, installed)


def _freeze_wheels(project_root: Path, wheels: Sequence[CachedWheel]) -> tuple[CachedWheel, ...]:
    """Publish immutable Wheel objects while the cache entry lock is still held."""
    result = []
    for wheel in wheels:
        directory = project_root / ".blex" / "wheel-objects" / wheel.sha256
        ensure_managed_directory(project_root, directory)
        lock = project_root / ".blex" / "locks" / "wheel-objects" / f"{wheel.sha256}.lock"
        ensure_managed_directory(project_root, lock.parent)
        target = directory / wheel.filename
        with FileLock(lock, "Wheel object"):
            if is_symlink_or_reparse(target):
                raise BlexError(f"immutable Wheel object must not be a symlink: {target}")
            if target.exists():
                if not target.is_file() or sha256_file(target) != wheel.sha256:
                    raise BlexError(f"immutable Wheel object is corrupt: {target}")
            else:
                temporary = directory / f".tmp-{uuid.uuid4().hex}"
                try:
                    shutil.copy2(wheel.path, temporary)
                    if sha256_file(temporary) != wheel.sha256:
                        raise BlexError(
                            f"Wheel changed while creating immutable object: {wheel.path}"
                        )
                    temporary.rename(target)
                finally:
                    temporary.unlink(missing_ok=True)
        result.append(CachedWheel(target, wheel.filename, wheel.sha256))
    return tuple(result)


def _rollback_cache_replacement(
    *,
    project_root: Path,
    entry: Path,
    temporary: Path,
    backup: Path | None,
    had_entry: bool,
) -> None:
    """Restore the pre-update cache state from the paths that actually exist."""

    entry_exists = entry.exists() or is_symlink_or_reparse(entry)
    backup_exists = backup is not None and (backup.exists() or is_symlink_or_reparse(backup))
    if backup_exists:
        if entry_exists:
            if temporary.exists() or is_symlink_or_reparse(temporary):
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
        if temporary.exists() or is_symlink_or_reparse(temporary):
            raise BlexError(
                "cannot roll back Wheel cache update: both the installed entry "
                f"and temporary path exist: {entry}, {temporary}"
            )
        entry.rename(temporary)

    if temporary.exists() or is_symlink_or_reparse(temporary):
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
