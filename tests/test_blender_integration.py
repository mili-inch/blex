from __future__ import annotations

import os
from pathlib import Path

import pytest

import blex.main as main_module


def test_real_blender_dev_smoke(tmp_path: Path) -> None:
    configured = os.environ.get("BLEX_TEST_BLENDER")
    if configured is None or not configured.strip():
        pytest.skip("set BLEX_TEST_BLENDER to an explicit executable to run this test")

    executable = Path(configured).expanduser()
    assert executable.is_absolute(), "BLEX_TEST_BLENDER must be an absolute executable path"
    assert executable.is_file(), f"BLEX_TEST_BLENDER is not a file: {executable}"
    assert os.access(executable, os.X_OK), f"BLEX_TEST_BLENDER is not executable: {executable}"

    project = tmp_path / "real-blender-smoke"
    assert main_module.main(["init", str(project), "--name", "Real Blender Smoke"]) == 0

    module_name = "bl_ext.blex_dev.real_blender_smoke"
    status = main_module.main(
        [
            "dev",
            str(project),
            "--blender",
            str(executable),
            "--link-mode",
            "copy",
            "--no-reload-panel",
            "--no-debugpy",
            "--no-wheels",
            "--",
            "--background",
            "--python-expr",
            f"import sys; assert {module_name!r} in sys.modules",
        ]
    )
    assert status == 0
    assert (
        project
        / ".blex"
        / "runtime_environment"
        / "extensions"
        / "blex_dev"
        / "real_blender_smoke"
        / "blender_manifest.toml"
    ).is_file()
