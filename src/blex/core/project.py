"""Project naming, path, and ``pyproject.toml`` validation."""

from __future__ import annotations

import keyword
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import cast

import tomlkit
from tomlkit.exceptions import ParseError
from tomlkit.items import Item
from tomlkit.toml_document import TOMLDocument

from .config import BlexConfig, load_config
from .errors import ProjectError

_NAME_INPUT_RE = re.compile(r"^[A-Za-z0-9 _-]+$")
_PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_MANIFEST_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PYTHON_VERSION_RE = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)")
_WINDOWS_DRIVE_RELATIVE_RE = re.compile(r"^[A-Za-z]:[^\\/]")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


@dataclass(frozen=True)
class ProjectNames:
    """Names generated from the single user-facing name input."""

    project_name: str
    manifest_id: str
    display_name: str
    class_prefix: str


@dataclass(frozen=True)
class ProjectData:
    """Validated project metadata needed by ``dev`` and ``stage``."""

    root: Path
    path: Path
    document: TOMLDocument
    name: str
    dependencies: tuple[str, ...]
    config: BlexConfig


def derive_names(base: str) -> ProjectNames:
    """Generate all project and extension names from *base*."""

    if not isinstance(base, str):
        raise ProjectError("project name input must be a string")
    trimmed = base.strip()
    if not trimmed:
        raise ProjectError("project name input must not be empty")
    if _NAME_INPUT_RE.fullmatch(trimmed) is None:
        raise ProjectError(
            "project name input may contain only ASCII letters, digits, spaces, '-' and '_'"
        )

    project_name = re.sub(r"-+", "-", re.sub(r"[ _]", "-", trimmed.lower())).strip("-")
    if _PROJECT_NAME_RE.fullmatch(project_name) is None:
        raise ProjectError(f"generated project name is invalid: {project_name!r}")

    manifest_id = re.sub(r"_+", "_", re.sub(r"[ -]", "_", trimmed.lower())).strip("_")
    if _MANIFEST_ID_RE.fullmatch(manifest_id) is None or keyword.iskeyword(manifest_id):
        raise ProjectError(f"generated manifest ID is invalid: {manifest_id!r}")

    words = re.split(r"[ _-]+", trimmed)
    display_name = " ".join(word.lower().capitalize() for word in words)
    return ProjectNames(
        project_name=project_name,
        manifest_id=manifest_id,
        display_name=display_name,
        class_prefix=manifest_id.upper(),
    )


def normalize_python_version(value: str) -> str:
    """Validate and normalize an ``X.Y`` target Python version."""

    if not isinstance(value, str):
        raise ProjectError("Python version must be a string in X.Y form")
    if "\n" in value or "\r" in value:
        raise ProjectError(f"invalid Python version {value!r}; expected one X.Y line")
    trimmed = value.strip()
    if _PYTHON_VERSION_RE.fullmatch(trimmed) is None:
        raise ProjectError(f"invalid Python version {value!r}; expected X.Y")
    major_text, minor_text = trimmed.split(".", 1)
    return f"{int(major_text)}.{int(minor_text)}"


def normalize_python_version_file(value: str) -> str:
    """Normalize a one-line version file, allowing one trailing line ending."""

    if value.endswith("\r\n"):
        line = value[:-2]
    elif value.endswith(("\r", "\n")):
        line = value[:-1]
    else:
        line = value
    if "\n" in line or "\r" in line:
        raise ProjectError("invalid Python version file; expected exactly one X.Y line")
    return normalize_python_version(line)


def normalize_python_versions(values: Sequence[str]) -> tuple[str, ...]:
    """Validate multiple Python versions and reject duplicates."""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        current = normalize_python_version(value)
        if current in seen:
            raise ProjectError(f"duplicate Python version: {current}")
        seen.add(current)
        normalized.append(current)
    return tuple(normalized)


def validate_description(value: str) -> str:
    """Validate the one-line project description used by ``init``."""

    if not isinstance(value, str):
        raise ProjectError("description must be a string")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ProjectError("description must be a single line and must not contain NUL")
    trimmed = value.strip()
    if not trimmed:
        raise ProjectError("description must not be empty")
    return trimmed


