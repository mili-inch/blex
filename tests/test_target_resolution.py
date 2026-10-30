"""Offline end-to-end regressions using real uv and pip, with synthetic Wheels."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import venv
import zipfile

import pytest
import tomlkit

from blex.main import main


@pytest.fixture(scope="session")
def pip_executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    environment = tmp_path_factory.mktemp("offline-pip")
    # ensurepip uses wheels bundled with the Python installation, never PyPI.
    venv.EnvBuilder(with_pip=True).create(environment)
    return environment / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")


@pytest.fixture
def local_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    index = tmp_path / "index"
    index.mkdir()
    for name in tuple(os.environ):
        if name.upper().startswith(("PIP_", "UV_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", str(index))
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_FIND_LINKS", str(index))
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
            + "\n"
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


def _uv() -> str:
    executable = shutil.which("uv")
    assert executable is not None, "uv is required by the contributor test suite"
    return executable


def test_stage_evaluates_direct_and_transitive_markers_for_each_target(
    tmp_path: Path, local_index: Path, pip_executable: Path
) -> None:
    for name in ("winleaf", "linleaf", "oldleaf", "newleaf", "rootwin", "extra_leaf"):
        make_wheel(local_index, name)
    make_wheel(
        local_index,
        "parentprobe",
        requires=(
            'winleaf; sys_platform == "win32"',
            'linleaf; sys_platform == "linux"',
            'oldleaf; python_version < "3.12"',
        ),
    )
    root = tmp_path / "project"
    _project(
        root,
        ["parentprobe", 'newleaf; python_version >= "3.12"', 'rootwin; sys_platform == "win32"'],
    )
    assert (
        main(
            [
                "stage",
                str(root),
                "--python",
                "3.11",
                "--python",
                "3.12",
                "--platform",
                "windows-x64",
                "--platform",
                "linux-x64",
                "--pip",
                str(pip_executable),
                "--uv",
                _uv(),
            ]
        )
        == 0
    )
    for python in ("3.11", "3.12"):
        for platform in ("windows-x64", "linux-x64"):
            source = root / "dist" / f"py{python}" / platform / "source"
            names = {path.name.split("-")[0] for path in (source / "wheels").iterdir()}
            expected = {"parentprobe", "oldleaf" if python == "3.11" else "newleaf"}
            expected |= {"winleaf", "rootwin"} if platform == "windows-x64" else {"linleaf"}
            assert names == expected
            manifest = tomlkit.parse((source / "blender_manifest.toml").read_text())
            assert manifest["platforms"] == [platform]
            assert len(manifest["wheels"]) == len(expected)


def test_linux_accepts_older_manylinux_and_stage_keeps_platform_versions_separate(
    tmp_path: Path, local_index: Path, pip_executable: Path
) -> None:
    make_wheel(
        local_index, "targetpkg", "1.0", tag="cp311-cp311-win_amd64", requires=("shareddep<2",)
    )
    make_wheel(
        local_index,
        "targetpkg",
        "2.0",
        tag="cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64",
        requires=("shareddep>=2",),
    )
    make_wheel(local_index, "shareddep", "1.0")
    make_wheel(local_index, "shareddep", "2.0")
    root = tmp_path / "project"
    _project(root, ["targetpkg"])
    assert (
        main(
            [
                "stage",
                str(root),
                "--python",
                "3.11",
                "--platform",
                "windows-x64",
                "--platform",
                "linux-x64",
                "--pip",
                str(pip_executable),
                "--uv",
                _uv(),
            ]
        )
        == 0
    )
    for platform, version in (("windows-x64", "1.0"), ("linux-x64", "2.0")):
        source = root / "dist" / "py3.11" / platform / "source"
        files = list((source / "wheels").glob("*.whl"))
        assert len(files) == 2
        assert all(path.name.split("-")[1] == version for path in files)


def test_failed_target_does_not_replace_any_existing_stages(
    tmp_path: Path, local_index: Path, pip_executable: Path
) -> None:
    make_wheel(local_index, "windowsonly", tag="cp311-cp311-win_amd64")
    root = tmp_path / "project"
    _project(root, ["windowsonly"])
    marker = root / "dist" / "py3.11" / "previous.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("keep", encoding="utf-8")
    assert (
        main(
            [
                "stage",
                str(root),
                "--python",
                "3.11",
                "--platform",
                "windows-x64",
                "--platform",
                "linux-x64",
                "--pip",
                str(pip_executable),
                "--uv",
                _uv(),
            ]
        )
        == 1
    )
    assert marker.read_text() == "keep"
    assert list(marker.parent.iterdir()) == [marker]
