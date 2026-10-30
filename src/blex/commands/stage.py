"""Implementation of ``blex stage``."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any
import uuid

import tomlkit

from blex.adapters.pip import validate_pip_executable_value
from blex.core.cache import FileLock, sha256_file
from blex.core.config import select_value
from blex.core.errors import BlexError
from blex.core.manifest import load_manifest, update_manifest_for_target
from blex.core.project import (
    derive_names,
    is_strict_descendant,
    is_symlink_or_reparse,
    load_pyproject,
    read_python_version,
    resolve_project_path,
    resolve_project_root,
    resolve_source_dir,
    validate_source_tree,
    validate_executable_value,
)
from blex.core.wheels import (
    WheelArtifact,
    current_platform,
    ensure_blex_root,
    ensure_managed_directory,
    normalize_platforms,
    normalize_python_versions,
    remove_managed_tree,
    resolve_wheels,
    stage_id,
)


@dataclass
class _TargetState:
    python: str
    identifier: str
    target: Path
    prepared: Path
    backup: Path
    had_target: bool = False
    state: str = "prepared"


def run(args: argparse.Namespace) -> int:
    """Create all selected Python staging trees as one transaction."""

    root = resolve_project_root(args.directory, must_exist=True)
    try:
        ensure_blex_root(root)
        lock_path = root / ".blex" / "locks" / "stage.lock"
        ensure_managed_directory(root, lock_path.parent)
        with FileLock(lock_path, "stage"):
            _run_locked(args, root)
    except BlexError:
        raise
    except (OSError, UnicodeError, shutil.Error) as error:
        raise BlexError(f"stage filesystem operation failed: {error}") from error
    return 0


def _run_locked(args: argparse.Namespace, root: Path) -> None:
    staging_parent = root / ".blex" / "staging" / "stage"
    ensure_managed_directory(root, staging_parent)
    abandoned = sorted(staging_parent.iterdir(), key=lambda path: path.name)
    if abandoned:
        paths = ", ".join(str(path) for path in abandoned)
        raise BlexError(f"abandoned stage transaction exists: {paths}")

    project = load_pyproject(root)
    source_value = args.source_dir
    if source_value is None:
        source_value = project.config.source_dir
    if source_value is None:
        source_value = f"src/{derive_names(project.name).manifest_id}"
    source_dir = resolve_source_dir(root, source_value)
    if not source_dir.is_dir():
        raise BlexError(f"source-dir does not exist: {source_dir}")
    validate_source_tree(source_dir)
    manifest = load_manifest(source_dir)

    automatic = select_value(args.wheels, project.config.stage.wheels, True)
    if not automatic and args.refresh_wheels:
        raise BlexError("--refresh-wheels cannot be used with --no-wheels")

    python_values = args.python if args.python is not None else project.config.stage.python
    if python_values is None:
        python_values = (read_python_version(root),)
    python_versions = normalize_python_versions(python_values)

    platform_values = args.platform if args.platform is not None else project.config.stage.platforms
    if platform_values is None:
        platform_values = (current_platform(),)
    platforms = normalize_platforms(platform_values)

    output_value = select_value(args.output_dir, project.config.stage.output_dir, "dist")
    output_dir = _resolve_output_dir(root, source_dir, output_value, python_versions)
    pip_command = validate_pip_executable_value(select_value(args.pip, project.config.pip, "pip"))
    uv_command = validate_executable_value(select_value(args.uv, project.config.uv, "uv"), "uv")
    verbose = bool(getattr(args, "verbose", False))
    if verbose:
        print(f"project root: {root}")
        print(f"source-dir: {source_dir}")
        print(f"output-dir: {output_dir}")
        print(f"Python targets: {', '.join(python_versions)}")
        print(f"Platforms: {', '.join(platforms)}")

    run_root = staging_parent / uuid.uuid4().hex
    states: list[_TargetState] = []
    created_output_directories: list[Path] = []
    committed = False
    try:
        run_root.mkdir()
        for python_version in python_versions:
            identifier = stage_id(python_version)
            prepared = run_root / "prepared" / identifier
            backup = run_root / "backups" / identifier
            target = output_dir / identifier
            for platform_name in platforms:
                artifacts = resolve_wheels(
                    project_root=root,
                    source_dir=source_dir,
                    manifest=manifest.document,
                    dependencies=project.dependencies,
                    python_version=python_version,
                    platform_name=platform_name,
                    automatic=automatic,
                    pip_command=pip_command,
                    uv_command=uv_command,
                    refresh=args.refresh_wheels,
                    verbose=verbose,
                )
                _prepare_target(
                    source_dir=source_dir,
                    prepared=prepared / platform_name,
                    manifest_document=manifest.document,
                    platforms=(platform_name,),
                    artifacts=artifacts,
                )
            states.append(
                _TargetState(
                    python=python_version,
                    identifier=identifier,
                    target=target,
                    prepared=prepared,
                    backup=backup,
                )
            )
        _write_transaction(run_root, states)
        _commit(root, output_dir, run_root, states, created_output_directories)
        committed = True
    except BaseException:
        if states and any(state.state != "prepared" for state in states):
            try:
                _rollback(root, run_root, states)
            except BaseException as rollback_error:
                raise BlexError(
                    f"stage commit failed and rollback was incomplete; "
                    f"transaction remains at {run_root}: {rollback_error}"
                ) from rollback_error
        _remove_empty_directories(created_output_directories)
        if run_root.exists():
            remove_managed_tree(root, run_root)
        raise

    if committed:
        for state in states:
            if state.backup.exists():
                remove_managed_tree(root, state.backup)
        if run_root.exists():
            remove_managed_tree(root, run_root)
        _remove_empty_directories(created_output_directories)
        for state in states:
            print(f"Built {state.identifier}: {state.target}")


def _prepare_target(
    *,
    source_dir: Path,
    prepared: Path,
    manifest_document: Any,
    platforms: tuple[str, ...],
    artifacts: tuple[WheelArtifact, ...],
) -> None:
    source_target = prepared / "source"
    prepared.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source_dir, source_target, symlinks=False, copy_function=shutil.copy2)
    except (OSError, shutil.Error) as error:
        raise BlexError(f"failed to copy Extension source into staging: {error}") from error

    wheels_directory = source_target / "wheels"
    if wheels_directory.exists() and not wheels_directory.is_dir():
        raise BlexError(f"Extension wheels path is not a directory: {wheels_directory}")
    wheels_directory.mkdir(exist_ok=True)

    for artifact in artifacts:
        destination = source_target.joinpath(*artifact.relative_path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if not destination.is_file() or destination.is_symlink():
                raise BlexError(f"Wheel destination conflicts with a source entry: {destination}")
            if sha256_file(destination) != artifact.sha256:
                raise BlexError(f"Wheel destination has conflicting content: {destination}")
            continue
        try:
            shutil.copy2(artifact.source, destination, follow_symlinks=True)
        except OSError as error:
            raise BlexError(f"failed to copy Wheel to {destination}: {error}") from error
        if sha256_file(destination) != artifact.sha256:
            raise BlexError(f"copied Wheel hash does not match its source: {destination}")

    generated_manifest = update_manifest_for_target(
        manifest_document,
        platforms=platforms,
        wheels=[artifact.manifest_entry for artifact in artifacts],
    )
    manifest_text = tomlkit.dumps(generated_manifest).replace("\r\n", "\n").replace("\r", "\n")
    if not manifest_text.endswith("\n"):
        manifest_text += "\n"
    (source_target / "blender_manifest.toml").write_text(
        manifest_text,
        encoding="utf-8",
        newline="\n",
    )


def _resolve_output_dir(
    root: Path,
    source_dir: Path,
    value: str,
    python_versions: tuple[str, ...],
) -> Path:
    if not value or "\x00" in value:
        raise BlexError("output-dir must be a non-empty path without NUL")
    raw = Path(value)
    lexical = raw if raw.is_absolute() else root / raw
    _validate_existing_components(lexical)
    output = resolve_project_path(root, value, "output-dir")
    if not is_strict_descendant(output, root):
        raise BlexError(f"output-dir must be a strict descendant of the project root: {output}")
    if output.exists() and not output.is_dir():
        raise BlexError(f"output-dir is not a directory: {output}")

    protected = (source_dir, root / ".blex", root / ".git")
    for protected_path in protected:
        protected_resolved = protected_path.resolve(strict=False)
        if (
            output == protected_resolved
            or is_strict_descendant(output, protected_resolved)
            or is_strict_descendant(protected_resolved, output)
        ):
            raise BlexError(f"output-dir overlaps protected path {protected_resolved}: {output}")

    for python_version in python_versions:
        target = output / stage_id(python_version)
        if is_symlink_or_reparse(target):
            raise BlexError(f"selected stage target must not be a symlink: {target}")
        if target.exists() and not target.is_dir():
            raise BlexError(f"selected stage target is not a directory: {target}")
    return output


def _validate_existing_components(path: Path) -> None:
    if path.is_absolute():
        current = Path(path.anchor)
        parts = path.parts[1:]
    else:
        current = Path.cwd()
        parts = path.parts
    for part in parts:
        if part in {"", "."}:
            continue
        if part == "..":
            current = current.parent
            continue
        current = current / part
        if is_symlink_or_reparse(current):
            raise BlexError(f"output-dir component must not be a symlink: {current}")
        if current.exists() and not current.is_dir() and current != path:
            raise BlexError(f"output-dir parent component is not a directory: {current}")


def _transaction_payload(run_root: Path, states: list[_TargetState]) -> dict[str, object]:
    return {
        "format": 1,
        "run_id": run_root.name,
        "targets": [
            {
                "python": state.python,
                "target": state.target.as_posix(),
                "prepared": state.prepared.as_posix(),
                "backup": state.backup.as_posix(),
                "had_target": state.had_target,
                "state": state.state,
            }
            for state in states
        ],
    }


def _write_transaction(run_root: Path, states: list[_TargetState]) -> None:
    path = run_root / "transaction.json"
    temporary = run_root / f".transaction-{uuid.uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(_transaction_payload(run_root, states), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _commit(
    root: Path,
    output_dir: Path,
    run_root: Path,
    states: list[_TargetState],
    created_output_directories: list[Path],
) -> None:
    _create_output_directory(output_dir, root, created_output_directories)
    for state in states:
        if is_symlink_or_reparse(state.target):
            raise BlexError(f"selected stage target became a symlink: {state.target}")
        state.had_target = state.target.exists()
        state.state = "backup_pending" if state.had_target else "install_pending"
        _write_transaction(run_root, states)
        if state.had_target:
            if not state.target.is_dir():
                raise BlexError(f"selected stage target is not a directory: {state.target}")
            state.backup.parent.mkdir(parents=True, exist_ok=True)
            state.target.rename(state.backup)
            state.state = "backed_up"
            _write_transaction(run_root, states)
            state.state = "install_pending"
            _write_transaction(run_root, states)
        state.prepared.rename(state.target)
        state.state = "installed"
        _write_transaction(run_root, states)


def _rollback(root: Path, run_root: Path, states: list[_TargetState]) -> None:
    failures: list[str] = []
    for state in reversed(states):
        try:
            if state.state == "prepared":
                state.state = "rolled_back"
                _write_transaction(run_root, states)
                continue
            target_exists = state.target.exists() or is_symlink_or_reparse(state.target)
            prepared_exists = state.prepared.exists() or is_symlink_or_reparse(state.prepared)
            backup_exists = state.backup.exists() or is_symlink_or_reparse(state.backup)

            if state.had_target:
                if backup_exists:
                    if is_symlink_or_reparse(state.backup) or not state.backup.is_dir():
                        raise BlexError(f"stage backup cannot be restored safely: {state.backup}")
                    if target_exists:
                        if prepared_exists:
                            raise BlexError(
                                f"rollback found both target and prepared paths: {state.target}"
                            )
                        if is_symlink_or_reparse(state.target) or not state.target.is_dir():
                            raise BlexError(
                                f"installed target cannot be rolled back safely: {state.target}"
                            )
                        state.prepared.parent.mkdir(parents=True, exist_ok=True)
                        state.target.rename(state.prepared)
                    state.backup.rename(state.target)
                else:
                    if not target_exists or is_symlink_or_reparse(state.target):
                        raise BlexError(f"original stage target is missing: {state.target}")
                    if not state.target.is_dir():
                        raise BlexError(f"original stage target is invalid: {state.target}")
                    if not prepared_exists:
                        raise BlexError(
                            f"prepared stage target is missing without a backup: {state.prepared}"
                        )
            else:
                if backup_exists:
                    raise BlexError(f"unexpected backup for a new stage target: {state.backup}")
                if target_exists:
                    if prepared_exists:
                        raise BlexError(
                            f"rollback found both new target and prepared paths: {state.target}"
                        )
                    if is_symlink_or_reparse(state.target) or not state.target.is_dir():
                        raise BlexError(
                            f"new stage target cannot be rolled back safely: {state.target}"
                        )
                    state.prepared.parent.mkdir(parents=True, exist_ok=True)
                    state.target.rename(state.prepared)
                elif not prepared_exists:
                    raise BlexError(f"new prepared stage target is missing: {state.prepared}")
            state.state = "rolled_back"
            _write_transaction(run_root, states)
        except (OSError, BlexError) as error:
            failures.append(f"{state.identifier}: {error}")
    if failures:
        raise BlexError("; ".join(failures))


def _create_output_directory(
    output_dir: Path, root: Path, created_output_directories: list[Path]
) -> None:
    missing: list[Path] = []
    current = output_dir
    while current != root and not current.exists():
        missing.append(current)
        current = current.parent
    if current != root and not current.is_dir():
        raise BlexError(f"output-dir parent is not a directory: {current}")
    created_output_directories.extend(missing)
    output_dir.mkdir(parents=True, exist_ok=True)
    _validate_existing_components(output_dir)


def _remove_empty_directories(directories: list[Path]) -> None:
    for directory in directories:
        if not directory.exists():
            continue
        try:
            directory.rmdir()
        except OSError:
            break
