"""The native interpreter and platform used for dependency resolution."""

from __future__ import annotations

import os
import platform
import sys
import sysconfig

from blex.core.errors import BlexError


def current_python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def current_platform(system: str | None = None, machine: str | None = None) -> str:
    system_name = platform.system() if system is None else system
    machine_name = platform.machine() if machine is None else machine
    normalized_machine = machine_name.lower()
    if system_name == "Windows" and normalized_machine in {"amd64", "x86_64"}:
        return "windows-x64"
    if system_name == "Windows" and normalized_machine in {"arm64", "aarch64"}:
        return "windows-arm64"
    if system_name == "Darwin" and normalized_machine == "x86_64":
        return "macos-x64"
    if system_name == "Darwin" and normalized_machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system_name == "Linux" and normalized_machine in {"x86_64", "amd64"}:
        return "linux-x64"
    raise BlexError(f"unsupported host Platform for Wheel targeting: {system_name} {machine_name}")


def resolution_environment() -> dict[str, str]:
    """Capture marker values and native ABI details for the Wheel fingerprint."""
    implementation = sys.implementation.version
    implementation_version = f"{implementation.major}.{implementation.minor}.{implementation.micro}"
    if implementation.releaselevel != "final":
        implementation_version += implementation.releaselevel[0] + str(implementation.serial)
    return {
        "implementation_name": sys.implementation.name,
        "implementation_version": implementation_version,
        "os_name": os.name,
        "platform_machine": platform.machine(),
        "platform_release": platform.release(),
        "platform_system": platform.system(),
        "platform_version": platform.version(),
        "platform_python_implementation": platform.python_implementation(),
        "python_full_version": platform.python_version(),
        "python_version": current_python_version(),
        "sys_platform": sys.platform,
        "python_build": sys.version,
        "sysconfig_platform": sysconfig.get_platform(),
        "soabi": str(sysconfig.get_config_var("SOABI")),
        "libc": " ".join(platform.libc_ver()),
        "macos_version": platform.mac_ver()[0],
    }
