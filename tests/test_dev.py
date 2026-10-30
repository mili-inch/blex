from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
import types
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import tomlkit

from blex.commands import dev
from blex.core.errors import BlexError
from blex.core.manifest import load_manifest
from blex.core.wheels import WheelArtifact


def test_reload_reconciles_source_links_and_rejects_unsafe_additions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs, _root, source = _runtime_inputs(tmp_path)
    try:
        stage, mode = dev._prepare_runtime(inputs, link_mode="symlink")
    except BlexError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    assert mode == "symlink"
    repository = stage / "extensions" / "blex_dev"
    extension = repository / "demo_extension"
    repositories = _FakeRepositoryCollection()
    bpy, events = _fake_bpy(repositories)
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    script = dev._render_bootstrap(
        manifest_id="demo_extension",
        repository_root=repository,
        source_dir=source,
        sync_source_links=True,
        reload_panel=True,
        debugpy_root=None,
        debugpy_host="127.0.0.1",
        debugpy_port=5678,
    )
    namespace: dict[str, Any] = {}
    exec(compile(script, "bootstrap-dev.py", "exec"), namespace)
    (source / "new_part.py").write_text("VALUE = 42\n", encoding="utf-8")
    (source / "new_package").mkdir()
    (source / "new_package" / "__init__.py").write_text("", encoding="utf-8")
    namespace["_reload_extension"]()
    assert (extension / "new_part.py").read_text() == "VALUE = 42\n"
    assert (extension / "new_package" / "__init__.py").is_file()
    (extension / "runtime-owned.txt").write_text("keep", encoding="utf-8")
    (source / "new_part.py").rename(source / "renamed.py")
    namespace["_reload_extension"]()
    assert not (extension / "new_part.py").is_symlink()
    assert (extension / "renamed.py").is_symlink()
    assert (extension / "runtime-owned.txt").read_text() == "keep"
    outside = tmp_path / "outside.py"
    outside.write_text("external", encoding="utf-8")
    (source / "escape.py").symlink_to(outside)
    before = list(events)
    with pytest.raises(RuntimeError, match="escapes"):
        namespace["_reload_extension"]()
    assert events == before  # The existing add-on was not disabled.
    (source / "escape.py").unlink()
    (source / "runtime-owned.txt").write_text("collision", encoding="utf-8")
    with pytest.raises(RuntimeError, match="conflicts"):
        namespace["_reload_extension"]()
    assert (extension / "runtime-owned.txt").read_text() == "keep"


def _write_project(root: Path) -> Path:
    source = root / "src" / "demo_extension"
    source.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """[project]
name = "demo-extension"
dependencies = []

[tool.blex]
source-dir = "src/demo_extension"

[tool.blex.dev]
wheels = false
""",
        encoding="utf-8",
    )
    (source / "blender_manifest.toml").write_text(
        """schema_version = "1.0.0"
id = "demo_extension"
version = "1.0.0"
name = "Demo Extension"
type = "add-on"
platforms = ["macos-x64"]
""",
        encoding="utf-8",
    )
    (source / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / ".hidden").write_text("hidden\n", encoding="utf-8")
    return source


def _fake_executable(path: Path) -> Path:
    path.write_text("fake blender\n", encoding="utf-8")
    path.chmod(0o755)
    return path.resolve()


