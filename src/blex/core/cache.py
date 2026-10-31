"""Wheel cache primitives.

The cache is intentionally strict: an entry is either complete and exactly
matches its metadata, or it is reported as corrupt.  Normal cache reads never
repair or replace an entry.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import BinaryIO

from blex.core.errors import BlexError


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest for *path*."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wheel_fingerprint(
    dependencies: Sequence[str],
    python_version: str,
    platform_name: str,
    python_executable: Path,
    pip_version: str,
    environment: Mapping[str, str] | None = None,
    *,
    runtime: Mapping[str, str],
) -> str:
    """Create the canonical fingerprint for one pip target.

    Every ``PIP_*`` environment value participates in the hash.  The values
    are deliberately not returned or persisted anywhere else.
    """

    source_environment = os.environ if environment is None else environment
    pip_environment = {
        key: value for key, value in source_environment.items() if key.upper().startswith("PIP_")
    }
    payload = {
        "format": 1,
        "runtime": dict(runtime),
        "dependencies": list(dependencies),
        "pip": {
            "python_executable": python_executable.as_posix(),
            "version": pip_version,
        },
        "pip_environment": pip_environment,
        "platform": platform_name,
        "python": python_version,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class CachedWheel:
    """A validated Wheel stored in a cache entry."""

    path: Path
    filename: str
    sha256: str


class FileLock:
    """A process-scoped, non-blocking exclusive OS file lock."""

    def __init__(self, path: Path, description: str) -> None:
        self.path = path
        self.description = description
        self._stream: BinaryIO | None = None

    def __enter__(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if _is_link_or_reparse(self.path):
            raise BlexError(f"lock file must not be a symlink or reparse point: {self.path}")
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise BlexError(
                f"another {self.description} operation is already running (lock: {self.path})"
            ) from error
        self._stream = stream
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None


def cache_entry_path(
    project_root: Path,
    python_version: str,
    platform_name: str,
    fingerprint: str,
) -> Path:
    return project_root / ".blex" / "wheels" / f"py{python_version}" / platform_name / fingerprint


def cache_lock_path(
    project_root: Path,
    python_version: str,
    platform_name: str,
    fingerprint: str,
) -> Path:
    return (
        project_root
        / ".blex"
        / "locks"
        / "wheels"
        / f"py{python_version}"
        / platform_name
        / f"{fingerprint}.lock"
    )


def validate_cache_entry(
    entry: Path,
    *,
    fingerprint: str,
    python_version: str,
    platform_name: str,
    python_executable: Path,
    pip_version: str,
    runtime: Mapping[str, str],
    dependencies: Sequence[str],
) -> tuple[CachedWheel, ...]:
    """Validate a cache entry and return its Wheels in filename order."""

    try:
        if _is_link_or_reparse(entry) or not entry.is_dir():
            raise BlexError(f"Wheel cache entry is not a regular directory: {entry}")
        metadata_path = entry / "metadata.json"
        if _is_link_or_reparse(metadata_path) or not metadata_path.is_file():
            raise BlexError(f"Wheel cache metadata is missing: {metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except BlexError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BlexError(f"Wheel cache metadata is corrupt: {entry}") from error

    expected_top_keys = {
        "format",
        "fingerprint",
        "python",
        "platform",
        "pip",
        "runtime",
        "dependencies",
        "wheels",
    }
    if not isinstance(metadata, dict) or set(metadata) != expected_top_keys:
        raise BlexError(f"Wheel cache metadata schema is invalid: {metadata_path}")
    expected_pip = {
        "python_executable": python_executable.as_posix(),
        "version": pip_version,
    }
    if (
        type(metadata["format"]) is not int
        or metadata["format"] != 1
        or metadata["fingerprint"] != fingerprint
        or metadata["python"] != python_version
        or metadata["platform"] != platform_name
        or metadata["pip"] != expected_pip
        or metadata["runtime"] != dict(runtime)
        or metadata["dependencies"] != list(dependencies)
        or not isinstance(metadata["wheels"], list)
    ):
        raise BlexError(f"Wheel cache metadata does not match its target: {metadata_path}")

    wheel_records = metadata["wheels"]
    expected_names: set[str] = {"metadata.json"}
    cached_wheels: list[CachedWheel] = []
    previous_name: str | None = None
    for record in wheel_records:
        if not isinstance(record, dict) or set(record) != {"filename", "sha256"}:
            raise BlexError(f"Wheel cache metadata has an invalid Wheel record: {metadata_path}")
        filename = record["filename"]
        recorded_hash = record["sha256"]
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
            or not filename.endswith(".whl")
            or not isinstance(recorded_hash, str)
            or len(recorded_hash) != 64
            or any(character not in "0123456789abcdef" for character in recorded_hash)
        ):
            raise BlexError(f"Wheel cache metadata has an invalid Wheel record: {metadata_path}")
        if previous_name is not None and filename <= previous_name:
            raise BlexError(f"Wheel cache metadata Wheel order is invalid: {metadata_path}")
        previous_name = filename
        wheel_path = entry / filename
        if _is_link_or_reparse(wheel_path) or not wheel_path.is_file():
            raise BlexError(f"Wheel cache file is missing or invalid: {wheel_path}")
        actual_hash = sha256_file(wheel_path)
        if actual_hash != recorded_hash:
            raise BlexError(f"Wheel cache hash mismatch: {wheel_path}")
        expected_names.add(filename)
        cached_wheels.append(CachedWheel(wheel_path, filename, recorded_hash))

    try:
        actual_names = {child.name for child in entry.iterdir()}
    except OSError as error:
        raise BlexError(f"cannot inspect Wheel cache entry: {entry}") from error
    unexpected = sorted(actual_names - expected_names)
    if unexpected or actual_names != expected_names:
        details = ", ".join(unexpected or sorted(expected_names - actual_names))
        raise BlexError(f"Wheel cache contains unexpected or missing files: {entry} ({details})")
    return tuple(cached_wheels)


def write_cache_metadata(
    entry: Path,
    *,
    fingerprint: str,
    python_version: str,
    platform_name: str,
    python_executable: Path,
    pip_version: str,
    runtime: Mapping[str, str],
    dependencies: Sequence[str],
    wheels: Sequence[CachedWheel],
) -> None:
    """Write canonical cache metadata into a prepared temporary entry."""

    ordered_wheels = sorted(wheels, key=lambda wheel: wheel.filename)
    metadata = {
        "format": 1,
        "runtime": dict(runtime),
        "fingerprint": fingerprint,
        "python": python_version,
        "platform": platform_name,
        "pip": {
            "python_executable": python_executable.as_posix(),
            "version": pip_version,
        },
        "dependencies": list(dependencies),
        "wheels": [
            {"filename": wheel.filename, "sha256": wheel.sha256} for wheel in ordered_wheels
        ],
    }
    text = json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
    (entry / "metadata.json").write_text(text, encoding="utf-8", newline="\n")


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x0400)
    except FileNotFoundError:
        return False
