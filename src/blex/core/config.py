"""Validation for the blex configuration stored in ``pyproject.toml``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeVar, cast

from tomlkit.items import Item

from .errors import ConfigurationError

SUPPORTED_PLATFORMS = (
    "windows-x64",
    "windows-arm64",
    "macos-x64",
    "macos-arm64",
    "linux-x64",
)


@dataclass(frozen=True)
class DevConfig:
    """Values explicitly present in ``[tool.blex.dev]``."""

    link_mode: str | None = None
    reload_panel: bool | None = None
    wheels: bool | None = None
    debugpy: bool | None = None
    debugpy_host: str | None = None
    debugpy_port: int | None = None
    blend_file: str | None = None
    user_resources: str | None = None


@dataclass(frozen=True)
class StageConfig:
    """Values explicitly present in ``[tool.blex.stage]``."""

    output_dir: str | None = None
    wheels: bool | None = None


@dataclass(frozen=True)
class BlexConfig:
    """Validated blex settings without applying built-in defaults."""

    source_dir: str | None = None
    blender: str | None = None
    dev: DevConfig = DevConfig()
    stage: StageConfig = StageConfig()


def _unwrap(value: object) -> object:
    if isinstance(value, Item):
        return value.unwrap()
    unwrap = getattr(value, "unwrap", None)
    if callable(unwrap):
        return cast(object, unwrap())
    return value


def _as_mapping(value: object, location: str) -> Mapping[str, object]:
    plain = _unwrap(value)
    if not isinstance(plain, Mapping):
        raise ConfigurationError(f"{location} must be a TOML table")
    return cast(Mapping[str, object], plain)


def _optional_string(table: Mapping[str, object], key: str, location: str) -> str | None:
    if key not in table:
        return None
    value = _unwrap(table[key])
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{location}.{key} must be a non-empty string")
    return value


def _optional_bool(table: Mapping[str, object], key: str, location: str) -> bool | None:
    if key not in table:
        return None
    value = _unwrap(table[key])
    if type(value) is not bool:
        raise ConfigurationError(f"{location}.{key} must be a boolean")
    return cast(bool, value)


def _unknown_keys(table: Mapping[str, object], allowed: set[str], location: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ConfigurationError(f"unknown key in {location}: {joined}")


def _load_dev(table: Mapping[str, object]) -> DevConfig:
    location = "[tool.blex.dev]"
    _unknown_keys(
        table,
        {
            "link-mode",
            "reload-panel",
            "wheels",
            "debugpy",
            "debugpy-host",
            "debugpy-port",
            "blend-file",
            "user-resources",
        },
        location,
    )

    link_mode = _optional_string(table, "link-mode", location)
    if link_mode is not None and link_mode not in {"auto", "symlink", "copy"}:
        raise ConfigurationError(f"{location}.link-mode must be one of: auto, symlink, copy")

    debugpy_port: int | None = None
    if "debugpy-port" in table:
        value = _unwrap(table["debugpy-port"])
        if type(value) is not int or not 1 <= cast(int, value) <= 65535:
            raise ConfigurationError(f"{location}.debugpy-port must be an integer from 1 to 65535")
        debugpy_port = cast(int, value)

    return DevConfig(
        link_mode=link_mode,
        reload_panel=_optional_bool(table, "reload-panel", location),
        wheels=_optional_bool(table, "wheels", location),
        debugpy=_optional_bool(table, "debugpy", location),
        debugpy_host=_optional_string(table, "debugpy-host", location),
        debugpy_port=debugpy_port,
        blend_file=_optional_string(table, "blend-file", location),
        user_resources=_optional_string(table, "user-resources", location),
    )


def _load_stage(table: Mapping[str, object]) -> StageConfig:
    location = "[tool.blex.stage]"
    _unknown_keys(table, {"output-dir", "wheels"}, location)

    return StageConfig(
        output_dir=_optional_string(table, "output-dir", location),
        wheels=_optional_bool(table, "wheels", location),
    )


def load_config(document: Mapping[str, object]) -> BlexConfig:
    """Validate and return the blex configuration in a TOML document.

    Values that are absent remain ``None``. Callers are responsible for applying
    command-line precedence and built-in defaults.
    """

    plain_document = _as_mapping(document, "pyproject.toml")
    tool_value = plain_document.get("tool")
    if tool_value is None:
        return BlexConfig()
    tool = _as_mapping(tool_value, "[tool]")

    blex_value = tool.get("blex")
    if blex_value is None:
        return BlexConfig()
    blex = _as_mapping(blex_value, "[tool.blex]")
    _unknown_keys(blex, {"source-dir", "blender", "dev", "stage"}, "[tool.blex]")

    dev_value = blex.get("dev")
    stage_value = blex.get("stage")
    dev = DevConfig() if dev_value is None else _load_dev(_as_mapping(dev_value, "[tool.blex.dev]"))
    stage = (
        StageConfig()
        if stage_value is None
        else _load_stage(_as_mapping(stage_value, "[tool.blex.stage]"))
    )

    return BlexConfig(
        source_dir=_optional_string(blex, "source-dir", "[tool.blex]"),
        blender=_optional_string(blex, "blender", "[tool.blex]"),
        dev=dev,
        stage=stage,
    )


T = TypeVar("T")


def select_value(cli_value: T | None, configured_value: T | None, default: T) -> T:
    """Apply the specified CLI > configuration > built-in precedence."""

    if cli_value is not None:
        return cli_value
    if configured_value is not None:
        return configured_value
    return default
