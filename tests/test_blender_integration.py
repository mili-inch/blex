from __future__ import annotations

import os
from pathlib import Path

import pytest

import blex.main as main_module


def _blender() -> Path:
    configured = os.environ.get("BLEX_TEST_BLENDER")
    if configured is None or not configured.strip():
        pytest.skip("set BLEX_TEST_BLENDER to an explicit executable to run this test")

    executable = Path(configured).expanduser()
    assert executable.is_absolute(), "BLEX_TEST_BLENDER must be an absolute executable path"
    assert executable.is_file(), f"BLEX_TEST_BLENDER is not a file: {executable}"
    assert os.access(executable, os.X_OK), f"BLEX_TEST_BLENDER is not executable: {executable}"
    return executable


def test_real_blender_dev_smoke(tmp_path: Path) -> None:
    executable = _blender()

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


def test_real_blender_reload_imports_added_and_renamed_modules(tmp_path: Path) -> None:
    executable = _blender()
    project = tmp_path / "reload-regression"
    assert main_module.main(["init", str(project)]) == 0
    source = project / "src" / "reload_regression"
    probe = tmp_path / "link-probe"
    try:
        probe.symlink_to(source / "__init__.py")
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    probe.unlink()
    script = project / "verify_reload.py"
    script.write_text(
        f"""import bpy
import sys
from pathlib import Path
source = Path({str(source)!r})
initial = source / '__init__.py'
original = initial.read_text(encoding='utf-8')
module = 'bl_ext.blex_dev.reload_regression'
added = source / 'new_part.py'
added.write_text('VALUE = 42\\n', encoding='utf-8')
initial.write_text(original + '\\nfrom .new_part import VALUE\\n', encoding='utf-8')
assert bpy.ops.blex.reload_extension() == {{'FINISHED'}}
assert sys.modules[module].VALUE == 42
renamed = added.rename(source / 'renamed.py')
initial.write_text(original + '\\nfrom .renamed import VALUE\\n', encoding='utf-8')
assert bpy.ops.blex.reload_extension() == {{'FINISHED'}}
assert sys.modules[module].VALUE == 42
renamed.unlink()
initial.write_text(original, encoding='utf-8')
assert bpy.ops.blex.reload_extension() == {{'FINISHED'}}
assert module + '.new_part' not in sys.modules
assert module + '.renamed' not in sys.modules
""",
        encoding="utf-8",
        newline="\n",
    )
    assert (
        main_module.main(
            [
                "dev",
                str(project),
                "--blender",
                str(executable),
                "--no-wheels",
                "--link-mode",
                "symlink",
                "--",
                "--background",
                "--python",
                str(script),
            ]
        )
        == 0
    )