def is_symlink_or_reparse(path: Path) -> bool:
    """Return whether *path* is a symlink or a Windows reparse point."""

    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def is_strict_descendant(path: Path, parent: Path) -> bool:
    """Return whether *path* is below, but not equal to, *parent*."""

    try:
        relative = path.relative_to(parent)
    except ValueError:
        return False
    return bool(relative.parts)


def resolve_project_root(directory: str | os.PathLike[str] | None, *, must_exist: bool) -> Path:
    """Resolve a command's project root without searching parent directories."""

    raw = Path.cwd() if directory is None else Path(directory)
    if "\x00" in str(raw):
        raise ProjectError("project root path must not contain NUL")
    absolute = raw if raw.is_absolute() else Path.cwd() / raw
    if absolute.is_symlink() or is_symlink_or_reparse(absolute):
        raise ProjectError(f"project root must not be a symlink or reparse point: {absolute}")
    if absolute.exists() and not absolute.is_dir():
        raise ProjectError(f"project root is not a directory: {absolute}")
    if must_exist and not absolute.is_dir():
        raise ProjectError(f"project root does not exist: {absolute}")
    try:
        return absolute.resolve(strict=must_exist)
    except OSError as exc:
        raise ProjectError(f"cannot resolve project root {absolute}: {exc}") from exc


def resolve_project_path(root: Path, value: str, label: str) -> Path:
    """Resolve a path-valued option relative to the project root."""

    if not isinstance(value, str) or not value.strip():
        raise ProjectError(f"{label} must be a non-empty path string")
    if "\x00" in value:
        raise ProjectError(f"{label} must not contain NUL")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve(strict=False)
    except OSError as exc:
        raise ProjectError(f"cannot resolve {label} {candidate}: {exc}") from exc


def resolve_source_dir(root: Path, value: str) -> Path:
    """Resolve and validate a source directory location."""

    resolved_root = root.resolve(strict=False)
    source = resolve_project_path(resolved_root, value, "source-dir")
    if not is_strict_descendant(source, resolved_root):
        raise ProjectError(f"source-dir must be a strict descendant of the project root: {source}")
    for reserved_name in (".blex", ".git"):
        reserved = (resolved_root / reserved_name).resolve(strict=False)
        if source == reserved or is_strict_descendant(source, reserved):
            raise ProjectError(f"source-dir must not be inside {reserved_name}: {source}")
    if source.exists() and not source.is_dir():
        raise ProjectError(f"source-dir is not a directory: {source}")
    return source


def _directory_identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


def validate_source_tree(source_dir: Path) -> None:
    """Validate every symlink/reparse point in an existing source tree."""

    source = source_dir.resolve(strict=True)
    if not source.is_dir():
        raise ProjectError(f"source-dir is not a directory: {source}")

    def visit(directory: Path, ancestors: frozenset[tuple[int, int]]) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ProjectError(f"cannot inspect source-dir entry {directory}: {exc}") from exc
        for entry in entries:
            path = Path(entry.path)
            if is_symlink_or_reparse(path):
                try:
                    target = path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise ProjectError(f"broken or cyclic source symlink: {path}") from exc
                if target != source and not is_strict_descendant(target, source):
                    raise ProjectError(
                        f"source symlink points outside source-dir: {path} -> {target}"
                    )
                if target.is_dir():
                    identity = _directory_identity(target)
                    if identity in ancestors:
                        raise ProjectError(f"cyclic source symlink: {path} -> {target}")
                    visit(target, ancestors | {identity})
                elif not target.is_file():
                    raise ProjectError(f"source symlink does not point to a regular file: {path}")
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                raise ProjectError(f"cannot inspect source-dir entry {path}: {exc}") from exc
            if is_directory:
                identity = _directory_identity(path)
                if identity in ancestors:
                    raise ProjectError(f"cyclic source directory: {path}")
                visit(path, ancestors | {identity})

    root_identity = _directory_identity(source)
    visit(source, frozenset({root_identity}))


