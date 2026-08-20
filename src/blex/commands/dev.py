from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import stat
import sys
import uuid
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, BinaryIO, Self

from tomlkit import dumps

from blex.adapters.blender import (
    blender_command,
    format_command,
    resolve_blender_executable,
    run_blender,
)
from blex.adapters.pip import validate_pip_executable_value
from blex.core.errors import BlexError
from blex.core.manifest import ManifestData, load_manifest, update_manifest_for_target
from blex.core.project import (
    derive_names,
    load_pyproject,
    normalize_python_version,
    read_python_version,
    resolve_project_root,
    resolve_source_dir,
    validate_source_tree,
)
from blex.core.wheels import WheelArtifact, current_platform, resolve_wheels


_DEBUGPY_VERSION = "1.8.21"
_DEBUGPY_WHEEL = "debugpy-1.8.21-py2.py3-none-any.whl"
_DEBUGPY_SHA256 = "b1e37d333663c8851516a47364ef473da127f9caebe4417e6df6f5825a7e9a92"
_REPOSITORY_MODULE = "blex_dev"
_BLENDER_ENVIRONMENT_OVERRIDES = (
    "BLENDER_USER_CONFIG",
    "BLENDER_USER_SCRIPTS",
    "BLENDER_USER_EXTENSIONS",
    "BLENDER_USER_DATAFILES",
)


class _SymlinkFailure(BlexError):
    pass


