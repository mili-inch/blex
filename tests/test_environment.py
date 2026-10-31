from __future__ import annotations

import platform
import sys

import pytest
from pip._vendor.packaging.markers import default_environment

from blex.core.environment import current_platform, current_python_version, resolution_environment
from blex.core.errors import BlexError


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Windows", "AMD64", "windows-x64"),
        ("Windows", "x86_64", "windows-x64"),
        ("Windows", "ARM64", "windows-arm64"),
        ("Windows", "aarch64", "windows-arm64"),
        ("Darwin", "x86_64", "macos-x64"),
        ("Darwin", "arm64", "macos-arm64"),
        ("Darwin", "aarch64", "macos-arm64"),
        ("Linux", "x86_64", "linux-x64"),
        ("Linux", "AMD64", "linux-x64"),
    ],
)
def test_platform_mapping(system: str, machine: str, expected: str) -> None:
    assert current_platform(system, machine) == expected


@pytest.mark.parametrize("system,machine", [("Linux", "aarch64"), ("FreeBSD", "amd64")])
def test_unsupported_platform(system: str, machine: str) -> None:
    with pytest.raises(BlexError, match="unsupported host"):
        current_platform(system, machine)


def test_native_environment_covers_pip_markers() -> None:
    actual = resolution_environment()
    for key, value in default_environment().items():
        assert actual[key] == value
    assert current_python_version() == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert actual["python_full_version"] == platform.python_version()
    assert actual["python_build"] == sys.version
    assert {"soabi", "sysconfig_platform", "libc", "macos_version"} <= actual.keys()