def resolve_executable(value: str, root: Path, label: str) -> Path:
    """Resolve an executable path or a command name exactly as configured."""

    if not isinstance(value, str) or not value.strip():
        raise ProjectError(f"{label} must be a non-empty executable path or command name")
    if "\x00" in value:
        raise ProjectError(f"{label} executable must not contain NUL")
    if _WINDOWS_DRIVE_RELATIVE_RE.match(value) or (
        PureWindowsPath(value).drive and not PureWindowsPath(value).is_absolute()
    ):
        raise ProjectError(f"{label} uses a drive-relative path, which is not supported: {value}")

    path_like = Path(value).is_absolute() or "/" in value or "\\" in value
    if path_like:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ProjectError(f"cannot resolve {label} executable {candidate}: {exc}") from exc
    else:
        found = shutil.which(value)
        if found is None:
            raise ProjectError(f"cannot find {label} executable on PATH: {value}")
        resolved = Path(found).resolve(strict=True)

    if resolved.suffix.lower() == ".app" or not resolved.is_file():
        raise ProjectError(f"{label} executable is not a regular executable file: {resolved}")
    if os.name != "nt" and not os.access(resolved, os.X_OK):
        raise ProjectError(f"{label} file is not executable: {resolved}")
    return resolved


def read_python_version(root: Path) -> str:
    """Read and validate ``.python-version`` at the project root."""

    path = root / ".python-version"
    if not path.is_file():
        raise ProjectError(f"Python version file does not exist: {path}")
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProjectError(f"cannot read Python version file {path}: {exc}") from exc
    return normalize_python_version_file(value)


def _unwrap(value: object) -> object:
    if isinstance(value, Item):
        return value.unwrap()
    unwrap = getattr(value, "unwrap", None)
    if callable(unwrap):
        return cast(object, unwrap())
    return value


def _mapping(value: object, location: str) -> Mapping[str, object]:
    plain = _unwrap(value)
    if not isinstance(plain, Mapping):
        raise ProjectError(f"{location} must be a TOML table")
    return cast(Mapping[str, object], plain)


def load_pyproject(root: Path) -> ProjectData:
    """Load the project metadata required by ``dev`` and ``stage``."""

    path = root / "pyproject.toml"
    if not path.is_file():
        raise ProjectError(f"pyproject.toml does not exist: {path}")
    try:
        document = tomlkit.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ParseError) as exc:
        raise ProjectError(f"cannot parse {path}: {exc}") from exc

    project_value = document.get("project")
    if project_value is None:
        raise ProjectError(f"[project] table is missing in {path}")
    project = _mapping(project_value, "[project]")
    name = _unwrap(project.get("name")) if "name" in project else None
    if not isinstance(name, str) or not name.strip():
        raise ProjectError("[project].name must be a non-empty string")

    dependencies: tuple[str, ...] = ()
    if "dependencies" in project:
        raw_dependencies = _unwrap(project["dependencies"])
        if not isinstance(raw_dependencies, Sequence) or isinstance(raw_dependencies, (str, bytes)):
            raise ProjectError("[project].dependencies must be an array of strings")
        parsed: list[str] = []
        for dependency in raw_dependencies:
            plain_dependency = _unwrap(dependency)
            if not isinstance(plain_dependency, str):
                raise ProjectError("[project].dependencies must contain only strings")
            parsed.append(plain_dependency)
        dependencies = tuple(parsed)

    config = load_config(document)
    return ProjectData(
        root=root,
        path=path,
        document=document,
        name=name,
        dependencies=dependencies,
        config=config,
    )


def project_relative_path(path: Path, root: Path) -> str:
    """Return a project-contained path using forward slashes."""

    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ProjectError(f"path is outside the project root: {path}") from exc
    if not relative.parts:
        raise ProjectError(f"path must be a strict descendant of the project root: {path}")
    return relative.as_posix()