def _args(root: Path, blender_path: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "directory": str(root),
        "source_dir": None,
        "blender": str(blender_path),
        "blend_file": None,
        "user_resources": None,
        "link_mode": "copy",
        "reload_panel": None,
        "debugpy": None,
        "debugpy_host": None,
        "debugpy_port": None,
        "wheels": None,
        "python": None,
        "pip": None,
        "uv": None,
        "refresh_wheels": False,
        "blender_args": [],
        "verbose": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _runtime_inputs(
    tmp_path: Path, *, seed: Path | None = None
) -> tuple[dev._RuntimeInputs, Path, Path]:
    root = tmp_path / "project"
    source = _write_project(root)
    blex_root = dev._ensure_managed_root(root)
    manual = source / "vendor" / "manual.whl"
    manual.parent.mkdir()
    manual.write_bytes(b"manual")
    automatic = tmp_path / "automatic.whl"
    automatic.write_bytes(b"automatic")
    artifacts = (
        WheelArtifact(
            automatic,
            PurePosixPath("wheels", "automatic.whl"),
            dev._hash_file(automatic),
        ),
        WheelArtifact(
            manual,
            PurePosixPath("vendor", "manual.whl"),
            dev._hash_file(manual),
        ),
    )
    inputs = dev._RuntimeInputs(
        project_root=root,
        blex_root=blex_root,
        source_dir=source,
        manifest=load_manifest(source),
        platform="windows-x64",
        artifacts=artifacts,
        seed=seed,
    )
    return inputs, root, source


def test_run_creates_isolated_runtime_and_launches_exact_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    source = _write_project(root)
    executable = _fake_executable(root / "fake-blender")
    original_manifest = (source / "blender_manifest.toml").read_bytes()
    blend_file = root / "scene.blend"
    blend_file.write_bytes(b"BLENDER")
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "seeded.txt").write_text("seeded\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_run(command: tuple[str, ...], *, environment: dict[str, str]) -> int:
        captured["command"] = command
        captured["environment"] = environment
        return 19

    monkeypatch.setattr(dev, "current_platform", lambda: "windows-x64")
    monkeypatch.setattr(dev, "run_blender", fake_run)
    for variable in dev._BLENDER_ENVIRONMENT_OVERRIDES:
        monkeypatch.setenv(variable, "parent-value")

    result = dev.run(
        _args(
            root,
            executable,
            blend_file="scene.blend",
            user_resources=str(seed),
            blender_args=["--background", "--factory-startup"],
        )
    )
    assert result == 19

    runtime = root / ".blex" / "runtime_environment"
    extension = runtime / "extensions" / "blex_dev" / "demo_extension"
    assert (runtime / "seeded.txt").read_text(encoding="utf-8") == "seeded\n"
    assert (extension / "__init__.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (extension / ".hidden").is_file()
    assert (extension / "wheels").is_dir()
    generated = tomlkit.parse((extension / "blender_manifest.toml").read_text(encoding="utf-8"))
    assert generated["platforms"] == ["windows-x64"]
    assert "wheels" not in generated
    assert (source / "blender_manifest.toml").read_bytes() == original_manifest

    command = captured["command"]
    bootstrap = root / ".blex" / "bootstrap" / "dev.py"
    assert command == (
        str(executable),
        str(blend_file.resolve()),
        "--python-exit-code",
        "1",
        "--python",
        str(bootstrap.resolve()),
        "--background",
        "--factory-startup",
    )
    environment = captured["environment"]
    assert environment["BLENDER_USER_RESOURCES"] == str(runtime.resolve())
    for variable in dev._BLENDER_ENVIRONMENT_OVERRIDES:
        assert variable not in environment
    script = bootstrap.read_text(encoding="utf-8")
    assert "_MANIFEST_ID: str = 'demo_extension'" in script
    assert "_REPOSITORY_ROOT: str = " in script
    assert not (root / ".python-version").exists()


def test_run_replaces_runtime_from_seed_each_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    executable = _fake_executable(root / "fake-blender")
    seed = tmp_path / "seed"
    seed.mkdir()
    marker = seed / "marker.txt"
    marker.write_text("first", encoding="utf-8")
    monkeypatch.setattr(dev, "current_platform", lambda: "linux-x64")
    monkeypatch.setattr(dev, "run_blender", lambda *_args, **_kwargs: 0)

    args = _args(root, executable, user_resources=str(seed))
    assert dev.run(args) == 0
    runtime_marker = root / ".blex" / "runtime_environment" / "marker.txt"
    assert runtime_marker.read_text(encoding="utf-8") == "first"
    (root / ".blex" / "runtime_environment" / "old-only.txt").write_text("old", encoding="utf-8")
    marker.write_text("second", encoding="utf-8")

    assert dev.run(args) == 0
    assert runtime_marker.read_text(encoding="utf-8") == "second"
    assert not (runtime_marker.parent / "old-only.txt").exists()


def test_run_validates_disabled_feature_options_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    executable = _fake_executable(root / "fake-blender")
    monkeypatch.setattr(dev, "current_platform", lambda: "linux-x64")
    with pytest.raises(BlexError, match="host or port"):
        dev.run(_args(root, executable, debugpy_host="127.0.0.1"))
    with pytest.raises(BlexError, match="refresh-wheels"):
        dev.run(_args(root, executable, refresh_wheels=True))
    with pytest.raises(BlexError, match="invalid Python version"):
        dev.run(_args(root, executable, python="03.11"))


def test_run_validates_enabled_values_and_verbose_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    executable = _fake_executable(root / "fake-blender")
    (root / ".python-version").write_text("3.11\n", encoding="utf-8")
    seed = tmp_path / "seed"
    seed.mkdir()
    monkeypatch.setattr(dev, "current_platform", lambda: "linux-x64")
    monkeypatch.setattr(dev, "run_blender", lambda *_args, **_kwargs: 0)

    assert (
        dev.run(
            _args(
                root,
                executable,
                wheels=True,
                user_resources=str(seed),
                verbose=True,
            )
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "project root:" in output
    assert "source directory:" in output
    assert "runtime directory:" in output
    assert "user resources seed:" in output
    assert "Blender command:" in output

    with pytest.raises(BlexError, match="host must not be empty"):
        dev.run(_args(root, executable, debugpy=True, debugpy_host="   "))
    with pytest.raises(BlexError, match="port must be"):
        dev.run(_args(root, executable, debugpy=True, debugpy_port=0))
    with pytest.raises(BlexError, match="NUL"):
        dev.run(_args(root, executable, blend_file="bad\x00blend"))
    with pytest.raises(BlexError, match="NUL"):
        dev.run(_args(root, executable, blender_args=["bad\x00argument"]))
    with pytest.raises(BlexError, match="Blender executable must not be empty"):
        dev.run(_args(root, executable, blender=""))
    with pytest.raises(BlexError, match="pip executable must not be empty"):
        dev.run(_args(root, executable, pip=" \t "))
    with pytest.raises(BlexError, match="drive-relative"):
        dev.run(_args(root, executable, pip="C:"))
    assert dev.run(_args(root, executable, pip="definitely-missing-blex-test-pip")) == 0


def test_run_rejects_missing_source_directory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "missing-source"\ndependencies = []\n',
        encoding="utf-8",
    )
    executable = _fake_executable(root / "fake-blender")
    with pytest.raises(BlexError, match="source directory does not exist"):
        dev.run(_args(root, executable))


def test_run_wraps_unhandled_filesystem_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    executable = _fake_executable(root / "fake-blender")

    def fail(_args: argparse.Namespace, _root: Path) -> int:
        raise PermissionError("simulated denial")

    monkeypatch.setattr(dev, "_run_locked", fail)
    with pytest.raises(BlexError, match="dev filesystem operation failed"):
        dev.run(_args(root, executable))


def test_runtime_copy_places_artifacts_and_preserves_seed(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "config.txt").write_text("seed\n", encoding="utf-8")
    inputs, _root, source = _runtime_inputs(tmp_path, seed=seed)
    (source / "wheels").mkdir()
    (source / "wheels" / "unreferenced.whl").write_bytes(b"unused")

    stage, _mode = dev._prepare_runtime(inputs, link_mode="copy")
    extension = stage / "extensions" / "blex_dev" / "demo_extension"
    assert not (extension / "__init__.py").is_symlink()
    assert (extension / "vendor" / "manual.whl").read_bytes() == b"manual"
    assert (extension / "wheels" / "automatic.whl").read_bytes() == b"automatic"
    assert not (extension / "wheels" / "unreferenced.whl").exists()
    assert (stage / "config.txt").read_text(encoding="utf-8") == "seed\n"
    generated = tomlkit.parse((extension / "blender_manifest.toml").read_text(encoding="utf-8"))
    assert generated["platforms"] == ["windows-x64"]
    assert generated["wheels"] == [
        "./wheels/automatic.whl",
        "./vendor/manual.whl",
    ]


def test_runtime_rejects_non_directory_source_wheels(tmp_path: Path) -> None:
    inputs, _root, source = _runtime_inputs(tmp_path)
    (source / "wheels").write_bytes(b"not a directory")

    with pytest.raises(BlexError, match="source wheels entry is not a directory"):
        dev._prepare_runtime(inputs, link_mode="copy")
    assert not list(inputs.blex_root.glob(".tmp-*"))


def test_runtime_accepts_internal_symlinked_source_wheels(tmp_path: Path) -> None:
    inputs, _root, source = _runtime_inputs(tmp_path)
    real_wheels = source / "real-wheels"
    real_wheels.mkdir()
    (real_wheels / "unreferenced.whl").write_bytes(b"unused")
    try:
        (source / "wheels").symlink_to(real_wheels, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    stage, _mode = dev._prepare_runtime(inputs, link_mode="copy")
    extension = stage / "extensions" / "blex_dev" / "demo_extension"
    assert (extension / "wheels").is_dir()
    assert not (extension / "wheels").is_symlink()
    assert not (extension / "wheels" / "unreferenced.whl").exists()


def test_runtime_symlink_mode_links_sources_and_wheels(tmp_path: Path) -> None:
    inputs, _root, _source = _runtime_inputs(tmp_path)
    try:
        stage, _mode = dev._prepare_runtime(inputs, link_mode="symlink")
    except BlexError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    extension = stage / "extensions" / "blex_dev" / "demo_extension"
    assert (extension / "__init__.py").is_symlink()
    assert (extension / "vendor").is_symlink()
    assert (extension / "wheels").is_dir()
    assert not (extension / "wheels").is_symlink()
    assert (extension / "wheels" / "automatic.whl").is_symlink()
    assert not (extension / "blender_manifest.toml").is_symlink()


def test_runtime_auto_falls_back_as_one_copy_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs, _root, _source = _runtime_inputs(tmp_path)
    calls = 0
    original = dev._create_symlink

    def fail_first(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise dev._SymlinkFailure("simulated denial")
        original(source, destination)

    monkeypatch.setattr(dev, "_create_symlink", fail_first)
    stage, _mode = dev._prepare_runtime(inputs, link_mode="auto")
    extension = stage / "extensions" / "blex_dev" / "demo_extension"
    assert not (extension / "__init__.py").is_symlink()
    assert not (extension / "wheels" / "automatic.whl").is_symlink()
    assert "warning: symlink deployment failed" in capsys.readouterr().err


def test_runtime_rejects_reserved_repository_in_seed(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    (seed / "extensions" / "blex_dev").mkdir(parents=True)
    inputs, _root, _source = _runtime_inputs(tmp_path, seed=seed)
    with pytest.raises(BlexError, match="reserved repository"):
        dev._prepare_runtime(inputs, link_mode="copy")
    assert not list(inputs.blex_root.glob(".tmp-*"))


def test_seed_copy_dereferences_links_and_detects_cycles(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = source / "target"
    target.mkdir()
    (target / "value.txt").write_text("value", encoding="utf-8")
    link = source / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    destination = tmp_path / "destination"
    dev._copy_dereferenced(source, destination)
    assert not (destination / "link").is_symlink()
    assert (destination / "link" / "value.txt").read_text(encoding="utf-8") == "value"

    cycle = target / "cycle"
    cycle.symlink_to(source, target_is_directory=True)
    with pytest.raises(BlexError, match="cycle"):
        dev._copy_dereferenced(source, tmp_path / "cycle-copy")


def test_runtime_install_rolls_back_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blex_root = tmp_path / ".blex"
    blex_root.mkdir()
    runtime = blex_root / "runtime_environment"
    runtime.mkdir()
    (runtime / "old.txt").write_text("old", encoding="utf-8")
    stage = blex_root / ".runtime_environment.stage.tmp"
    stage.mkdir()
    (stage / "new.txt").write_text("new", encoding="utf-8")
    original_replace = dev.os.replace
    calls = 0

    def fail_install(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated install failure")
        original_replace(source, destination)

    monkeypatch.setattr(dev.os, "replace", fail_install)
    with pytest.raises(BlexError, match="could not replace"):
        dev._install_runtime(stage, runtime, blex_root=blex_root)
    assert (runtime / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (runtime / "new.txt").exists()
    assert not stage.exists()


@pytest.mark.parametrize(
    ("transition", "had_runtime"),
    (("backup", True), ("install", True), ("install", False)),
    ids=("backup-existing", "install-existing", "install-empty"),
)
def test_runtime_install_rolls_back_interrupt_after_real_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
    had_runtime: bool,
) -> None:
    blex_root = tmp_path / ".blex"
    blex_root.mkdir()
    runtime = blex_root / "runtime_environment"
    if had_runtime:
        runtime.mkdir()
        (runtime / "old.txt").write_text("old", encoding="utf-8")
    stage = blex_root / ".runtime_environment.stage.tmp"
    stage.mkdir()
    (stage / "new.txt").write_text("new", encoding="utf-8")
    original_replace = dev.os.replace
    interrupted = False

    def interrupt_after_replace(source: Path, destination: Path) -> None:
        nonlocal interrupted
        original_replace(source, destination)
        is_backup = source == runtime and destination.name.startswith(".backup-")
        is_install = source == stage and destination == runtime
        if not interrupted and (
            (transition == "backup" and is_backup) or (transition == "install" and is_install)
        ):
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(dev.os, "replace", interrupt_after_replace)
    with pytest.raises(KeyboardInterrupt):
        dev._install_runtime(stage, runtime, blex_root=blex_root)

    assert interrupted is True
    if had_runtime:
        assert (runtime / "old.txt").read_text(encoding="utf-8") == "old"
        assert not (runtime / "new.txt").exists()
    else:
        assert not dev._lexists(runtime)
    assert not dev._lexists(stage)
    assert list(blex_root.glob(".backup-*")) == []


def test_runtime_backup_cleanup_failure_keeps_installed_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blex_root = tmp_path / ".blex"
    blex_root.mkdir()
    runtime = blex_root / "runtime_environment"
    runtime.mkdir()
    (runtime / "old.txt").write_text("old", encoding="utf-8")
    stage = blex_root / ".runtime_environment.stage.tmp"
    stage.mkdir()
    (stage / "new.txt").write_text("new", encoding="utf-8")
    original_remove = dev._safe_remove_tree

    def fail_partial_backup_cleanup(path: Path, *, managed_root: Path) -> None:
        if path.name.startswith(".backup-"):
            (path / "old.txt").unlink()
            raise OSError("simulated partial backup cleanup failure")
        original_remove(path, managed_root=managed_root)

    monkeypatch.setattr(dev, "_safe_remove_tree", fail_partial_backup_cleanup)
    with pytest.raises(BlexError, match="was installed.*backup"):
        dev._install_runtime(stage, runtime, blex_root=blex_root)

    assert (runtime / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (runtime / "old.txt").exists()
    assert not dev._lexists(stage)
    backups = list(blex_root.glob(".backup-*"))
    assert len(backups) == 1
    assert not (backups[0] / "old.txt").exists()


def test_runtime_stage_creation_interrupt_cleans_created_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs, _root, _source = _runtime_inputs(tmp_path)
    original_mkdir = Path.mkdir
    interrupted = False

    def interrupt_after_mkdir(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        nonlocal interrupted
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
        if not interrupted and path.parent == inputs.blex_root and path.name.startswith(".tmp-"):
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(Path, "mkdir", interrupt_after_mkdir)
    with pytest.raises(KeyboardInterrupt):
        dev._prepare_runtime(inputs, link_mode="copy")

    assert interrupted is True
    assert not list(inputs.blex_root.glob(".tmp-*"))


def test_managed_temporary_names_are_short(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    identifier = "a" * 32
    monkeypatch.setattr(dev.uuid, "uuid4", lambda: types.SimpleNamespace(hex=identifier))

    temporary = dev._temporary_path(tmp_path)
    assert temporary == tmp_path / f".tmp-{identifier}"
    assert len(temporary.name) == 37


@pytest.mark.skipif(os.name != "nt", reason="Windows legacy path limit regression")
def test_runtime_creation_with_long_windows_project_path(tmp_path: Path) -> None:
    stage_suffix = (
        Path("project")
        / ".blex"
        / f".tmp-{'a' * 32}"
        / "extensions"
        / "blex_dev"
        / "demo_extension"
        / f".tmp-{'b' * 32}"
    )
    padding_length = 245 - len(str(tmp_path / stage_suffix)) - 1
    if padding_length < 1:
        pytest.skip("pytest temporary root is too long for the regression fixture")
    padded_root = tmp_path / ("p" * padding_length)
    inputs, _root, _source = _runtime_inputs(padded_root)

    stage, _mode = dev._prepare_runtime(inputs, link_mode="copy")
    atomic_candidate = stage / "extensions" / "blex_dev" / "demo_extension" / f".tmp-{'b' * 32}"

    assert len(str(atomic_candidate)) == 245
    assert (
        stage / "extensions" / "blex_dev" / "demo_extension" / "blender_manifest.toml"
    ).is_file()


def test_dev_lock_is_nonblocking_and_reusable(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with dev._DevLock(root):
        with pytest.raises(BlexError, match="holds the project lock"):
            with dev._DevLock(root):
                pass
    with dev._DevLock(root):
        pass
    assert (root / ".blex" / "locks" / "dev.lock").is_file()


def test_managed_gitignore_is_merged_without_replacement(tmp_path: Path) -> None:
    root = tmp_path / "project"
    managed = root / ".blex"
    managed.mkdir(parents=True)
    ignore = managed / ".gitignore"
    ignore.write_text("custom\n*\n", encoding="utf-8")
    assert dev._ensure_managed_root(root) == managed
    assert ignore.read_text(encoding="utf-8") == "custom\n*\n!.gitignore\n"


def test_managed_path_guards_fail_before_mutating_unexpected_paths(tmp_path: Path) -> None:
    managed = tmp_path / ".blex"
    managed.mkdir()
    unexpected_file = managed / "runtime_environment"
    unexpected_file.write_text("owned", encoding="utf-8")
    with pytest.raises(BlexError, match="not a directory"):
        dev._validate_managed_directory(unexpected_file, label="runtime")
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(BlexError, match="outside managed root"):
        dev._safe_remove_tree(outside, managed_root=managed)
    assert dev._is_link_like(tmp_path / "absent") is False
    dev._safe_remove_tree(managed / "absent", managed_root=managed)

    ignore = managed / ".gitignore"
    ignore.mkdir()
    with pytest.raises(BlexError, match="not a regular file"):
        dev._ensure_blex_gitignore(managed)


def test_input_seed_and_deployment_validation_errors(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    file_path = root / "file"
    file_path.write_text("value", encoding="utf-8")
    directory = root / "directory"
    directory.mkdir()
    with pytest.raises(BlexError, match="does not exist"):
        dev._resolve_input_path(root, "missing", label="input", directory=False)
    with pytest.raises(BlexError, match="not a directory"):
        dev._resolve_input_path(root, "file", label="input", directory=True)
    with pytest.raises(BlexError, match="not a regular file"):
        dev._resolve_input_path(root, "directory", label="input", directory=False)

    dev._validate_seed(None, root / ".blex" / "runtime_environment")
    with pytest.raises(BlexError, match="must not contain each other"):
        dev._validate_seed(root, root / ".blex" / "runtime_environment")
    with pytest.raises(BlexError, match="could not hash"):
        dev._hash_file(root / "missing")
    with pytest.raises(AssertionError, match="deployment mode"):
        dev._place_source_entry(file_path, root / "placed", mode="invalid")
    with pytest.raises(BlexError, match="invalid Wheel deployment path"):
        dev._artifact_destination(root, PurePosixPath("/absolute.whl"))
    with pytest.raises(BlexError, match="destination parent is not a directory"):
        dev._ensure_real_parents(file_path / "nested", stop=root)

    extension = root / "extension"
    extension.mkdir()
    source_wheel = root / "source.whl"
    source_wheel.write_bytes(b"expected")
    deployed = extension / "wheel.whl"
    deployed.write_bytes(b"different")
    artifact = WheelArtifact(
        source_wheel,
        PurePosixPath("wheel.whl"),
        hashlib.sha256(b"expected").hexdigest(),
    )
    with pytest.raises(BlexError, match="conflicts"):
        dev._place_artifact(artifact, extension, mode="copy")
    deployed.unlink()
    with pytest.raises(AssertionError, match="deployment mode"):
        dev._place_artifact(artifact, extension, mode="invalid")


def test_runtime_rejects_unknown_link_mode(tmp_path: Path) -> None:
    inputs, _root, _source = _runtime_inputs(tmp_path)
    with pytest.raises(BlexError, match="invalid dev link mode"):
        dev._prepare_runtime(inputs, link_mode="junction")


def test_debugpy_is_extracted_and_existing_directory_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blex_root = tmp_path / ".blex"
    blex_root.mkdir()
    target = dev._prepare_debugpy(blex_root)
    assert target == blex_root / "debugpy" / dev._DEBUGPY_VERSION
    assert (target / "debugpy" / "__init__.py").is_file()
    assert not (target / ".blex-metadata.json").exists()

    corrupted = target / "debugpy" / "__init__.py"
    corrupted.write_bytes(b"corrupt")

    def unexpected_resource_access() -> Any:
        raise AssertionError("the bundled Wheel must not be inspected when debugpy exists")

    monkeypatch.setattr(dev, "_debugpy_resource", unexpected_resource_access)
    assert dev._prepare_debugpy(blex_root) == target
    assert corrupted.read_bytes() == b"corrupt"


def test_debugpy_existing_target_must_be_a_directory(tmp_path: Path) -> None:
    blex_root = tmp_path / ".blex"
    target = blex_root / "debugpy" / dev._DEBUGPY_VERSION
    target.parent.mkdir(parents=True)
    target.write_text("not a directory", encoding="utf-8")

    with pytest.raises(BlexError, match="debugpy extraction directory is not a directory"):
        dev._prepare_debugpy(blex_root)


def test_debugpy_extraction_rejects_bad_archives_and_unsafe_members(tmp_path: Path) -> None:
    bad = tmp_path / "bad.whl"
    bad.write_bytes(b"not a zip")
    destination = tmp_path / "bad-output"
    destination.mkdir()
    with pytest.raises(BlexError, match="not a valid archive"):
        dev._extract_debugpy_wheel(bad, destination)

    def archive_with(info: zipfile.ZipInfo) -> Path:
        archive = tmp_path / f"{len(list(tmp_path.glob('member-*.whl')))}-member.whl"
        with zipfile.ZipFile(archive, "w") as wheel:
            wheel.writestr(info, b"value")
        return archive

    symlink_info = zipfile.ZipInfo("link.py")
    symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    symlink_wheel = archive_with(symlink_info)
    (tmp_path / "symlink-output").mkdir()
    with pytest.raises(BlexError, match="symbolic link"):
        dev._extract_debugpy_wheel(symlink_wheel, tmp_path / "symlink-output")

    special_info = zipfile.ZipInfo("pipe")
    special_info.external_attr = (stat.S_IFIFO | 0o600) << 16
    special_wheel = archive_with(special_info)
    (tmp_path / "special-output").mkdir()
    with pytest.raises(BlexError, match="special file"):
        dev._extract_debugpy_wheel(special_wheel, tmp_path / "special-output")


def test_debugpy_prepare_failure_cleans_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blex_root = tmp_path / ".blex"
    blex_root.mkdir()
    monkeypatch.setattr(dev, "_DEBUGPY_SHA256", "0" * 64)
    with pytest.raises(BlexError, match="hash mismatch"):
        dev._prepare_debugpy(blex_root)
    debugpy_root = blex_root / "debugpy"
    assert not list(debugpy_root.glob(".*.tmp"))


def test_debugpy_temporary_creation_interrupt_cleans_created_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blex_root = tmp_path / ".blex"
    blex_root.mkdir()
    original_mkdir = Path.mkdir
    interrupted = False

    def interrupt_after_mkdir(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        nonlocal interrupted
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
        if not interrupted and path.name.startswith(f".{dev._DEBUGPY_VERSION}."):
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(Path, "mkdir", interrupt_after_mkdir)
    with pytest.raises(KeyboardInterrupt):
        dev._prepare_debugpy(blex_root)

    assert interrupted is True
    debugpy_root = blex_root / "debugpy"
    assert not list(debugpy_root.glob(".*.tmp"))
    assert not (debugpy_root / dev._DEBUGPY_VERSION).exists()


@pytest.mark.parametrize(
    "path",
    ("../escape.py", "/absolute.py", "dir\\file.py", "C:/drive.py", "a//b.py"),
)
def test_debugpy_archive_paths_are_confined(path: str) -> None:
    with pytest.raises(BlexError, match="unsafe path"):
        dev._safe_archive_path(path, directory=False)


class _FakeRepositoryCollection(list[Any]):
    def __init__(self, entries: list[Any] | None = None) -> None:
        super().__init__(entries or [])
        self.created: Any | None = None

    def new(self, **values: Any) -> Any:
        repository = types.SimpleNamespace(
            directory=values["custom_directory"],
            enabled=False,
            use_custom_directory=False,
            use_remote_url=True,
            use_sync_on_startup=True,
            **values,
        )
        self.created = repository
        self.append(repository)
        return repository


class _FakeOperator:
    reports: list[tuple[set[str], str]]

    def __init__(self) -> None:
        self.reports = []

    def report(self, levels: set[str], message: str) -> None:
        self.reports.append((levels, message))


class _FakePanel:
    pass


def _fake_bpy(repositories: _FakeRepositoryCollection) -> tuple[types.ModuleType, list[Any]]:
    events: list[Any] = []
    module = types.ModuleType("bpy")
    bpy: Any = module
    bpy.context = types.SimpleNamespace(
        preferences=types.SimpleNamespace(
            extensions=types.SimpleNamespace(repos=repositories),
        )
    )

    def refresh() -> set[str]:
        events.append("refresh")
        return {"FINISHED"}

    def enable(*, module: str) -> set[str]:
        events.append(("enable", module))
        return {"FINISHED"}

    def disable(*, module: str) -> set[str]:
        events.append(("disable", module))
        return {"FINISHED"}

    bpy.ops = types.SimpleNamespace(
        extensions=types.SimpleNamespace(repo_refresh_all=refresh),
        preferences=types.SimpleNamespace(addon_enable=enable, addon_disable=disable),
    )
    bpy.types = types.SimpleNamespace(Operator=_FakeOperator, Panel=_FakePanel)
    bpy.utils = types.SimpleNamespace(
        register_class=lambda value: events.append(("register", value))
    )
    return module, events


def test_bootstrap_adds_local_repository_enables_and_registers_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repositories = _FakeRepositoryCollection()
    bpy, events = _fake_bpy(repositories)
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    repository_root = tmp_path / "runtime" / "extensions" / "blex_dev"
    repository_root.mkdir(parents=True)
    script = dev._render_bootstrap(
        manifest_id="demo_extension",
        repository_root=repository_root,
        reload_panel=True,
        debugpy_root=None,
        debugpy_host="127.0.0.1",
        debugpy_port=5678,
        source_dir=tmp_path / "source",
        sync_source_links=False,
    )
    namespace: dict[str, Any] = {"__name__": "__main__"}
    exec(compile(script, "bootstrap-dev.py", "exec"), namespace)

    created = repositories.created
    assert created is not None
    assert created.name == "Blex Development"
    assert created.module == "blex_dev"
    assert created.source == "USER"
    assert created.remote_url == ""
    assert created.enabled is True
    assert created.use_custom_directory is True
    assert created.use_remote_url is False
    assert created.use_sync_on_startup is False
    module_name = "bl_ext.blex_dev.demo_extension"
    assert events[:2] == ["refresh", ("enable", module_name)]
    registered = [
        entry[1] for entry in events if isinstance(entry, tuple) and entry[0] == "register"
    ]
    assert len(registered) == 2

    monkeypatch.setitem(sys.modules, module_name, types.ModuleType(module_name))
    monkeypatch.setitem(sys.modules, module_name + ".nested", types.ModuleType("nested"))
    operator = registered[0]()
    assert operator.execute(None) == {"FINISHED"}
    assert module_name not in sys.modules
    assert module_name + ".nested" not in sys.modules
    assert "[reload_extension] purged modules: 2" in capsys.readouterr().out


def test_bootstrap_starts_only_the_configured_bundled_debugpy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debugpy_root = tmp_path / "debugpy-bundle"
    debugpy_root.mkdir()
    log = tmp_path / "listen.txt"
    (debugpy_root / "debugpy.py").write_text(
        "__version__ = '1.8.21'\n"
        f"def listen(value):\n    open({str(log)!r}, 'w').write(repr(value))\n",
        encoding="utf-8",
    )
    for name in tuple(sys.modules):
        if name == "debugpy" or name.startswith("debugpy."):
            monkeypatch.delitem(sys.modules, name)
    repositories = _FakeRepositoryCollection()
    bpy, _events = _fake_bpy(repositories)
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    repository_root = tmp_path / "runtime" / "extensions" / "blex_dev"
    repository_root.mkdir(parents=True)
    script = dev._render_bootstrap(
        manifest_id="demo_extension",
        repository_root=repository_root,
        reload_panel=False,
        debugpy_root=debugpy_root,
        debugpy_host="127.0.0.1",
        debugpy_port=5678,
        source_dir=tmp_path / "source",
        sync_source_links=False,
    )
    try:
        exec(compile(script, "bootstrap-dev.py", "exec"), {"__name__": "__main__"})
        assert log.read_text(encoding="utf-8") == "('127.0.0.1', 5678)"
    finally:
        sys.modules.pop("debugpy", None)
        if str(debugpy_root) in sys.path:
            sys.path.remove(str(debugpy_root))


def test_bootstrap_rejects_existing_repository_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = types.SimpleNamespace(module="blex_dev", directory=str(tmp_path / "existing"))
    bpy, _events = _fake_bpy(_FakeRepositoryCollection([existing]))
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    script = dev._render_bootstrap(
        manifest_id="demo_extension",
        repository_root=tmp_path / "new",
        reload_panel=False,
        debugpy_root=None,
        debugpy_host="127.0.0.1",
        debugpy_port=5678,
        source_dir=tmp_path / "source",
        sync_source_links=False,
    )
    with pytest.raises(RuntimeError, match="repository module already exists"):
        exec(compile(script, "bootstrap-dev.py", "exec"), {"__name__": "__main__"})


def test_bootstrap_rejects_same_id_in_another_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_root = tmp_path / "other"
    extension = other_root / "different_folder"
    extension.mkdir(parents=True)
    (extension / "blender_manifest.toml").write_text('id = "demo_extension"\n', encoding="utf-8")
    existing = types.SimpleNamespace(module="other", directory=str(other_root))
    bpy, _events = _fake_bpy(_FakeRepositoryCollection([existing]))
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    script = dev._render_bootstrap(
        manifest_id="demo_extension",
        repository_root=tmp_path / "new",
        reload_panel=False,
        debugpy_root=None,
        debugpy_host="127.0.0.1",
        debugpy_port=5678,
        source_dir=tmp_path / "source",
        sync_source_links=False,
    )
    with pytest.raises(RuntimeError, match="already exists in repository"):
        exec(compile(script, "bootstrap-dev.py", "exec"), {"__name__": "__main__"})


def test_bootstrap_fails_closed_on_unreadable_repository_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_root = tmp_path / "other"
    extension = other_root / "broken_extension"
    extension.mkdir(parents=True)
    (extension / "blender_manifest.toml").write_text("not = [valid", encoding="utf-8")
    existing = types.SimpleNamespace(module="other", directory=str(other_root))
    bpy, _events = _fake_bpy(_FakeRepositoryCollection([existing]))
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    script = dev._render_bootstrap(
        manifest_id="demo_extension",
        repository_root=tmp_path / "new",
        reload_panel=False,
        debugpy_root=None,
        debugpy_host="127.0.0.1",
        debugpy_port=5678,
        source_dir=tmp_path / "source",
        sync_source_links=False,
    )
    with pytest.raises(RuntimeError, match="could not parse extension manifest"):
        exec(compile(script, "bootstrap-dev.py", "exec"), {"__name__": "__main__"})
