from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import uuid
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from tomlkit import dumps

from blex.adapters.blender import (
    blender_command,
    format_command,
    resolve_blender_executable,
    run_blender,
)
from blex.core.cache import FileLock, sha256_file
from blex.core.config import select_value
from blex.core.environment import current_platform
from blex.core.filesystem import ensure_blex_root, ensure_managed_directory
from blex.core.errors import BlexError
from blex.core.manifest import ManifestData, load_manifest, update_manifest_for_target
from blex.core.project import (
    derive_names,
    is_strict_descendant,
    is_symlink_or_reparse,
    load_pyproject,
    resolve_project_root,
    resolve_source_dir,
    validate_source_tree,
)
from blex.core.wheels import WheelArtifact, resolve_wheels


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


def _managed_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _validate_managed_directory(path: Path, *, label: str) -> None:
    if not _lexists(path):
        return
    if is_symlink_or_reparse(path):
        raise BlexError(f"{label} must not be a symlink or reparse point: {path}")
    if not path.is_dir():
        raise BlexError(f"{label} is not a directory: {path}")


def _safe_remove_tree(path: Path, *, managed_root: Path) -> None:
    if not _lexists(path):
        return
    absolute_path = _managed_absolute(path)
    absolute_root = _managed_absolute(managed_root)
    if not is_strict_descendant(absolute_path, absolute_root):
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
    return (
        first == second
        or is_strict_descendant(first, second)
        or is_strict_descendant(second, first)
    )


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
        if is_symlink_or_reparse(current):
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
        if not destination.is_file() or sha256_file(destination) != artifact.sha256:
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


def _prepare_runtime(inputs: _RuntimeInputs, *, link_mode: str) -> tuple[Path, str]:
    if link_mode == "auto":
        try:
            return _build_runtime_stage(inputs, mode="symlink"), "symlink"
        except _SymlinkFailure as exc:
            print(
                f"warning: symlink deployment failed; rebuilding runtime with copy: {exc}",
                file=sys.stderr,
            )
            return _build_runtime_stage(inputs, mode="copy"), "copy"
    if link_mode == "symlink":
        try:
            return _build_runtime_stage(inputs, mode="symlink"), "symlink"
        except _SymlinkFailure as exc:
            raise BlexError(str(exc)) from exc
    if link_mode == "copy":
        return _build_runtime_stage(inputs, mode="copy"), "copy"
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


def _extract_debugpy_wheel(wheel: Path, destination: Path) -> None:
    normalized_destinations: set[str] = set()
    try:
        archive = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BlexError(f"bundled debugpy Wheel is not a valid archive: {wheel}") from exc
    with archive:
        for information in archive.infolist():
            is_directory = information.is_dir()
            relative = _safe_archive_path(information.filename, directory=is_directory)
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
            try:
                with archive.open(information, "r") as source, output.open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
            except OSError as exc:
                raise BlexError(
                    f"could not extract bundled debugpy file: {information.filename}"
                ) from exc


def _debugpy_resource() -> Any:
    return resources.files("blex").joinpath("vendor", _DEBUGPY_WHEEL)


