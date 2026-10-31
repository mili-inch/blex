from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit

from blex.core.errors import ManifestError
from blex.core.manifest import (
    load_manifest,
    update_manifest_for_target,
    validate_manifest_document,
    validate_manifest_id,
)


def _manifest(extra: str = "") -> str:
    return f'schema_version = "1.0.0"\nid = "demo_addon"\ntype = "add-on"\n{extra}'


def test_validate_and_load_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "blender_manifest.toml").write_text(
        _manifest(
            'platforms = ["windows-x64", "macos-arm64"]\nwheels = ["./wheels/example.whl"]\n'
        ),
        encoding="utf-8",
    )
    loaded = load_manifest(source)
    assert loaded.path == source / "blender_manifest.toml"
    assert loaded.extension_id == "demo_addon"
    assert loaded.platforms == ("windows-x64", "macos-arm64")
    assert loaded.wheels == ("./wheels/example.whl",)


@pytest.mark.parametrize("value", ["", "Bad", "bad-id", "1bad", "class"])
def test_invalid_manifest_id(value: str) -> None:
    with pytest.raises(ManifestError):
        validate_manifest_id(value)


@pytest.mark.parametrize(
    "text",
    [
        'schema_version = "2.0.0"\nid = "demo"\ntype = "add-on"\n',
        'schema_version = "1.0.0"\ntype = "add-on"\n',
        'schema_version = "1.0.0"\nid = "demo"\ntype = "theme"\n',
        _manifest('platforms = ["linux-arm64"]\n'),
        _manifest("platforms = []\n"),
        _manifest("wheels = []\n"),
        _manifest("wheels = [1]\n"),
        _manifest('wheels = "./demo.whl"\n'),
    ],
)
def test_invalid_manifest_document(text: str) -> None:
    with pytest.raises(ManifestError):
        validate_manifest_document(tomlkit.parse(text))


def test_update_manifest_for_target_clones_and_preserves_unknown_fields() -> None:
    source = tomlkit.parse(
        _manifest(
            '# keep this comment\ncustom = "value"\nplatforms = ["linux-x64"]\n'
            'wheels = ["./old.whl"]\n'
        )
    )
    generated = update_manifest_for_target(
        source,
        platforms=["windows-x64", "windows-arm64"],
        wheels=["./wheels/a.whl"],
    )
    assert list(source["platforms"]) == ["linux-x64"]
    assert list(source["wheels"]) == ["./old.whl"]
    assert list(generated["platforms"]) == ["windows-x64", "windows-arm64"]
    assert list(generated["wheels"]) == ["./wheels/a.whl"]
    assert generated["custom"] == "value"
    assert "# keep this comment" in tomlkit.dumps(generated)


def test_update_manifest_removes_empty_wheels() -> None:
    source = tomlkit.parse(_manifest('wheels = ["./old.whl"]\n'))
    generated = update_manifest_for_target(source, platforms=["linux-x64"], wheels=[])
    assert "wheels" not in generated


def test_manifest_parse_failure_is_handled(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "blender_manifest.toml").write_text("not = [toml", encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(source)
