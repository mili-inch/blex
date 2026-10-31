from __future__ import annotations

import argparse
from pathlib import Path
from pathlib import PurePosixPath

import pytest
import tomlkit

from blex.commands import stage
from blex.core.cache import sha256_file
from blex.core.errors import BlexError
from blex.core.environment import NativeEnvironment
from blex.core.wheels import WheelArtifact


@pytest.fixture(autouse=True)
def native_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        stage,
        "capture_native_environment",
        lambda: NativeEnvironment(Path("python"), "windows-x64", {"python_version": "3.11"}),
    )


def _project(tmp_path: Path, *, python: str = "3.11") -> tuple[Path, Path]:
    root = tmp_path / "project"
    source = root / "src" / "demo_extension"
    source.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """[project]
name = "demo-extension"
dependencies = []

[tool.blex]
source-dir = "src/demo_extension"

[tool.blex.stage]
wheels = false
""",
        encoding="utf-8",
    )
    (root / ".python-version").write_text(f"{python}\n", encoding="utf-8")
    (source / "blender_manifest.toml").write_text(
        """schema_version = "1.0.0"
id = "demo_extension"
version = "1.0.0"
name = "Demo Extension"
type = "add-on"
platforms = ["macos-x64"]
wheels = ["./vendor/manual.whl"]
""",
        encoding="utf-8",
    )
    (source / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / ".hidden").write_text("hidden\n", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "kept.pyc").write_bytes(b"cache")
    manual = source / "vendor" / "manual.whl"
    manual.parent.mkdir()
    manual.write_bytes(b"manual")
    return root, source


def _args(root: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "directory": str(root),
        "source_dir": None,
        "output_dir": None,
        "wheels": None,
        "refresh_wheels": False,
        "verbose": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_stage_creates_final_source_without_changing_original(tmp_path: Path) -> None:
    root, source = _project(tmp_path)
    original_manifest = (source / "blender_manifest.toml").read_bytes()

    assert stage.run(_args(root)) == 0

    target = root / "dist" / "py3.11" / "windows-x64" / "source"
    assert (target / "__init__.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (target / ".hidden").is_file()
    assert (target / "__pycache__" / "kept.pyc").is_file()
    assert (target / "wheels").is_dir()
    assert (target / "vendor" / "manual.whl").read_bytes() == b"manual"
    generated = tomlkit.parse((target / "blender_manifest.toml").read_text(encoding="utf-8"))
    assert generated["platforms"] == ["windows-x64"]
    assert generated["wheels"] == ["./vendor/manual.whl"]
    assert (source / "blender_manifest.toml").read_bytes() == original_manifest
    assert list((root / ".blex" / "staging" / "stage").iterdir()) == []


def test_configured_source_does_not_derive_an_unused_default(tmp_path: Path) -> None:
    root, _source = _project(tmp_path)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'name = "demo-extension"', 'name = "demo.extension"'
        ),
        encoding="utf-8",
    )

    assert stage.run(_args(root)) == 0
    assert (root / "dist" / "py3.11" / "windows-x64" / "source" / "blender_manifest.toml").is_file()


def test_restage_replaces_selected_output(tmp_path: Path) -> None:
    root, _source = _project(tmp_path)
    assert stage.run(_args(root)) == 0
    marker = root / "dist" / "py3.11" / "windows-x64" / "old.txt"
    marker.write_text("replace", encoding="utf-8")

    assert stage.run(_args(root)) == 0

    assert not marker.exists()
    assert (root / "dist" / "py3.11" / "windows-x64" / "source").is_dir()


def test_stage_uses_current_platform_and_verbose_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _source = _project(tmp_path)
    assert stage.run(_args(root, verbose=True)) == 0
    output = capsys.readouterr().out
    assert "project root:" in output
    assert "Platform:" in output


def test_stage_rejects_missing_source_and_refresh_without_wheels(tmp_path: Path) -> None:
    root, source = _project(tmp_path)
    for child in sorted(source.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        else:
            child.rmdir()
    source.rmdir()
    with pytest.raises(BlexError, match="source-dir does not exist"):
        stage.run(_args(root))

    root, _source = _project(tmp_path / "second")
    with pytest.raises(BlexError, match="refresh-wheels"):
        stage.run(_args(root, refresh_wheels=True))


def test_stage_rejects_file_at_wheels_directory(tmp_path: Path) -> None:
    root, source = _project(tmp_path)
    (source / "wheels").write_bytes(b"file")
    with pytest.raises(BlexError, match="wheels path"):
        stage.run(_args(root))


def test_stage_rejects_output_and_selected_target_files(tmp_path: Path) -> None:
    root, _source = _project(tmp_path)
    with pytest.raises(BlexError, match="NUL"):
        stage.run(_args(root, output_dir="bad\x00output"))
    (root / "blocked").write_bytes(b"file")
    with pytest.raises(BlexError, match="output-dir"):
        stage.run(_args(root, output_dir="blocked"))

    target = root / "dist" / "py3.11" / "windows-x64"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"file")
    with pytest.raises(BlexError, match="selected stage target"):
        stage.run(_args(root))


def test_stage_wraps_managed_setup_filesystem_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _source = _project(tmp_path)

    def fail(_root: Path) -> None:
        raise PermissionError("simulated managed setup denial")

    monkeypatch.setattr(stage, "ensure_blex_root", fail)
    with pytest.raises(BlexError, match="stage filesystem operation failed"):
        stage.run(_args(root))