@dataclass(frozen=True)
class _RuntimeInputs:
    project_root: Path
    blex_root: Path
    source_dir: Path
    manifest: ManifestData
    platform: str
    artifacts: tuple[WheelArtifact, ...]
    seed: Path | None


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _is_link_like(path: Path) -> bool:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(path_stat.st_mode):
        return True
    attributes = getattr(path_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _strict_descendant(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _managed_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _validate_managed_directory(path: Path, *, label: str) -> None:
    if not _lexists(path):
        return
    if _is_link_like(path):
        raise BlexError(f"{label} must not be a symlink or reparse point: {path}")
    if not path.is_dir():
        raise BlexError(f"{label} is not a directory: {path}")


def _safe_remove_tree(path: Path, *, managed_root: Path) -> None:
    if not _lexists(path):
        return
    absolute_path = _managed_absolute(path)
    absolute_root = _managed_absolute(managed_root)
    if not _strict_descendant(absolute_path, absolute_root):
        raise BlexError(f"refusing to remove path outside managed root: {absolute_path}")
    _validate_managed_directory(path, label="managed removal target")
    shutil.rmtree(path)


def _ensure_final_lf(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.rstrip("\n") + "\n"


def _temporary_path(parent: Path) -> Path:
    return parent / f".tmp-{uuid.uuid4().hex}"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path.parent)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_ensure_final_lf(text))
        os.replace(temporary, path)
    finally:
        if _lexists(temporary):
            temporary.unlink()


def _ensure_blex_gitignore(blex_root: Path) -> None:
    gitignore = blex_root / ".gitignore"
    required = ("*", "!.gitignore")
    if not gitignore.exists():
        _atomic_write_text(gitignore, "\n".join(required))
        return
    if _is_link_like(gitignore) or not gitignore.is_file():
        raise BlexError(f"managed .gitignore is not a regular file: {gitignore}")
    try:
        original = gitignore.read_bytes()
        decoded = original.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BlexError(f"could not read managed .gitignore: {gitignore}") from exc
    lines = decoded.splitlines()
    missing = [pattern for pattern in required if pattern not in lines]
    if not missing:
        return
    separator = b"" if not original or original.endswith((b"\n", b"\r")) else b"\n"
    updated = original + separator + ("\n".join(missing) + "\n").encode()
    temporary = _temporary_path(gitignore.parent)
    try:
        temporary.write_bytes(updated)
        os.replace(temporary, gitignore)
    finally:
        if _lexists(temporary):
            temporary.unlink()


def _ensure_managed_root(project_root: Path) -> Path:
    blex_root = project_root / ".blex"
    _validate_managed_directory(blex_root, label="managed .blex directory")
    blex_root.mkdir(exist_ok=True)
    _ensure_blex_gitignore(blex_root)
    return blex_root


class _DevLock:
    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._stream: BinaryIO | None = None
        self._locking_module: Any | None = None

    def __enter__(self) -> Self:
        blex_root = _ensure_managed_root(self._project_root)
        locks = blex_root / "locks"
        _validate_managed_directory(locks, label="managed lock directory")
        locks.mkdir(exist_ok=True)
        lock_path = locks / "dev.lock"
        if _lexists(lock_path) and _is_link_like(lock_path):
            raise BlexError(f"dev lock must not be a symlink or reparse point: {lock_path}")
        stream = lock_path.open("a+b")
        self._stream = stream
        try:
            if os.name == "nt":
                locking_module: Any = importlib.import_module("msvcrt")
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                locking_module.locking(stream.fileno(), locking_module.LK_NBLCK, 1)
            else:
                locking_module = importlib.import_module("fcntl")
                locking_module.flock(
                    stream.fileno(), locking_module.LOCK_EX | locking_module.LOCK_NB
                )
        except OSError as exc:
            stream.close()
            self._stream = None
            raise BlexError(
                f"another blex dev process holds the project lock: {lock_path}"
            ) from exc
        self._locking_module = locking_module
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        stream = self._stream
        locking_module = self._locking_module
        if stream is None or locking_module is None:
            return
        try:
            if os.name == "nt":
                stream.seek(0)
                locking_module.locking(stream.fileno(), locking_module.LK_UNLCK, 1)
            else:
                locking_module.flock(stream.fileno(), locking_module.LOCK_UN)
        finally:
            stream.close()
            self._stream = None
            self._locking_module = None


def _resolve_input_path(
    project_root: Path,
    value: str,
    *,
    label: str,
    directory: bool,
) -> Path:
    if not value or "\x00" in value:
        raise BlexError(f"{label} must be a non-empty path without NUL")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BlexError(f"{label} does not exist: {candidate}") from exc
    if directory:
        if not resolved.is_dir():
            raise BlexError(f"{label} is not a directory: {resolved}")
    elif not resolved.is_file():
        raise BlexError(f"{label} is not a regular file: {resolved}")
    return resolved


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or _strict_descendant(first, second) or _strict_descendant(second, first)


def _validate_seed(seed: Path | None, runtime_root: Path) -> None:
    if seed is None:
        return
    runtime_location = _managed_absolute(runtime_root)
    if runtime_root.exists():
        runtime_location = runtime_root.resolve(strict=True)
    if _paths_overlap(seed, runtime_location):
        raise BlexError(
            f"user resources seed and managed runtime must not contain each other: "
            f"{seed}, {runtime_root}"
        )


def _copy_dereferenced(
    source: Path,
    destination: Path,
    *,
    active_directories: tuple[Path, ...] = (),
) -> None:
    try:
        resolved = source.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BlexError(f"broken link or unreadable copy source: {source}") from exc

    if resolved.is_dir():
        if resolved in active_directories:
            raise BlexError(f"directory copy cycle detected through: {source}")
        destination.mkdir()
        next_active = (*active_directories, resolved)
        try:
            children = sorted(resolved.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            raise BlexError(f"could not enumerate directory: {resolved}") from exc
        for child in children:
            _copy_dereferenced(
                child,
                destination / child.name,
                active_directories=next_active,
            )
        try:
            shutil.copystat(resolved, destination, follow_symlinks=True)
        except OSError as exc:
            raise BlexError(f"could not preserve directory metadata: {resolved}") from exc
        return

    if resolved.is_file():
        try:
            shutil.copy2(resolved, destination, follow_symlinks=True)
        except OSError as exc:
            raise BlexError(f"could not copy file: {resolved}") from exc
        return
    raise BlexError(f"copy source is not a regular file or directory: {source}")


def _create_symlink(source: Path, destination: Path) -> None:
    try:
        destination.symlink_to(source, target_is_directory=source.is_dir())
    except OSError as exc:
        raise _SymlinkFailure(f"could not create symlink {destination}: {exc}") from exc


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BlexError(f"could not hash file: {path}") from exc
    return digest.hexdigest()


def _place_source_entry(source: Path, destination: Path, *, mode: str) -> None:
    if mode == "symlink":
        _create_symlink(source, destination)
        return
    if mode == "copy":
        _copy_dereferenced(source, destination)
        return
    raise AssertionError(f"unexpected deployment mode: {mode}")


def _artifact_destination(extension_root: Path, relative_path: PurePosixPath) -> Path:
    if relative_path.is_absolute() or not relative_path.parts:
        raise BlexError(f"invalid Wheel deployment path: {relative_path}")
    if any(part in ("", ".", "..") for part in relative_path.parts):
        raise BlexError(f"invalid Wheel deployment path: {relative_path}")
    return extension_root.joinpath(*relative_path.parts)


def _ensure_real_parents(path: Path, *, stop: Path) -> None:
    missing: list[Path] = []
    current = path
    while current != stop and not _lexists(current):
        missing.append(current)
        current = current.parent
    if current != stop:
        if _is_link_like(current):
            raise BlexError(f"Wheel destination parent is a symlink: {current}")
        if not current.is_dir():
            raise BlexError(f"Wheel destination parent is not a directory: {current}")
    for directory in reversed(missing):
        directory.mkdir()


def _place_artifact(
    artifact: WheelArtifact,
    extension_root: Path,
    *,
    mode: str,
) -> None:
    destination = _artifact_destination(extension_root, artifact.relative_path)
    if _lexists(destination):
        if not destination.is_file() or _hash_file(destination) != artifact.sha256:
            raise BlexError(f"Wheel conflicts with deployed source entry: {artifact.relative_path}")
        return
    _ensure_real_parents(destination.parent, stop=extension_root)
    if mode == "symlink":
        _create_symlink(artifact.source, destination)
    elif mode == "copy":
        try:
            shutil.copy2(artifact.source, destination, follow_symlinks=True)
        except OSError as exc:
            raise BlexError(f"could not copy Wheel: {artifact.source}") from exc
    else:
        raise AssertionError(f"unexpected deployment mode: {mode}")


def _write_generated_manifest(inputs: _RuntimeInputs, extension_root: Path) -> None:
    wheel_entries = tuple(artifact.manifest_entry for artifact in inputs.artifacts)
    generated = update_manifest_for_target(
        inputs.manifest.document,
        platforms=(inputs.platform,),
        wheels=wheel_entries,
    )
    _atomic_write_text(extension_root / "blender_manifest.toml", dumps(generated))


def _runtime_stage_path(blex_root: Path) -> Path:
    return _temporary_path(blex_root)


def _build_runtime_stage(inputs: _RuntimeInputs, *, mode: str) -> Path:
    stage = _runtime_stage_path(inputs.blex_root)
    try:
        if inputs.seed is None:
            stage.mkdir()
        else:
            _copy_dereferenced(inputs.seed, stage)

        repository_root = stage / "extensions" / _REPOSITORY_MODULE
        if _lexists(repository_root):
            raise BlexError(
                f"user resources seed already contains reserved repository: {repository_root}"
            )
        extension_root = repository_root / inputs.manifest.extension_id
        extension_root.mkdir(parents=True)
        (extension_root / "wheels").mkdir()

        for source_entry in sorted(inputs.source_dir.iterdir(), key=lambda path: path.name):
            if source_entry.name == "blender_manifest.toml":
                continue
            if source_entry.name == "wheels":
                if not source_entry.is_dir():
                    raise BlexError(f"source wheels entry is not a directory: {source_entry}")
                continue
            _place_source_entry(
                source_entry,
                extension_root / source_entry.name,
                mode=mode,
            )

        for artifact in inputs.artifacts:
            _place_artifact(artifact, extension_root, mode=mode)
        _write_generated_manifest(inputs, extension_root)
        return stage
    except BaseException:
        if _lexists(stage):
            _safe_remove_tree(stage, managed_root=inputs.blex_root)
        raise


def _prepare_runtime(inputs: _RuntimeInputs, *, link_mode: str) -> Path:
    if link_mode == "auto":
        try:
            return _build_runtime_stage(inputs, mode="symlink")
        except _SymlinkFailure as exc:
            print(
                f"warning: symlink deployment failed; rebuilding runtime with copy: {exc}",
                file=sys.stderr,
            )
            return _build_runtime_stage(inputs, mode="copy")
    if link_mode == "symlink":
        try:
            return _build_runtime_stage(inputs, mode="symlink")
        except _SymlinkFailure as exc:
            raise BlexError(str(exc)) from exc
    if link_mode == "copy":
        return _build_runtime_stage(inputs, mode="copy")
    raise BlexError(f"invalid dev link mode: {link_mode!r}")


def _install_runtime(stage: Path, runtime_root: Path, *, blex_root: Path) -> None:
    backup = blex_root / f".backup-{uuid.uuid4().hex}"
    had_runtime = _lexists(runtime_root)
    if had_runtime:
        _validate_managed_directory(runtime_root, label="managed runtime directory")
    try:
        if had_runtime:
            os.replace(runtime_root, backup)
        os.replace(stage, runtime_root)
    except BaseException as exc:
        try:
            _rollback_runtime_install(
                stage=stage,
                runtime_root=runtime_root,
                backup=backup,
                blex_root=blex_root,
                had_runtime=had_runtime,
            )
        except Exception as rollback_exc:
            raise BlexError(
                f"runtime replacement and rollback both failed: {rollback_exc}"
            ) from exc
        if isinstance(exc, OSError):
            raise BlexError(f"could not replace managed runtime: {exc}") from exc
        raise

    if had_runtime:
        try:
            _safe_remove_tree(backup, managed_root=blex_root)
        except Exception as exc:
            raise BlexError(
                "managed runtime was installed, but its previous backup could not "
                f"be removed: {backup}: {exc}"
            ) from exc


def _rollback_runtime_install(
    *,
    stage: Path,
    runtime_root: Path,
    backup: Path,
    blex_root: Path,
    had_runtime: bool,
) -> None:
    """Restore the runtime state that existed before the replacement began."""

    runtime_exists = _lexists(runtime_root)
    backup_exists = _lexists(backup)
    if backup_exists:
        if runtime_exists:
            if _lexists(stage):
                raise BlexError(
                    "cannot roll back runtime replacement: both the installed runtime "
                    f"and stage path exist: {runtime_root}, {stage}"
                )
            os.replace(runtime_root, stage)
        os.replace(backup, runtime_root)
    elif had_runtime:
        if not runtime_exists:
            raise BlexError(
                "cannot roll back runtime replacement: both the original runtime "
                f"and its backup are missing: {runtime_root}"
            )
    elif runtime_exists:
        if _lexists(stage):
            raise BlexError(
                "cannot roll back runtime replacement: both the installed runtime "
                f"and stage path exist: {runtime_root}, {stage}"
            )
        os.replace(runtime_root, stage)

    if _lexists(stage):
        _safe_remove_tree(stage, managed_root=blex_root)


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_archive_path(name: str, *, directory: bool) -> PurePosixPath:
    raw = name[:-1] if directory and name.endswith("/") else name
    segments = raw.split("/")
    if (
        not raw
        or "\\" in raw
        or "\0" in raw
        or any(segment in ("", ".", "..") or ":" in segment for segment in segments)
    ):
        raise BlexError(f"unsafe path in bundled debugpy Wheel: {name!r}")
    relative = PurePosixPath(*segments)
    if relative.is_absolute():
        raise BlexError(f"unsafe path in bundled debugpy Wheel: {name!r}")
    return relative


def _iter_tree_files(root: Path) -> Iterator[tuple[str, Path]]:
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name, reverse=True)
        except OSError as exc:
            raise BlexError(f"could not inspect debugpy directory: {directory}") from exc
        for entry in entries:
            path = Path(entry.path)
            if _is_link_like(path):
                raise BlexError(f"debugpy extraction contains a symlink or reparse point: {path}")
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                relative = path.relative_to(root).as_posix()
                if relative != ".blex-metadata.json":
                    yield relative, path
            else:
                raise BlexError(f"debugpy extraction contains a special file: {path}")


def _validate_debugpy_install(target: Path) -> None:
    _validate_managed_directory(target, label="debugpy extraction directory")
    marker = target / ".blex-metadata.json"
    if not marker.is_file() or _is_link_like(marker):
        raise BlexError(f"debugpy extraction metadata is missing or invalid: {marker}")
    try:
        metadata: object = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BlexError(f"debugpy extraction metadata is corrupt: {marker}") from exc
    if not isinstance(metadata, dict) or set(metadata) != {
        "format",
        "version",
        "artifact_sha256",
        "files",
    }:
        raise BlexError(f"debugpy extraction metadata has an invalid schema: {marker}")
    if (
        type(metadata["format"]) is not int
        or metadata["format"] != 1
        or metadata["version"] != _DEBUGPY_VERSION
        or metadata["artifact_sha256"] != _DEBUGPY_SHA256
        or not isinstance(metadata["files"], list)
    ):
        raise BlexError(
            f"debugpy extraction metadata does not match the bundled artifact: {marker}"
        )

    expected: dict[str, str] = {}
    for item in metadata["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise BlexError(f"debugpy extraction metadata contains an invalid file entry: {marker}")
        path_value = item["path"]
        digest_value = item["sha256"]
        if not isinstance(path_value, str) or not _valid_sha256(digest_value):
            raise BlexError(f"debugpy extraction metadata contains an invalid file entry: {marker}")
        safe_path = _safe_archive_path(path_value, directory=False).as_posix()
        if safe_path in expected:
            raise BlexError(f"debugpy extraction metadata contains duplicate paths: {safe_path}")
        expected[safe_path] = digest_value
    if list(expected) != sorted(expected):
        raise BlexError(f"debugpy extraction metadata file inventory is not sorted: {marker}")

    actual = {relative: path for relative, path in _iter_tree_files(target)}
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        additional = sorted(set(actual) - set(expected))
        raise BlexError(
            f"debugpy extraction file inventory differs from metadata; "
            f"missing={missing!r}, additional={additional!r}"
        )
    for relative, path in actual.items():
        if _hash_file(path) != expected[relative]:
            raise BlexError(f"debugpy extraction file hash mismatch: {relative}")


def _extract_debugpy_wheel(wheel: Path, destination: Path) -> list[dict[str, str]]:
    inventory: list[dict[str, str]] = []
    normalized_destinations: set[str] = set()
    try:
        archive = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BlexError(f"bundled debugpy Wheel is not a valid archive: {wheel}") from exc
    with archive:
        for information in archive.infolist():
            is_directory = information.is_dir()
            relative = _safe_archive_path(information.filename, directory=is_directory)
            if relative.as_posix() == ".blex-metadata.json":
                raise BlexError("bundled debugpy Wheel conflicts with extraction metadata")
            output = destination.joinpath(*relative.parts)
            output_key = os.path.normcase(str(output))
            if output_key in normalized_destinations:
                raise BlexError(
                    f"bundled debugpy Wheel contains duplicate output path: {information.filename!r}"
                )
            normalized_destinations.add(output_key)

            member_mode = information.external_attr >> 16
            member_type = stat.S_IFMT(member_mode)
            if member_type == stat.S_IFLNK:
                raise BlexError(
                    f"bundled debugpy Wheel contains a symbolic link: {information.filename!r}"
                )
            if member_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise BlexError(
                    f"bundled debugpy Wheel contains a special file: {information.filename!r}"
                )

            if is_directory:
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            try:
                with archive.open(information, "r") as source, output.open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                        digest.update(chunk)
            except OSError as exc:
                raise BlexError(
                    f"could not extract bundled debugpy file: {information.filename}"
                ) from exc
            inventory.append({"path": relative.as_posix(), "sha256": digest.hexdigest()})
    inventory.sort(key=lambda item: item["path"])
    return inventory


def _debugpy_resource() -> Any:
    return resources.files("blex").joinpath("vendor", _DEBUGPY_WHEEL)


def _prepare_debugpy(blex_root: Path) -> Path:
    debugpy_root = blex_root / "debugpy"
    _validate_managed_directory(debugpy_root, label="managed debugpy directory")
    debugpy_root.mkdir(exist_ok=True)
    target = debugpy_root / _DEBUGPY_VERSION
    resource = _debugpy_resource()
    if resource.name != _DEBUGPY_WHEEL:
        raise BlexError(f"unexpected bundled debugpy artifact: {resource.name}")
    with resources.as_file(resource) as wheel:
        if _hash_file(wheel) != _DEBUGPY_SHA256:
            raise BlexError(f"bundled debugpy Wheel hash mismatch: {wheel}")
        if _lexists(target):
            _validate_debugpy_install(target)
            return target

        temporary = debugpy_root / f".{_DEBUGPY_VERSION}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.mkdir()
            inventory = _extract_debugpy_wheel(wheel, temporary)
            metadata = {
                "format": 1,
                "version": _DEBUGPY_VERSION,
                "artifact_sha256": _DEBUGPY_SHA256,
                "files": inventory,
            }
            _atomic_write_text(
                temporary / ".blex-metadata.json",
                json.dumps(metadata, ensure_ascii=False, indent=2),
            )
            os.replace(temporary, target)
        except BaseException:
            if _lexists(temporary):
                _safe_remove_tree(temporary, managed_root=debugpy_root)
            raise
    return target


def _render_bootstrap(
    *,
    manifest_id: str,
    repository_root: Path,
    reload_panel: bool,
    debugpy_root: Path | None,
    debugpy_host: str,
    debugpy_port: int,
) -> str:
    template = resources.files("blex").joinpath("templates", "bootstrap", "dev.py")
    source = template.read_text(encoding="utf-8")
    replacements: Mapping[str, str] = {
        "manifest-id": repr(manifest_id),
        "repository-root": repr(str(repository_root)),
        "reload-panel": repr(reload_panel),
        "debugpy-enabled": repr(debugpy_root is not None),
        "debugpy-root": repr(str(debugpy_root) if debugpy_root is not None else ""),
        "debugpy-host": repr(debugpy_host),
        "debugpy-port": repr(debugpy_port),
    }
    counts = dict.fromkeys(replacements, 0)
    rendered: list[str] = []
    for line in source.splitlines():
        marker_prefix = "# BLEX-CONFIG "
        if marker_prefix not in line:
            rendered.append(line)
            continue
        marker = line.rsplit(marker_prefix, 1)[1]
        if marker not in replacements:
            raise BlexError(f"unknown bootstrap configuration marker: {marker}")
        left = line.split("=", 1)[0]
        rendered.append(f"{left}= {replacements[marker]}  {marker_prefix}{marker}")
        counts[marker] += 1
    invalid_counts = {name: count for name, count in counts.items() if count != 1}
    if invalid_counts:
        raise BlexError(f"bootstrap template configuration markers are invalid: {invalid_counts!r}")
    return "\n".join(rendered)


def _write_bootstrap(
    blex_root: Path,
    *,
    manifest_id: str,
    runtime_root: Path,
    reload_panel: bool,
    debugpy_root: Path | None,
    debugpy_host: str,
    debugpy_port: int,
) -> Path:
    bootstrap_directory = blex_root / "bootstrap"
    _validate_managed_directory(bootstrap_directory, label="managed bootstrap directory")
    bootstrap_directory.mkdir(exist_ok=True)
    script = bootstrap_directory / "dev.py"
    if _lexists(script) and (_is_link_like(script) or not script.is_file()):
        raise BlexError(f"managed bootstrap script is not a regular file: {script}")
    repository_root = runtime_root / "extensions" / _REPOSITORY_MODULE
    rendered = _render_bootstrap(
        manifest_id=manifest_id,
        repository_root=repository_root,
        reload_panel=reload_panel,
        debugpy_root=debugpy_root,
        debugpy_host=debugpy_host,
        debugpy_port=debugpy_port,
    )
    _atomic_write_text(script, rendered)
    return script.resolve(strict=True)


def _selected(cli_value: Any | None, configured_value: Any | None, default: Any) -> Any:
    if cli_value is not None:
        return cli_value
    if configured_value is not None:
        return configured_value
    return default


def _run_locked(args: argparse.Namespace, project_root: Path) -> int:
    if any("\x00" in argument for argument in args.blender_args):
        raise BlexError("Blender arguments must not contain NUL")
    project = load_pyproject(project_root)
    config = project.config

    source_value = args.source_dir
    if source_value is None:
        source_value = config.source_dir
    if source_value is None:
        source_value = f"src/{derive_names(project.name).manifest_id}"
    source_dir = resolve_source_dir(project_root, source_value)
    if not source_dir.is_dir():
        raise BlexError(f"source directory does not exist: {source_dir}")
    validate_source_tree(source_dir)
    manifest = load_manifest(source_dir)

    platform_name = current_platform()
    link_mode: str = _selected(args.link_mode, config.dev.link_mode, "auto")
    reload_panel: bool = _selected(args.reload_panel, config.dev.reload_panel, True)
    debugpy_enabled: bool = _selected(args.debugpy, config.dev.debugpy, False)
    debugpy_host: str = _selected(args.debugpy_host, config.dev.debugpy_host, "127.0.0.1")
    debugpy_port: int = _selected(args.debugpy_port, config.dev.debugpy_port, 5678)
    automatic_wheels: bool = _selected(args.wheels, config.dev.wheels, True)
    if not debugpy_host.strip():
        raise BlexError("debugpy host must not be empty")
    if type(debugpy_port) is not int or not 1 <= debugpy_port <= 65535:
        raise BlexError("debugpy port must be an integer from 1 through 65535")
    if not debugpy_enabled and (args.debugpy_host is not None or args.debugpy_port is not None):
        raise BlexError("debugpy host or port was specified while debugpy is disabled")
    if not automatic_wheels and args.refresh_wheels:
        raise BlexError("--refresh-wheels cannot be used while automatic Wheels are disabled")

    blend_file: Path | None = None
    blend_value = _selected(args.blend_file, config.dev.blend_file, None)
    if blend_value is not None:
        blend_file = _resolve_input_path(
            project_root,
            blend_value,
            label="Blend file",
            directory=False,
        )
    seed: Path | None = None
    seed_value = _selected(args.user_resources, config.dev.user_resources, None)
    if seed_value is not None:
        seed = _resolve_input_path(
            project_root,
            seed_value,
            label="user resources seed",
            directory=True,
        )

    blender_value: str = _selected(args.blender, config.blender, "blender")
    if not blender_value:
        raise BlexError("Blender executable must not be empty")
    blender = resolve_blender_executable(blender_value, project_root)

    python_value = _selected(args.python, config.dev.python, None)
    python_version: str | None
    if automatic_wheels:
        if python_value is None:
            python_version = read_python_version(project_root)
        else:
            python_version = normalize_python_version(python_value)
    else:
        python_version = (
            normalize_python_version(python_value) if python_value is not None else None
        )
    pip_value = validate_pip_executable_value(_selected(args.pip, config.pip, "pip"))
    artifacts = resolve_wheels(
        project_root=project_root,
        source_dir=source_dir,
        manifest=manifest.document,
        dependencies=project.dependencies,
        python_version=python_version,
        platforms=(platform_name,),
        automatic=automatic_wheels,
        pip_command=pip_value,
        refresh=args.refresh_wheels,
        verbose=args.verbose,
    )

    blex_root = _ensure_managed_root(project_root)
    runtime_root = blex_root / "runtime_environment"
    _validate_managed_directory(runtime_root, label="managed runtime directory")
    _validate_seed(seed, runtime_root)

    debugpy_root = _prepare_debugpy(blex_root) if debugpy_enabled else None
    runtime_inputs = _RuntimeInputs(
        project_root=project_root,
        blex_root=blex_root,
        source_dir=source_dir,
        manifest=manifest,
        platform=platform_name,
        artifacts=artifacts,
        seed=seed,
    )
    runtime_stage = _prepare_runtime(runtime_inputs, link_mode=link_mode)
    _install_runtime(runtime_stage, runtime_root, blex_root=blex_root)
    bootstrap = _write_bootstrap(
        blex_root,
        manifest_id=manifest.extension_id,
        runtime_root=runtime_root,
        reload_panel=reload_panel,
        debugpy_root=debugpy_root,
        debugpy_host=debugpy_host,
        debugpy_port=debugpy_port,
    )

    environment = dict(os.environ)
    environment["BLENDER_USER_RESOURCES"] = str(runtime_root.resolve(strict=True))
    for variable in _BLENDER_ENVIRONMENT_OVERRIDES:
        environment.pop(variable, None)
    command = blender_command(
        blender,
        blend_file=blend_file,
        bootstrap_script=bootstrap,
        extra_arguments=tuple(args.blender_args),
    )
    if args.verbose:
        print(f"project root: {project_root}")
        print(f"source directory: {source_dir}")
        print(f"runtime directory: {runtime_root}")
        if seed is not None:
            print(f"user resources seed: {seed}")
        if debugpy_root is not None:
            print(f"debugpy directory: {debugpy_root}")
        print(f"Blender command: {format_command(command)}")
    return run_blender(command, environment=environment)


def run(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.directory, must_exist=True)
        with _DevLock(project_root):
            return _run_locked(args, project_root)
    except BlexError:
        raise
    except (OSError, UnicodeError, shutil.Error) as exc:
        raise BlexError(f"dev filesystem operation failed: {exc}") from exc
