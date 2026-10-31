"""Offline end-to-end regressions using real pip, with synthetic Wheels."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import zipfile

import pytest
import tomlkit

from pip._vendor.packaging.tags import sys_tags

from blex.core import wheels
from blex.core.environment import capture_native_environment
from blex.core.environment import current_platform, current_python_version
from blex.main import main


@pytest.fixture
def local_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    index = tmp_path / "index"
    index.mkdir()
    for name in tuple(os.environ):
        if name.upper().startswith("PIP_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", str(index))
    return index


def make_wheel(
    folder: Path,
    name: str,
    version: str = "1.0",
    *,
    tag: str = "py3-none-any",
    requires: tuple[str, ...] = (),
) -> Path:
    distribution = f"{name}-{version}.dist-info"
    path = folder / f"{name}-{version}-{tag}.whl"
    entries = {
        f"{name}/__init__.py": f"VERSION = {version!r}\n",
        f"{distribution}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires)
            + "Provides-Extra: feature\n\n"
        ),
        f"{distribution}/WHEEL": (
            f"Wheel-Version: 1.0\nGenerator: blex-tests\nRoot-Is-Purelib: true\nTag: {tag}\n"
        ),
    }
    entries[f"{distribution}/RECORD"] = (
        "".join(f"{key},,\n" for key in entries) + f"{distribution}/RECORD,,\n"
    )
    with zipfile.ZipFile(path, "w") as archive:
        for key, content in entries.items():
            archive.writestr(key, content)
    return path


def _project(root: Path, dependencies: list[str]) -> Path:
    assert main(["init", str(root), "--name", "Target Test"]) == 0
    path = root / "pyproject.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["project"]["dependencies"] = dependencies
    path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")
    return root / "src" / "target_test"


def _staged_source(root: Path) -> Path:
    return root / "dist" / f"py{current_python_version()}" / current_platform() / "source"


def test_stage_resolves_native_direct_transitive_and_extra_markers(
    tmp_path: Path, local_index: Path
) -> None:
    for name in ("os_leaf", "py_leaf", "direct_leaf", "extra_leaf", "excluded_leaf"):
        make_wheel(local_index, name)
    make_wheel(
        local_index,
        "parentprobe",
        requires=(
            f'os_leaf; sys_platform == "{sys.platform}"',
            f'py_leaf; python_version == "{current_python_version()}"',
            'extra_leaf; extra == "feature"',
            'excluded_leaf; sys_platform == "unavailable"',
            'excluded_leaf; python_version < "3.0"',
        ),
    )
    root = tmp_path / "project"
    _project(
        root,
        [
            "parentprobe[feature]",
            f'direct_leaf; sys_platform == "{sys.platform}"',
            'excluded_leaf; python_version < "3.0"',
        ],
    )
    assert main(["stage", str(root)]) == 0
    source = _staged_source(root)
    names = {path.name.split("-")[0] for path in (source / "wheels").iterdir()}
    assert names == {"parentprobe", "os_leaf", "py_leaf", "direct_leaf", "extra_leaf"}
    manifest = tomlkit.parse((source / "blender_manifest.toml").read_text(encoding="utf-8"))
    assert manifest["platforms"] == [current_platform()]
    assert len(manifest["wheels"]) == len(names)


def test_stage_selects_native_wheel_tags_and_resolves_transitive_versions(
    tmp_path: Path, local_index: Path
) -> None:
    make_wheel(
        local_index, "targetpkg", "1.0", tag=str(next(sys_tags())), requires=("shareddep<2",)
    )
    make_wheel(
        local_index, "targetpkg", "2.0", tag="cp999-cp999-unavailable", requires=("shareddep>=2",)
    )
    make_wheel(local_index, "shareddep", "1.0")
    make_wheel(local_index, "shareddep", "2.0")
    root = tmp_path / "project"
    _project(root, ["targetpkg"])
    assert main(["stage", str(root)]) == 0
    files = list((_staged_source(root) / "wheels").glob("*.whl"))
    assert len(files) == 2
    assert all(path.name.split("-")[1] == "1.0" for path in files)


def test_refresh_preserves_previously_resolved_wheel_and_runtime_link(
    tmp_path: Path, local_index: Path
) -> None:
    make_wheel(local_index, "refreshprobe", "1.0")
    root = tmp_path / "project"
    source = _project(root, ["refreshprobe"])

    def resolve(refresh: bool) -> tuple[wheels.WheelArtifact, ...]:
        return wheels.resolve_wheels(
            project_root=root,
            source_dir=source,
            wheel_paths=(),
            native=capture_native_environment(),
            environment=dict(os.environ),
            dependencies=("refreshprobe",),
            automatic=True,
            refresh=refresh,
        )

    first = resolve(False)[0]
    before = first.source.read_bytes()
    runtime_link = root / "running-wheel.whl"
    try:
        runtime_link.symlink_to(first.source)
    except OSError:
        runtime_link = first.source
    make_wheel(local_index, "refreshprobe", "2.0")
    second = resolve(True)[0]
    assert second.source.name == "refreshprobe-2.0-py3-none-any.whl"
    assert first.source != second.source
    assert first.source.read_bytes() == before
    assert runtime_link.read_bytes() == before


def test_failed_native_resolution_preserves_existing_stage(
    tmp_path: Path, local_index: Path
) -> None:
    make_wheel(local_index, "foreignonly", tag="cp999-cp999-unavailable")
    root = tmp_path / "project"
    _project(root, ["foreignonly"])
    target = _staged_source(root).parent
    target.mkdir(parents=True)
    marker = target / "previous.txt"
    marker.write_text("keep", encoding="utf-8")
    assert main(["stage", str(root)]) == 1
    assert marker.read_text(encoding="utf-8") == "keep"
    assert list(target.iterdir()) == [marker]