@pytest.mark.parametrize("output", ("src", ".blex/out", ".git/out", ".."))
def test_stage_rejects_unsafe_output_directories(tmp_path: Path, output: str) -> None:
    root, _source = _project(tmp_path)
    with pytest.raises(BlexError, match="output-dir"):
        stage.run(_args(root, output_dir=output))


def test_stage_refuses_abandoned_transaction(tmp_path: Path) -> None:
    root, _source = _project(tmp_path)
    abandoned = root / ".blex" / "staging" / "stage" / "deadbeef"
    abandoned.mkdir(parents=True)
    with pytest.raises(BlexError, match="abandoned"):
        stage.run(_args(root))
    assert abandoned.is_dir()


def test_commit_failure_rolls_back_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _source = _project(tmp_path)
    old_target = root / "dist" / "py3.11" / "windows-x64"
    old_target.mkdir(parents=True)
    old_marker = old_target / "old.txt"
    old_marker.write_text("original", encoding="utf-8")
    original_write = stage._write_transaction
    calls = 0

    def fail_after_install(run_root: Path, state: stage._TargetState) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated transaction record failure")
        original_write(run_root, state)

    monkeypatch.setattr(stage, "_write_transaction", fail_after_install)
    with pytest.raises(BlexError, match="filesystem operation failed"):
        stage.run(_args(root))

    assert old_marker.read_text(encoding="utf-8") == "original"
    assert not (old_target / "source").exists()
    assert list((root / ".blex" / "staging" / "stage").iterdir()) == []


@pytest.mark.parametrize(
    ("existing_target", "transition"),
    [(True, "backup"), (True, "install"), (False, "install")],
)
def test_interrupt_after_commit_rename_restores_original_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_target: bool,
    transition: str,
) -> None:
    root, _source = _project(tmp_path)
    target = root / "dist" / "py3.11" / "windows-x64"
    marker = target / "old.txt"
    if existing_target:
        target.mkdir(parents=True)
        marker.write_text("original", encoding="utf-8")

    real_rename = Path.rename
    interrupted = False

    def interrupt_once(path: Path, destination: str | Path) -> Path:
        nonlocal interrupted
        result = real_rename(path, destination)
        destination_path = Path(destination)
        is_selected_transition = (
            transition == "backup" and path == target and destination_path.name == "backup"
        ) or (transition == "install" and path.name == "prepared" and destination_path == target)
        if is_selected_transition and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(Path, "rename", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        stage.run(_args(root))

    assert interrupted is True
    if existing_target:
        assert marker.read_text(encoding="utf-8") == "original"
        assert not (target / "source").exists()
    else:
        assert not target.exists()
    assert list((root / ".blex" / "staging" / "stage").iterdir()) == []


def test_interrupt_after_stage_run_directory_creation_cleans_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _source = _project(tmp_path)
    real_mkdir = Path.mkdir
    interrupted = False

    def interrupt_once(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        nonlocal interrupted
        real_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
        if path.parent.name == "stage" and len(path.name) == 32 and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(Path, "mkdir", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        stage.run(_args(root))

    assert interrupted is True
    assert list((root / ".blex" / "staging" / "stage").iterdir()) == []


def test_prepare_target_copies_an_automatic_wheel(tmp_path: Path) -> None:
    root, source = _project(tmp_path)
    automatic = root / "cache" / "automatic.whl"
    automatic.parent.mkdir()
    automatic.write_bytes(b"automatic")
    artifact = WheelArtifact(
        automatic,
        PurePosixPath("wheels/automatic.whl"),
        sha256_file(automatic),
    )
    prepared = root / ".blex" / "test-prepared"
    manifest = tomlkit.parse((source / "blender_manifest.toml").read_text(encoding="utf-8"))

    stage._prepare_target(
        source_dir=source,
        prepared=prepared,
        manifest_document=manifest,
        platforms=("windows-x64",),
        artifacts=(artifact,),
    )

    assert (prepared / "source" / "wheels" / "automatic.whl").read_bytes() == b"automatic"


def test_rollback_refuses_a_missing_installed_target(tmp_path: Path) -> None:
    root = tmp_path / "project"
    run_root = root / ".blex" / "staging" / "stage" / ("a" * 32)
    run_root.mkdir(parents=True)
    state = stage._TargetState(
        python="3.11",
        identifier="py3.11",
        target=root / "dist" / "py3.11",
        prepared=run_root / "prepared" / "py3.11",
        backup=run_root / "backups" / "py3.11",
        state="installed",
    )
    with pytest.raises(BlexError, match="py3.11"):
        stage._rollback(root, run_root, state)


def test_native_stage_ignores_version_file_and_preserves_other_environments(tmp_path: Path) -> None:
    root, _source = _project(tmp_path, python="not-a-version")
    preserved = [root / "dist" / "py3.12", root / "dist" / "py3.11" / "linux-x64"]
    for target in preserved:
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("keep", encoding="utf-8")
    assert stage.run(_args(root)) == 0
    assert (root / "dist" / "py3.11" / "windows-x64" / "source").is_dir()
    for target in preserved:
        assert (target / "owned.txt").read_text(encoding="utf-8") == "keep"