def _prepare_debugpy(blex_root: Path) -> Path:
    debugpy_root = blex_root / "debugpy"
    _validate_managed_directory(debugpy_root, label="managed debugpy directory")
    debugpy_root.mkdir(exist_ok=True)
    target = debugpy_root / _DEBUGPY_VERSION
    _validate_managed_directory(target, label="debugpy extraction directory")
    if target.is_dir():
        return target

    resource = _debugpy_resource()
    if resource.name != _DEBUGPY_WHEEL:
        raise BlexError(f"unexpected bundled debugpy artifact: {resource.name}")
    with resources.as_file(resource) as wheel:
        if sha256_file(wheel) != _DEBUGPY_SHA256:
            raise BlexError(f"bundled debugpy Wheel hash mismatch: {wheel}")
        temporary = debugpy_root / f".{_DEBUGPY_VERSION}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.mkdir()
            _extract_debugpy_wheel(wheel, temporary)
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
    source_dir: Path,
    sync_source_links: bool,
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
        "source-dir": repr(str(source_dir)),
        "sync-source-links": repr(sync_source_links),
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
    source_dir: Path,
    sync_source_links: bool,
    reload_panel: bool,
    debugpy_root: Path | None,
    debugpy_host: str,
    debugpy_port: int,
) -> Path:
    bootstrap_directory = blex_root / "bootstrap"
    _validate_managed_directory(bootstrap_directory, label="managed bootstrap directory")
    bootstrap_directory.mkdir(exist_ok=True)
    script = bootstrap_directory / "dev.py"
    if _lexists(script) and (is_symlink_or_reparse(script) or not script.is_file()):
        raise BlexError(f"managed bootstrap script is not a regular file: {script}")
    repository_root = runtime_root / "extensions" / _REPOSITORY_MODULE
    rendered = _render_bootstrap(
        manifest_id=manifest_id,
        repository_root=repository_root,
        source_dir=source_dir,
        sync_source_links=sync_source_links,
        reload_panel=reload_panel,
        debugpy_root=debugpy_root,
        debugpy_host=debugpy_host,
        debugpy_port=debugpy_port,
    )
    _atomic_write_text(script, rendered)
    return script.resolve(strict=True)


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
    link_mode: str = select_value(args.link_mode, config.dev.link_mode, "auto")
    reload_panel: bool = select_value(args.reload_panel, config.dev.reload_panel, True)
    debugpy_enabled: bool = select_value(args.debugpy, config.dev.debugpy, False)
    debugpy_host: str = select_value(args.debugpy_host, config.dev.debugpy_host, "127.0.0.1")
    debugpy_port: int = select_value(args.debugpy_port, config.dev.debugpy_port, 5678)
    automatic_wheels: bool = select_value(args.wheels, config.dev.wheels, True)
    if not debugpy_host.strip():
        raise BlexError("debugpy host must not be empty")
    if type(debugpy_port) is not int or not 1 <= debugpy_port <= 65535:
        raise BlexError("debugpy port must be an integer from 1 through 65535")
    if not debugpy_enabled and (args.debugpy_host is not None or args.debugpy_port is not None):
        raise BlexError("debugpy host or port was specified while debugpy is disabled")
    if not automatic_wheels and args.refresh_wheels:
        raise BlexError("--refresh-wheels cannot be used while automatic Wheels are disabled")

    blend_file: Path | None = None
    blend_value = select_value(args.blend_file, config.dev.blend_file, None)
    if blend_value is not None:
        blend_file = _resolve_input_path(
            project_root,
            blend_value,
            label="Blend file",
            directory=False,
        )
    seed: Path | None = None
    seed_value = select_value(args.user_resources, config.dev.user_resources, None)
    if seed_value is not None:
        seed = _resolve_input_path(
            project_root,
            seed_value,
            label="user resources seed",
            directory=True,
        )

    blender_value: str = select_value(args.blender, config.blender, "blender")
    if not blender_value:
        raise BlexError("Blender executable must not be empty")
    blender = resolve_blender_executable(blender_value, project_root)

    artifacts = resolve_wheels(
        project_root=project_root,
        source_dir=source_dir,
        manifest=manifest.document,
        dependencies=project.dependencies,
        automatic=automatic_wheels,
        refresh=args.refresh_wheels,
        verbose=args.verbose,
    )

    blex_root = project_root / ".blex"
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
    runtime_stage, deployed_mode = _prepare_runtime(runtime_inputs, link_mode=link_mode)
    _install_runtime(runtime_stage, runtime_root, blex_root=blex_root)
    bootstrap = _write_bootstrap(
        blex_root,
        manifest_id=manifest.extension_id,
        runtime_root=runtime_root,
        source_dir=source_dir,
        sync_source_links=deployed_mode == "symlink",
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
        blex_root = ensure_blex_root(project_root)
        lock_path = blex_root / "locks" / "dev.lock"
        ensure_managed_directory(project_root, lock_path.parent)
        with FileLock(lock_path, "dev"):
            return _run_locked(args, project_root)
    except BlexError:
        raise
    except (OSError, UnicodeError, shutil.Error) as exc:
        raise BlexError(f"dev filesystem operation failed: {exc}") from exc
