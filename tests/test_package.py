from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tarfile
import subprocess
import zipfile
from importlib import metadata, resources

from blex import __version__


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEBUGPY_WHEEL = "debugpy-1.8.21-py2.py3-none-any.whl"
_DEBUGPY_SHA256 = "b1e37d333663c8851516a47364ef473da127f9caebe4417e6df6f5825a7e9a92"
_TEMPLATE_FILES = (
    "templates/bootstrap/dev.py",
    "templates/extension/__init__.py",
    "templates/extension/blender_manifest.toml",
    "templates/extension/operators.py",
    "templates/extension/panels.py",
    "templates/project/LICENSE",
    "templates/project/README.md",
    "templates/project/gitignore",
    "templates/project/pyproject.toml",
    "templates/project/python-version",
)


def test_distribution_metadata_matches_runtime_contract() -> None:
    distribution = metadata.distribution("blex")
    assert __version__ == "0.1.0"
    assert distribution.metadata["Name"] == "blex"
    assert distribution.metadata["License-Expression"] == "GPL-3.0-or-later"
    assert distribution.metadata.get_all("License-File") == ["LICENSE"]
    assert distribution.requires == ["tomlkit<0.16,>=0.15"]


def test_vendored_debugpy_wheel_and_license() -> None:
    package = resources.files("blex")
    wheel = package.joinpath("vendor", "debugpy-1.8.21-py2.py3-none-any.whl")
    license_file = package.joinpath("vendor", "licenses", "debugpy", "LICENSE")

    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == (
        "b1e37d333663c8851516a47364ef473da127f9caebe4417e6df6f5825a7e9a92"
    )
    assert "MIT License" in license_file.read_text(encoding="utf-8")


def test_built_wheel_contains_all_runtime_resources(tmp_path: Path) -> None:
    output_directory = tmp_path / "dist"
    environment = dict(os.environ)
    environment["PIP_NO_INDEX"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(output_directory)],
        cwd=_PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    wheels = tuple(output_directory.glob("blex-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        debugpy_path = f"blex/vendor/{_DEBUGPY_WHEEL}"
        debugpy_license_path = "blex/vendor/licenses/debugpy/LICENSE"
        assert debugpy_path in names
        assert hashlib.sha256(archive.read(debugpy_path)).hexdigest() == _DEBUGPY_SHA256
        assert (
            archive.read(debugpy_license_path)
            == (
                _PROJECT_ROOT / "src" / "blex" / "vendor" / "licenses" / "debugpy" / "LICENSE"
            ).read_bytes()
        )

        for relative in _TEMPLATE_FILES:
            artifact_path = f"blex/{relative}"
            assert artifact_path in names
            assert (
                archive.read(artifact_path)
                == (_PROJECT_ROOT / "src" / "blex" / Path(relative)).read_bytes()
            )

        root_licenses = tuple(
            name for name in names if name.endswith(".dist-info/licenses/LICENSE")
        )
        assert len(root_licenses) == 1
        assert archive.read(root_licenses[0]) == (_PROJECT_ROOT / "LICENSE").read_bytes()

    sdists = tuple(output_directory.glob("blex-*.tar.gz"))
    assert len(sdists) == 1
    with tarfile.open(sdists[0]) as archive:
        names = archive.getnames()
        prefix = names[0].split("/")[0]
        for relative in (
            *_TEMPLATE_FILES,
            f"vendor/{_DEBUGPY_WHEEL}",
            "vendor/licenses/debugpy/LICENSE",
        ):
            member = archive.extractfile(f"{prefix}/src/blex/{relative}")
            assert member is not None
            assert member.read() == (_PROJECT_ROOT / "src" / "blex" / relative).read_bytes()
