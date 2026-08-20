"""The deliberately small Blender extension manifest validator used by blex."""

from __future__ import annotations

import keyword
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import tomlkit
from tomlkit.exceptions import ParseError
from tomlkit.items import Item
from tomlkit.toml_document import TOMLDocument

from .config import SUPPORTED_PLATFORMS
from .errors import ManifestError

_MANIFEST_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ManifestData:
    """Validated manifest data used by deployment commands."""

    path: Path
    document: TOMLDocument
    extension_id: str
    platforms: tuple[str, ...] | None
    wheels: tuple[str, ...]


def _unwrap(value: object) -> object:
    if isinstance(value, Item):
        return value.unwrap()
    unwrap = getattr(value, "unwrap", None)
    if callable(unwrap):
        return cast(object, unwrap())
    return value


def _document_mapping(document: TOMLDocument) -> Mapping[str, object]:
    plain = _unwrap(document)
    if not isinstance(plain, Mapping):
        raise ManifestError("manifest must be a TOML document")
    return cast(Mapping[str, object], plain)


def validate_manifest_id(value: object) -> str:
    """Validate a Blender extension ID and return it unchanged."""

    plain = _unwrap(value)
    if (
        not isinstance(plain, str)
        or _MANIFEST_ID_RE.fullmatch(plain) is None
        or keyword.iskeyword(plain)
    ):
        raise ManifestError(f"invalid manifest ID: {plain!r}")
    return plain


def _optional_string_array(
    manifest: Mapping[str, object], key: str, *, supported: tuple[str, ...] | None = None
) -> tuple[str, ...] | None:
    if key not in manifest:
        return None
    value = _unwrap(manifest[key])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ManifestError(f"manifest {key!r} must be a non-empty array of strings")
    result: list[str] = []
    for entry in value:
        plain = _unwrap(entry)
        if not isinstance(plain, str) or not plain:
            raise ManifestError(f"manifest {key!r} must contain only non-empty strings")
        if supported is not None and plain not in supported:
            raise ManifestError(f"unsupported platform in manifest: {plain}")
        result.append(plain)
    return tuple(result)


def validate_manifest_document(document: TOMLDocument, *, require_core: bool = True) -> None:
    """Validate only the manifest fields that blex consumes or rewrites."""

    manifest = _document_mapping(document)
    if require_core:
        schema_version = _unwrap(manifest.get("schema_version"))
        if schema_version != "1.0.0" or not isinstance(schema_version, str):
            raise ManifestError('manifest schema_version must be the string "1.0.0"')
        if "id" not in manifest:
            raise ManifestError("manifest id is missing")
        validate_manifest_id(manifest["id"])
        extension_type = _unwrap(manifest.get("type"))
        if extension_type != "add-on" or not isinstance(extension_type, str):
            raise ManifestError('manifest type must be the string "add-on"')
    else:
        if "schema_version" in manifest:
            schema_version = _unwrap(manifest["schema_version"])
            if not isinstance(schema_version, str):
                raise ManifestError("manifest schema_version must be a string")
        if "id" in manifest:
            validate_manifest_id(manifest["id"])
        if "type" in manifest and not isinstance(_unwrap(manifest["type"]), str):
            raise ManifestError("manifest type must be a string")

    _optional_string_array(manifest, "platforms", supported=SUPPORTED_PLATFORMS)
    _optional_string_array(manifest, "wheels")

    for key in (
        "version",
        "name",
        "tagline",
        "maintainer",
        "blender_version_min",
        "blender_version_max",
        "website",
    ):
        if key in manifest and not isinstance(_unwrap(manifest[key]), str):
            raise ManifestError(f"manifest {key!r} must be a string")
    if "license" in manifest:
        _optional_string_array(manifest, "license")


def load_manifest(source_dir: Path) -> ManifestData:
    """Load and validate the sole manifest at the source directory root."""

    path = source_dir / "blender_manifest.toml"
    if not path.is_file():
        raise ManifestError(f"Blender manifest does not exist: {path}")
    try:
        document = tomlkit.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ParseError) as exc:
        raise ManifestError(f"cannot parse {path}: {exc}") from exc
    validate_manifest_document(document)
    manifest = _document_mapping(document)
    platforms = _optional_string_array(manifest, "platforms", supported=SUPPORTED_PLATFORMS)
    wheels = _optional_string_array(manifest, "wheels") or ()
    return ManifestData(
        path=path,
        document=document,
        extension_id=validate_manifest_id(manifest["id"]),
        platforms=platforms,
        wheels=wheels,
    )


def update_manifest_for_target(
    document: TOMLDocument, *, platforms: Sequence[str], wheels: Sequence[str]
) -> TOMLDocument:
    """Clone a source manifest and replace its target-specific fields."""

    validate_manifest_document(document)
    if not platforms:
        raise ManifestError("generated manifest platforms must not be empty")
    seen_platforms: set[str] = set()
    for platform in platforms:
        if platform not in SUPPORTED_PLATFORMS:
            raise ManifestError(f"unsupported generated manifest platform: {platform}")
        if platform in seen_platforms:
            raise ManifestError(f"duplicate generated manifest platform: {platform}")
        seen_platforms.add(platform)
    for wheel in wheels:
        if not isinstance(wheel, str) or not wheel:
            raise ManifestError("generated manifest wheels must contain non-empty strings")

    clone = tomlkit.parse(tomlkit.dumps(document))
    clone["platforms"] = list(platforms)
    if wheels:
        clone["wheels"] = list(wheels)
    elif "wheels" in clone:
        del clone["wheels"]
    return clone
