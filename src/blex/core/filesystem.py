"""Shared filesystem operations for blex-managed project directories."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import uuid

from blex.core.errors import BlexError
from blex.core.project import is_symlink_or_reparse


def ensure_blex_root(project_root: Path) -> Path:
    """Create the managed root and merge its ignore file without following links."""
    blex_root = project_root / ".blex"
    if is_symlink_or_reparse(blex_root) or (blex_root.exists() and not blex_root.is_dir()):
        raise BlexError(f"managed .blex path must be a regular directory: {blex_root}")
    blex_root.mkdir(exist_ok=True)
    ignore_path = blex_root / ".gitignore"
    if is_symlink_or_reparse(ignore_path):
        raise BlexError(f"managed .blex ignore path must not be a symlink: {ignore_path}")
    lines: list[str]
    if ignore_path.exists():
        if not ignore_path.is_file():
            raise BlexError(f"managed .blex ignore path is invalid: {ignore_path}")
        try:
            original = ignore_path.read_bytes()
            lines = original.decode("utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            raise BlexError(f"cannot read managed ignore file: {ignore_path}") from error
    else:
        original = b""
        lines = []
    missing = [line for line in ("*", "!.gitignore") if line not in lines]
    if missing:
        separator = b"" if not original or original.endswith((b"\n", b"\r")) else b"\n"
        updated = original + separator + ("\n".join(missing) + "\n").encode("utf-8")
        temporary = blex_root / f".gitignore-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(updated)
            os.replace(temporary, ignore_path)
        finally:
            temporary.unlink(missing_ok=True)
    return blex_root


def ensure_managed_directory(project_root: Path, directory: Path) -> None:
    """Create a directory below ``.blex`` after validating each component."""
    managed_root = project_root / ".blex"
    try:
        relative = directory.relative_to(managed_root)
    except ValueError as error:
        raise BlexError(f"managed path escapes .blex: {directory}") from error
    if ".." in relative.parts:
        raise BlexError(f"managed path escapes .blex: {directory}")
    if is_symlink_or_reparse(managed_root) or not managed_root.is_dir():
        raise BlexError(f"managed .blex path must be a regular directory: {managed_root}")
    current = managed_root
    for part in relative.parts:
        current = current / part
        if is_symlink_or_reparse(current):
            raise BlexError(f"managed directory must not be a symlink: {current}")
        if current.exists() and not current.is_dir():
            raise BlexError(f"managed directory path is not a directory: {current}")
        current.mkdir(exist_ok=True)


def remove_managed_tree(project_root: Path, target: Path) -> None:
    """Remove a real entry only after proving its parent is below ``.blex``."""
    managed_root = project_root / ".blex"
    if is_symlink_or_reparse(managed_root):
        raise BlexError(f"managed .blex path must be a regular directory: {managed_root}")
    managed_root = managed_root.resolve(strict=True)
    try:
        resolved_parent = target.parent.resolve(strict=True)
        resolved_parent.relative_to(managed_root)
    except (OSError, RuntimeError, ValueError) as error:
        raise BlexError(f"refusing to remove path outside managed .blex root: {target}") from error
    if is_symlink_or_reparse(target):
        raise BlexError(f"refusing to recursively remove a managed symlink: {target}")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.is_file():
        target.unlink()
    else:
        raise BlexError(f"managed path is not a removable file or directory: {target}")
