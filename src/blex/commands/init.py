"""Implementation of ``blex init``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import uuid
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import cast

import tomlkit
from tomlkit.exceptions import ParseError
from tomlkit.items import Item
from tomlkit.toml_document import TOMLDocument

from blex.core.config import BlexConfig, load_config
from blex.core.errors import BlexError, InitError, ProjectError
from blex.core.manifest import validate_manifest_document, validate_manifest_id
from blex.core.project import (
    ProjectNames,
    derive_names,
    is_symlink_or_reparse,
    normalize_python_version,
    normalize_python_version_file,
    project_relative_path,
    resolve_executable,
    resolve_project_root,
    resolve_source_dir,
    validate_description,
    validate_source_tree,
)

_DEFAULT_DESCRIPTION = "Add your description here"
_BLENDER_PYTHON_PREFIX = "BLEX_PYTHON_VERSION="
_ROOT_GITIGNORE_LINES = (
    ".venv/",
    ".blex/*",
    "!.blex/.gitignore",
    "dist/",
    "__pycache__/",
    "*.py[cod]",
)
_BLEX_GITIGNORE_LINES = ("*", "!.gitignore")


@dataclass(frozen=True)
class _ExistingProject:
    document: TOMLDocument | None
    config: BlexConfig
    name: str | None
    description: str | None


@dataclass(frozen=True)
class _ExistingManifest:
    document: TOMLDocument | None
    extension_id: str | None
    display_name: str | None


@dataclass(frozen=True)
class _Write:
    path: Path
    content: bytes


def _unwrap(value: object) -> object:
    if isinstance(value, Item):
        return value.unwrap()
    unwrap = getattr(value, "unwrap", None)
    if callable(unwrap):
        return cast(object, unwrap())
    return value


def _mutable_table(value: object, location: str) -> MutableMapping[str, object]:
    if not isinstance(value, MutableMapping):
        raise InitError(f"{location} must be a TOML table")
    return cast(MutableMapping[str, object], value)


def _plain_mapping(value: object, location: str) -> Mapping[str, object]:
    plain = _unwrap(value)
    if not isinstance(plain, Mapping):
        raise InitError(f"{location} must be a TOML table")
    return cast(Mapping[str, object], plain)


def _read_toml(path: Path, label: str) -> TOMLDocument:
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ParseError) as exc:
        raise InitError(f"cannot parse {label} {path}: {exc}") from exc


def _existing_project(path: Path) -> _ExistingProject:
    if is_symlink_or_reparse(path):
        raise InitError(f"pyproject.toml must be a regular file: {path}")
    if not path.exists():
        return _ExistingProject(None, BlexConfig(), None, None)
    if not path.is_file():
        raise InitError(f"pyproject.toml must be a regular file: {path}")
    document = _read_toml(path, "pyproject.toml")
    config = load_config(document)

    project_value = document.get("project")
    if project_value is None:
        return _ExistingProject(document, config, None, None)
    project = _plain_mapping(project_value, "[project]")

    name: str | None = None
    if "name" in project:
        raw_name = _unwrap(project["name"])
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise InitError("[project].name must be a non-empty string")
        name = raw_name

    description: str | None = None
    if "description" in project:
        raw_description = _unwrap(project["description"])
        if not isinstance(raw_description, str):
            raise InitError("[project].description must be a string")
        # Validation returns a trimmed form, but an existing value itself is preserved.
        validate_description(raw_description)
        description = raw_description

    if "requires-python" in project and not isinstance(_unwrap(project["requires-python"]), str):
        raise InitError("[project].requires-python must be a string")
    if "dependencies" in project:
        dependencies = _unwrap(project["dependencies"])
        if not isinstance(dependencies, list):
            raise InitError("[project].dependencies must be an array of strings")
        if any(not isinstance(_unwrap(entry), str) for entry in dependencies):
            raise InitError("[project].dependencies must contain only strings")

    return _ExistingProject(document, config, name, description)


def _existing_manifest(path: Path) -> _ExistingManifest:
    if is_symlink_or_reparse(path) and not path.exists():
        raise InitError(f"blender_manifest.toml link is broken: {path}")
    if not path.exists():
        return _ExistingManifest(None, None, None)
    if not path.is_file():
        raise InitError(f"blender_manifest.toml must resolve to a regular file: {path}")
    document = _read_toml(path, "Blender manifest")
    validate_manifest_document(document, require_core=False)
    manifest = _plain_mapping(document, "Blender manifest")

    extension_id = validate_manifest_id(manifest["id"]) if "id" in manifest else None
    display_name: str | None = None
    if "name" in manifest:
        raw_name = _unwrap(manifest["name"])
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise InitError("manifest name must be a non-empty string")
        display_name = raw_name
    return _ExistingManifest(document, extension_id, display_name)


def _template_bytes(section: str, filename: str) -> bytes:
    return resources.files("blex").joinpath("templates", section, filename).read_bytes()


def _template_text(section: str, filename: str) -> str:
    try:
        return _template_bytes(section, filename).decode("utf-8")
    except UnicodeError as exc:
        raise InitError(f"bundled template is not UTF-8: {section}/{filename}") from exc


def _python_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render(text: str, replacements: Mapping[str, str]) -> bytes:
    placeholders = {"{{" + token + "}}": replacement for token, replacement in replacements.items()}
    for placeholder in placeholders:
        if placeholder not in text:
            raise InitError(f"bundled template is missing placeholder {placeholder}")
    if placeholders:
        pattern = re.compile("|".join(re.escape(placeholder) for placeholder in placeholders))
        text = pattern.sub(lambda match: placeholders[match.group(0)], text)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized.encode("utf-8")


def _render_new_pyproject(project_name: str, description: str, source_relative: str) -> bytes:
    document = tomlkit.parse(_template_text("project", "pyproject.toml"))
    project = _mutable_table(document["project"], "bundled [project] template")
    tool = _mutable_table(document["tool"], "bundled [tool] template")
    blex = _mutable_table(tool["blex"], "bundled [tool.blex] template")
    project["name"] = project_name
    project["description"] = description
    blex["source-dir"] = source_relative
    return _render(tomlkit.dumps(document), {})


def _render_new_manifest(extension_id: str, display_name: str) -> bytes:
    document = tomlkit.parse(_template_text("extension", "blender_manifest.toml"))
    document["id"] = extension_id
    document["name"] = display_name
    return _render(tomlkit.dumps(document), {})


def _render_sample(filename: str, extension_id: str, display_name: str) -> bytes:
    prefix = extension_id.upper()
    text = _template_text("extension", filename)
    replacements = {
        "BLEX_CLASS_PREFIX": prefix,
        '"BLEX_OPERATOR_ID"': _python_string(f"{extension_id}.hello"),
        '"BLEX_PANEL_ID"': _python_string(f"{prefix}_PT_hello"),
        '"BLEX_DISPLAY_NAME"': _python_string(display_name),
        '"BLEX_REPORT_MESSAGE"': _python_string(f"Hello from {display_name}!"),
    }
    for sentinel, replacement in replacements.items():
        text = text.replace(sentinel, replacement)
    return _render(text, {})


def _render_readme(display_name: str, description: str) -> bytes:
    return _render(
        _template_text("project", "README.md"),
        {"DISPLAY_NAME": display_name, "DESCRIPTION": description},
    )


def _ensure_table(
    parent: MutableMapping[str, object], key: str, location: str
) -> MutableMapping[str, object]:
    if key not in parent:
        table = tomlkit.table()
        parent[key] = table
        return cast(MutableMapping[str, object], table)
    return _mutable_table(parent[key], location)


def _merge_pyproject(
    document: TOMLDocument,
    *,
    project_name: str,
    description: str,
    source_relative: str,
) -> bytes:
    root = cast(MutableMapping[str, object], document)
    project = _ensure_table(root, "project", "[project]")
    project_defaults: tuple[tuple[str, object], ...] = (
        ("name", project_name),
        ("version", "0.1.0"),
        ("description", description),
        ("readme", "README.md"),
        ("authors", [{"name": "Your Name", "email": "you@example.com"}]),
        ("requires-python", ">=3.11"),
        ("license", "GPL-3.0-or-later"),
        ("dependencies", []),
    )
    for key, value in project_defaults:
        if key not in project:
            project[key] = value

    tool = _ensure_table(root, "tool", "[tool]")
    blex = _ensure_table(tool, "blex", "[tool.blex]")
    for key, value in (
        ("source-dir", source_relative),
        ("blender", "blender"),
    ):
        if key not in blex:
            blex[key] = value

    dev = _ensure_table(blex, "dev", "[tool.blex.dev]")
    for key, value in (
        ("link-mode", "auto"),
        ("reload-panel", True),
        ("wheels", True),
        ("debugpy", False),
        ("debugpy-host", "127.0.0.1"),
        ("debugpy-port", 5678),
    ):
        if key not in dev:
            dev[key] = value

    stage = _ensure_table(blex, "stage", "[tool.blex.stage]")
    for key, value in (("output-dir", "dist"), ("wheels", True)):
        if key not in stage:
            stage[key] = value
    return tomlkit.dumps(document).encode("utf-8")


def _merge_manifest(document: TOMLDocument, *, extension_id: str, display_name: str) -> bytes:
    root = cast(MutableMapping[str, object], document)
    defaults: tuple[tuple[str, object], ...] = (
        ("schema_version", "1.0.0"),
        ("id", extension_id),
        ("version", "1.0.0"),
        ("name", display_name),
        ("tagline", "This is another extension"),
        ("maintainer", "Developer name"),
        ("type", "add-on"),
        ("blender_version_min", "4.2.0"),
        ("license", ["SPDX:GPL-3.0-or-later"]),
    )
    for key, value in defaults:
        if key not in root:
            root[key] = value
    return tomlkit.dumps(document).encode("utf-8")


def _merge_gitignore(existing: bytes | None, required: tuple[str, ...], path: Path) -> bytes:
    if existing is None:
        return ("\n".join(required) + "\n").encode("utf-8")
    try:
        text = existing.decode("utf-8")
    except UnicodeError as exc:
        raise InitError(f"cannot decode existing gitignore as UTF-8: {path}") from exc
    lines = {line.rstrip("\r") for line in text.splitlines()}
    missing = [line for line in required if line not in lines]
    if not missing:
        return existing
    separator = "" if not text or text.endswith(("\n", "\r")) else "\n"
    return (text + separator + "\n".join(missing) + "\n").encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise InitError(f"cannot hash Blender executable {path}: {exc}") from exc
    return digest.hexdigest()


def _validate_blender_cache(data: object) -> dict[str, object]:
    # Deliberately raise ordinary built-in exceptions for cache corruption. The CLI
    # must expose those as unexpected failures rather than repairing or wrapping them.
    if not isinstance(data, dict):
        raise TypeError("Blender Python cache root must be an object")
    if set(data) != {"format", "entries"}:
        raise ValueError("invalid Blender Python cache top-level keys")
    if data["format"] != 1:
        raise ValueError("unsupported Blender Python cache format")
    entries = data["entries"]
    if not isinstance(entries, dict):
        raise TypeError("Blender Python cache entries must be an object")
    for digest, entry in entries.items():
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("invalid Blender Python cache digest")
        if not isinstance(entry, dict):
            raise TypeError("invalid Blender Python cache entry")
        if set(entry) != {"python", "executable"}:
            raise ValueError("invalid Blender Python cache entry keys")
        python = entry["python"]
        executable = entry["executable"]
        if not isinstance(python, str) or not isinstance(executable, str):
            raise TypeError("invalid Blender Python cache entry values")
        try:
            normalized = normalize_python_version(python)
        except ProjectError as exc:
            raise ValueError("invalid Python version in Blender Python cache") from exc
        if normalized != python:
            raise ValueError("non-normalized Python version in Blender Python cache")
    return cast(dict[str, object], data)


def _process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _detection_error(message: str, stdout: str, stderr: str) -> InitError:
    details = [message]
    if stdout:
        details.append(f"stdout:\n{stdout.rstrip()}")
    if stderr:
        details.append(f"stderr:\n{stderr.rstrip()}")
    return InitError("\n".join(details))


def _detect_blender_python(
    root: Path, configured: str, *, verbose: bool
) -> tuple[str, _Write | None]:
    executable = resolve_executable(configured, root, "Blender")
    digest = _file_sha256(executable)
    cache_path = root / ".blex" / "cache" / "blender-python.json"
    cache_data: dict[str, object]
    if is_symlink_or_reparse(cache_path):
        raise InitError(f"Blender Python cache must be a regular file: {cache_path}")
    if cache_path.exists():
        if not cache_path.is_file():
            raise InitError(f"Blender Python cache must be a regular file: {cache_path}")
        # json/schema errors intentionally escape as unexpected exceptions.
        cache_data = _validate_blender_cache(json.loads(cache_path.read_text(encoding="utf-8")))
    else:
        cache_data = {"format": 1, "entries": {}}
    entries = cast(dict[str, object], cache_data["entries"])
    cached = entries.get(digest)
    if cached is not None:
        entry = cast(dict[str, object], cached)
        version = cast(str, entry["python"])
        print(f"Blender Python cache hit: {version}")
        if verbose:
            print(f"Blender executable: {executable}")
            print(f"Blender Python cache: hit ({digest})")
        return version, None

    expression = (
        'import sys; print("'
        + _BLENDER_PYTHON_PREFIX
        + '" + str(sys.version_info.major) + "." + str(sys.version_info.minor))'
    )
    command = [
        str(executable),
        "--background",
        "--factory-startup",
        "--python-expr",
        expression,
    ]
    if verbose:
        print(f"Blender executable: {executable}")
        print(f"Blender Python cache: miss ({digest})")
        print(f"Running: {shlex.join(command)}")
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise _detection_error(
            "Blender Python detection timed out after 30 seconds",
            _process_output(exc.stdout),
            _process_output(exc.stderr),
        ) from exc
    except OSError as exc:
        raise InitError(f"failed to execute Blender for Python detection: {exc}") from exc
    if completed.returncode != 0:
        raise _detection_error(
            f"Blender Python detection exited with code {completed.returncode}",
            completed.stdout,
            completed.stderr,
        )
    versions = [
        line[len(_BLENDER_PYTHON_PREFIX) :]
        for line in completed.stdout.splitlines()
        if line.startswith(_BLENDER_PYTHON_PREFIX)
    ]
    if len(versions) != 1:
        raise _detection_error(
            "Blender Python detection output did not contain exactly one result line",
            completed.stdout,
            completed.stderr,
        )
    try:
        version = normalize_python_version(versions[0])
    except ProjectError as exc:
        raise _detection_error(
            "Blender Python detection returned an invalid version",
            completed.stdout,
            completed.stderr,
        ) from exc
    entries[digest] = {"python": version, "executable": executable.as_posix()}
    content = (json.dumps(cache_data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return version, _Write(cache_path, content)


def _check_managed_paths(root: Path, *, use_blex: bool) -> None:
    if not use_blex:
        return
    current = root / ".blex"
    for path in (current, current / "cache"):
        if is_symlink_or_reparse(path):
            raise InitError(f"managed directory must not be a symlink or reparse point: {path}")
        if path.exists() and not path.is_dir():
            raise InitError(f"managed directory path is not a directory: {path}")


def _check_write_targets(writes: list[_Write]) -> None:
    seen: set[Path] = set()
    for write in writes:
        if write.path in seen:
            raise InitError(f"internal duplicate init write target: {write.path}")
        seen.add(write.path)
        if is_symlink_or_reparse(write.path):
            raise InitError(f"generated file target must be a regular file: {write.path}")
        if write.path.exists():
            if not write.path.is_file():
                raise InitError(f"generated file target must be a regular file: {write.path}")
        parent = write.path.parent
        while not parent.exists() and parent != parent.parent:
            if is_symlink_or_reparse(parent):
                raise InitError(f"generated file parent must be a regular directory: {parent}")
            parent = parent.parent
        if is_symlink_or_reparse(parent) or (parent.exists() and not parent.is_dir()):
            raise InitError(f"generated file parent is not a directory: {parent}")


def _atomic_write(path: Path, content: bytes) -> None:
    mode: int | None = None
    if path.exists():
        mode = path.stat().st_mode & 0o7777
    temporary = path.parent / f".blex-init-{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists() or is_symlink_or_reparse(temporary):
            temporary.unlink()


def _create_parent_directories(path: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise InitError(f"cannot create directory below a non-directory: {current}")
    for directory in reversed(missing):
        created.append(directory)
        directory.mkdir()


def _apply_transaction(writes: list[_Write]) -> None:
    originals: dict[Path, bytes | None] = {}
    applied: list[Path] = []
    created_directories: list[Path] = []
    try:
        for write in writes:
            _create_parent_directories(write.path.parent, created_directories)
            originals[write.path] = write.path.read_bytes() if write.path.exists() else None
            if originals[write.path] == write.content:
                continue
            applied.append(write.path)
            _atomic_write(write.path, write.content)
    except BaseException as original_error:
        rollback_errors: list[str] = []
        for path in reversed(applied):
            try:
                original = originals[path]
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write(path, original)
            except OSError as exc:
                rollback_errors.append(f"{path}: {exc}")
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except FileNotFoundError:
                continue
            except OSError as exc:
                rollback_errors.append(f"{directory}: {exc}")
        if rollback_errors:
            joined = "; ".join(rollback_errors)
            raise InitError(f"init rollback was incomplete: {joined}") from original_error
        if isinstance(original_error, (BlexError, KeyboardInterrupt)):
            raise
        if isinstance(original_error, OSError):
            raise InitError(
                f"failed to write initialized project: {original_error}"
            ) from original_error
        raise


def _read_existing_bytes(path: Path) -> bytes | None:
    if is_symlink_or_reparse(path):
        raise InitError(f"expected a regular file: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise InitError(f"expected a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InitError(f"cannot read {path}: {exc}") from exc


def _names_for_missing_values(base: str) -> ProjectNames:
    try:
        return derive_names(base)
    except ProjectError as exc:
        raise InitError(str(exc)) from exc


def _check_manifest_sample_aliases(manifest_write_path: Path, source_dir: Path) -> None:
    for filename in ("__init__.py", "operators.py", "panels.py"):
        sample = source_dir / filename
        if not sample.exists():
            continue
        try:
            sample_target = sample.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise InitError(f"cannot resolve Extension sample path: {sample}") from exc
        if sample_target == manifest_write_path:
            raise InitError(
                f"Blender manifest and Extension sample paths must not alias: "
                f"{manifest_write_path} and {sample}"
            )


def run(args: argparse.Namespace) -> int:
    """Initialize one Blender Extension project and return a process status."""

    root = resolve_project_root(args.directory, must_exist=False)
    force = args.force
    explicit_names = _names_for_missing_values(args.name) if args.name is not None else None
    explicit_description = (
        validate_description(args.description) if args.description is not None else None
    )
    explicit_python = normalize_python_version(args.python) if args.python is not None else None

    pyproject_path = root / "pyproject.toml"
    if (pyproject_path.exists() or is_symlink_or_reparse(pyproject_path)) and not force:
        raise InitError(f"project marker already exists; use --force to merge: {pyproject_path}")
    existing_project = _existing_project(pyproject_path)

    if explicit_names is not None and existing_project.name is not None:
        if existing_project.name != explicit_names.project_name:
            raise InitError(
                f"--name conflicts with existing [project].name: {existing_project.name!r}"
            )
    if explicit_description is not None and existing_project.description is not None:
        if existing_project.description != explicit_description:
            raise InitError("--description conflicts with existing [project].description")

    default_base = (
        args.name
        if args.name is not None
        else existing_project.name
        if existing_project.name is not None
        else root.name
    )
    default_names = explicit_names

    configured_source = existing_project.config.source_dir
    if args.source_dir is not None and configured_source is not None:
        explicit_source_path = resolve_source_dir(root, args.source_dir)
        configured_source_path = resolve_source_dir(root, configured_source)
        if explicit_source_path != configured_source_path:
            raise InitError("--source-dir conflicts with existing [tool.blex].source-dir")
    if args.source_dir is not None:
        source_value = args.source_dir
    elif configured_source is not None:
        source_value = configured_source
    else:
        if default_names is None:
            default_names = _names_for_missing_values(default_base)
        source_value = f"src/{default_names.manifest_id}"
    source_dir = resolve_source_dir(root, source_value)
    source_relative = project_relative_path(source_dir, root)
    if source_dir.exists():
        validate_source_tree(source_dir)

    manifest_path = source_dir / "blender_manifest.toml"
    if (manifest_path.exists() or is_symlink_or_reparse(manifest_path)) and not force:
        raise InitError(f"project marker already exists; use --force to merge: {manifest_path}")
    existing_manifest = _existing_manifest(manifest_path)
    if is_symlink_or_reparse(manifest_path):
        try:
            manifest_write_path = manifest_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise InitError(f"cannot resolve Blender manifest link: {manifest_path}") from exc
    else:
        manifest_write_path = manifest_path
    _check_manifest_sample_aliases(manifest_write_path, source_dir)

    if explicit_names is not None:
        if (
            existing_manifest.extension_id is not None
            and existing_manifest.extension_id != explicit_names.manifest_id
        ):
            raise InitError(
                f"--name conflicts with existing manifest id: {existing_manifest.extension_id!r}"
            )
        if (
            existing_manifest.display_name is not None
            and existing_manifest.display_name != explicit_names.display_name
        ):
            raise InitError(
                f"--name conflicts with existing manifest name: {existing_manifest.display_name!r}"
            )

    if default_names is None and (
        existing_project.name is None
        or existing_manifest.extension_id is None
        or existing_manifest.display_name is None
    ):
        default_names = _names_for_missing_values(default_base)

    project_name = (
        explicit_names.project_name
        if explicit_names is not None
        else (
            existing_project.name
            if existing_project.name is not None
            else cast(ProjectNames, default_names).project_name
        )
    )
    extension_id = (
        explicit_names.manifest_id
        if explicit_names is not None
        else (
            existing_manifest.extension_id
            if existing_manifest.extension_id is not None
            else cast(ProjectNames, default_names).manifest_id
        )
    )
    display_name = (
        explicit_names.display_name
        if explicit_names is not None
        else (
            existing_manifest.display_name
            if existing_manifest.display_name is not None
            else cast(ProjectNames, default_names).display_name
        )
    )
    description = existing_project.description or explicit_description or _DEFAULT_DESCRIPTION

    _check_managed_paths(root, use_blex=args.blender is not None and explicit_python is None)

    cache_write: _Write | None = None
    python_was_explicit = explicit_python is not None or args.blender is not None
    if explicit_python is not None:
        python_version = explicit_python
    elif args.blender is not None:
        python_version, cache_write = _detect_blender_python(
            root, args.blender, verbose=args.verbose
        )
    else:
        python_version_path = root / ".python-version"
        if python_version_path.exists() or is_symlink_or_reparse(python_version_path):
            existing_python = _read_existing_bytes(python_version_path)
            if existing_python is None:
                raise InitError(f"Python version file does not exist: {python_version_path}")
            try:
                python_version = normalize_python_version_file(existing_python.decode("utf-8"))
            except UnicodeError as exc:
                raise InitError(f"cannot decode {python_version_path} as UTF-8") from exc
        else:
            python_version = "3.11"

    writes: list[_Write] = []
    if existing_project.document is None:
        writes.append(
            _Write(
                pyproject_path,
                _render_new_pyproject(project_name, description, source_relative),
            )
        )
    else:
        writes.append(
            _Write(
                pyproject_path,
                _merge_pyproject(
                    existing_project.document,
                    project_name=project_name,
                    description=description,
                    source_relative=source_relative,
                ),
            )
        )

    python_version_path = root / ".python-version"
    if python_was_explicit or not (
        python_version_path.exists() or is_symlink_or_reparse(python_version_path)
    ):
        writes.append(_Write(python_version_path, f"{python_version}\n".encode("utf-8")))

    readme_path = root / "README.md"
    if is_symlink_or_reparse(readme_path) and not readme_path.exists():
        raise InitError(f"README.md link is broken: {readme_path}")
    if not readme_path.exists():
        writes.append(_Write(readme_path, _render_readme(display_name, description)))
    elif not readme_path.is_file():
        raise InitError(f"README.md must be a regular file: {readme_path}")

    license_path = root / "LICENSE"
    if is_symlink_or_reparse(license_path) and not license_path.exists():
        raise InitError(f"LICENSE link is broken: {license_path}")
    if not license_path.exists():
        writes.append(_Write(license_path, _template_bytes("project", "LICENSE")))
    elif not license_path.is_file():
        raise InitError(f"LICENSE must be a regular file: {license_path}")

    root_gitignore_path = root / ".gitignore"
    root_gitignore_existing = _read_existing_bytes(root_gitignore_path)
    writes.append(
        _Write(
            root_gitignore_path,
            _merge_gitignore(root_gitignore_existing, _ROOT_GITIGNORE_LINES, root_gitignore_path),
        )
    )

    if existing_manifest.document is None:
        writes.append(_Write(manifest_write_path, _render_new_manifest(extension_id, display_name)))
    else:
        writes.append(
            _Write(
                manifest_write_path,
                _merge_manifest(
                    existing_manifest.document,
                    extension_id=extension_id,
                    display_name=display_name,
                ),
            )
        )

    for filename in ("__init__.py", "operators.py", "panels.py"):
        path = source_dir / filename
        if is_symlink_or_reparse(path) and not path.exists():
            raise InitError(f"Extension sample link is broken: {path}")
        if not path.exists():
            writes.append(_Write(path, _render_sample(filename, extension_id, display_name)))
        elif not path.is_file():
            raise InitError(f"Extension sample path must be a regular file: {path}")

    if cache_write is not None:
        writes.append(cache_write)
    if args.blender is not None and explicit_python is None:
        blex_gitignore_path = root / ".blex" / ".gitignore"
        blex_gitignore_existing = _read_existing_bytes(blex_gitignore_path)
        writes.append(
            _Write(
                blex_gitignore_path,
                _merge_gitignore(
                    blex_gitignore_existing, _BLEX_GITIGNORE_LINES, blex_gitignore_path
                ),
            )
        )

    _check_write_targets(writes)
    _apply_transaction(writes)
    print(f"Initialized Blender Extension project at {root}")
    return 0
