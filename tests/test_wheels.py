from __future__ import annotations

import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import subprocess
from typing import Any

import pytest
import tomlkit

from blex.adapters import pip as pip_adapter
from blex.core.cache import (
    CachedWheel,
    FileLock,
    cache_entry_path,
    sha256_file,
    validate_cache_entry,
    wheel_fingerprint,
    write_cache_metadata,
)
from blex.core.errors import BlexError
from blex.core import wheels


def _source(tmp_path: Path, manifest_text: str = 'schema_version = "1.0.0"\n') -> Path:
    source = tmp_path / "src" / "extension"
    source.mkdir(parents=True)
    (source / "blender_manifest.toml").write_text(manifest_text, encoding="utf-8")
    return source


def test_python_and_platform_normalization() -> None:
    assert wheels.normalize_python_version(" 3.11 ") == "3.11"
    assert wheels.normalize_python_versions(("3.14", "2.7", "3.11")) == (
        "2.7",
        "3.11",
        "3.14",
    )
    assert wheels.build_id("3.11") == "py3.11"
    with pytest.raises(BlexError):
        wheels.normalize_python_version("03.11")
    with pytest.raises(BlexError):
        wheels.normalize_python_versions(("3.11", "3.11"))

    assert wheels.normalize_platforms(("linux-x64", "windows-x64")) == (
        "windows-x64",
        "linux-x64",
    )
    assert wheels.current_platform("Windows", "AMD64") == "windows-x64"
    assert wheels.current_platform("Windows", "ARM64") == "windows-arm64"
    assert wheels.current_platform("Darwin", "x86_64") == "macos-x64"
    assert wheels.current_platform("Darwin", "arm64") == "macos-arm64"
    assert wheels.current_platform("Linux", "AMD64") == "linux-x64"
    assert wheels.PIP_PLATFORMS == {
        "windows-x64": "win_amd64",
        "windows-arm64": "win_arm64",
        "macos-x64": "macosx_11_0_x86_64",
        "macos-arm64": "macosx_11_0_arm64",
        "linux-x64": "manylinux_2_28_x86_64",
    }
    with pytest.raises(BlexError):
        wheels.current_platform("Linux", "aarch64")
    with pytest.raises(BlexError):
        wheels.normalize_platforms(("linux-x64", "linux-x64"))


@pytest.mark.parametrize(
    "dependency",
    (
        "-r requirements.txt",
        "demo @ https://example.invalid/demo.whl",
        "git+https://example.invalid/demo.git",
        "../demo",
        "demo.whl",
    ),
)
def test_unsupported_dependencies_are_rejected(dependency: str) -> None:
    with pytest.raises(BlexError):
        wheels.validate_dependencies((dependency,))


def test_index_dependencies_allow_extras_specifiers_and_markers() -> None:
    wheels.validate_dependencies(
        (
            'demo[feature]>=1.2; python_version >= "3.11"',
            'other-package~=2.0; os_name == "posix/linux"',
        )
    )


def test_pip_download_command_and_inherited_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "pip"
    destination = tmp_path / "download"
    destination.mkdir()
    observed: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append((command, kwargs))
        (destination / "demo.whl").write_bytes(b"wheel")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(pip_adapter.subprocess, "run", fake_run)
    downloaded = pip_adapter.download_wheels(
        executable,
        dependencies=("demo[extra]>=1", 'other; python_version >= "3.11"'),
        python_version="3.11",
        abi="cp311",
        pip_platform="win_amd64",
        destination=destination,
    )

    assert observed == [
        (
            [
                str(executable),
                "download",
                "--only-binary=:all:",
                "--python-version",
                "3.11",
                "--implementation",
                "cp",
                "--abi",
                "cp311",
                "--abi",
                "abi3",
                "--abi",
                "none",
                "--platform",
                "win_amd64",
                "--dest",
                str(destination),
                "--disable-pip-version-check",
                "--no-input",
                "--no-cache-dir",
                "demo[extra]>=1",
                'other; python_version >= "3.11"',
            ],
            {"check": False},
        )
    ]
    assert downloaded[0].filename == "demo.whl"


def test_pip_download_failure_is_handled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pip_adapter.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 19),
    )
    with pytest.raises(BlexError, match="exit code 19"):
        pip_adapter.download_wheels(
            tmp_path / "pip",
            dependencies=("demo",),
            python_version="3.11",
            abi="cp311",
            pip_platform="manylinux_2_28_x86_64",
            destination=tmp_path,
        )


def test_pip_version_failure_includes_captured_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pip_adapter.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 2, stdout="version stdout\n", stderr="version stderr\n"
        ),
    )
    with pytest.raises(BlexError, match="version stdout") as error:
        pip_adapter.get_pip_version(tmp_path / "pip")
    assert "version stderr" in str(error.value)


def test_pip_executable_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "tools" / "pip.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"pip")
    assert pip_adapter.resolve_pip_executable("tools/pip.exe", tmp_path) == executable

    monkeypatch.setattr(pip_adapter.shutil, "which", lambda _value: str(executable))
    assert pip_adapter.resolve_pip_executable("fake-pip", tmp_path) == executable

    monkeypatch.setattr(pip_adapter.shutil, "which", lambda _value: None)
    with pytest.raises(BlexError, match="PATH"):
        pip_adapter.resolve_pip_executable("missing-pip", tmp_path)
    with pytest.raises(BlexError, match="does not exist"):
        pip_adapter.resolve_pip_executable("missing/pip", tmp_path)
    with pytest.raises(BlexError, match="must not be empty"):
        pip_adapter.resolve_pip_executable(" ", tmp_path)
    with pytest.raises(BlexError, match="NUL"):
        pip_adapter.resolve_pip_executable("bad\x00pip", tmp_path)
    with pytest.raises(BlexError, match="drive-relative"):
        pip_adapter.resolve_pip_executable("C:pip.exe", tmp_path)
    with pytest.raises(BlexError, match="drive-relative"):
        pip_adapter.resolve_pip_executable("C:", tmp_path)
    with pytest.raises(BlexError, match="regular file"):
        pip_adapter.resolve_pip_executable("tools/", tmp_path)


def test_pip_version_success_verbose_and_execution_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = tmp_path / "pip"
    monkeypatch.setattr(
        pip_adapter.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="pip 99\n", stderr=""
        ),
    )
    assert pip_adapter.get_pip_version(executable, verbose=True) == "pip 99"
    assert "pip version command" in capsys.readouterr().out

    def fail_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("cannot execute")

    monkeypatch.setattr(pip_adapter.subprocess, "run", fail_run)
    with pytest.raises(BlexError, match="failed to execute"):
        pip_adapter.get_pip_version(executable)


def test_pip_download_verbose_execution_error_and_unexpected_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("cannot execute")

    monkeypatch.setattr(pip_adapter.subprocess, "run", fail_run)
    with pytest.raises(BlexError, match="failed to execute"):
        pip_adapter.download_wheels(
            tmp_path / "pip",
            dependencies=("demo",),
            python_version="3.11",
            abi="cp311",
            pip_platform="win_amd64",
            destination=tmp_path,
            verbose=True,
        )
    assert "pip download command" in capsys.readouterr().out

    monkeypatch.setattr(
        pip_adapter.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )
    (tmp_path / "source.tar.gz").write_bytes(b"sdist")
    with pytest.raises(BlexError, match="unexpected"):
        pip_adapter.download_wheels(
            tmp_path / "pip",
            dependencies=("demo",),
            python_version="3.11",
            abi="cp311",
            pip_platform="win_amd64",
            destination=tmp_path,
        )


def test_fingerprint_uses_only_all_pip_environment_values(tmp_path: Path) -> None:
    executable = tmp_path / "pip"
    base = wheel_fingerprint(
        ("demo",),
        "3.11",
        "linux-x64",
        executable,
        "pip 99",
        {"PIP_INDEX_URL": "one", "PIP_TOKEN": "secret", "OTHER": "first"},
    )
    unrelated_change = wheel_fingerprint(
        ("demo",),
        "3.11",
        "linux-x64",
        executable,
        "pip 99",
        {"PIP_INDEX_URL": "one", "PIP_TOKEN": "secret", "OTHER": "second"},
    )
    pip_change = wheel_fingerprint(
        ("demo",),
        "3.11",
        "linux-x64",
        executable,
        "pip 99",
        {"PIP_INDEX_URL": "two", "PIP_TOKEN": "secret", "OTHER": "second"},
    )
    assert base == unrelated_change
    assert base != pip_change
    assert "secret" not in base


def test_file_lock_rejects_contention(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with FileLock(path, "test"):
        with pytest.raises(BlexError, match="already running"):
            with FileLock(path, "test"):
                pytest.fail("contended lock was acquired")


def _cache_fixture(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    entry = tmp_path / "entry"
    entry.mkdir()
    wheel = entry / "demo.whl"
    wheel.write_bytes(b"wheel")
    executable = tmp_path / "pip"
    kwargs: dict[str, Any] = {
        "fingerprint": "f" * 64,
        "python_version": "3.11",
        "platform_name": "windows-x64",
        "pip_executable": executable,
        "pip_version": "pip 99",
        "dependencies": ("demo",),
    }
    write_cache_metadata(
        entry,
        **kwargs,
        wheels=(CachedWheel(wheel, wheel.name, sha256_file(wheel)),),
    )
    return entry, kwargs


@pytest.mark.parametrize(
    "damage",
    (
        "missing-metadata",
        "bad-json",
        "bad-schema",
        "target-mismatch",
        "bad-record-shape",
        "bad-record-value",
        "bad-order",
        "missing-wheel",
        "unexpected-file",
    ),
)
def test_cache_damage_is_rejected(tmp_path: Path, damage: str) -> None:
    entry, kwargs = _cache_fixture(tmp_path)
    metadata_path = entry / "metadata.json"
    if damage == "missing-metadata":
        metadata_path.unlink()
    elif damage == "bad-json":
        metadata_path.write_text("not json", encoding="utf-8")
    else:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if damage == "bad-schema":
            metadata["extra"] = True
        elif damage == "target-mismatch":
            metadata["format"] = True
        elif damage == "bad-record-shape":
            metadata["wheels"] = [{"filename": "demo.whl"}]
        elif damage == "bad-record-value":
            metadata["wheels"][0]["filename"] = "../demo.whl"
        elif damage == "bad-order":
            metadata["wheels"].append(dict(metadata["wheels"][0]))
        elif damage == "missing-wheel":
            (entry / "demo.whl").unlink()
        elif damage == "unexpected-file":
            (entry / "extra.txt").write_text("extra", encoding="utf-8")
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(BlexError, match="cache|Wheel"):
        validate_cache_entry(entry, **kwargs)


def test_cache_entry_must_be_a_directory(tmp_path: Path) -> None:
    entry = tmp_path / "entry"
    entry.write_bytes(b"file")
    with pytest.raises(BlexError, match="directory"):
        validate_cache_entry(
            entry,
            fingerprint="f" * 64,
            python_version="3.11",
            platform_name="linux-x64",
            pip_executable=tmp_path / "pip",
            pip_version="pip 99",
            dependencies=(),
        )


def test_file_lock_exit_without_enter_is_a_noop(tmp_path: Path) -> None:
    FileLock(tmp_path / "lock", "test").__exit__(None, None, None)


def test_manual_wheels_work_without_a_python_target(tmp_path: Path) -> None:
    source = _source(tmp_path)
    manual = source / "vendor" / "demo.whl"
    manual.parent.mkdir()
    manual.write_bytes(b"manual")
    manifest = tomlkit.parse('wheels = ["./vendor/demo.whl"]\n')

    artifacts = wheels.resolve_wheels(
        project_root=tmp_path,
        source_dir=source,
        manifest=manifest,
        dependencies=("../unsupported",),
        python_version=None,
        platforms=(),
        automatic=False,
        pip_command="missing-pip",
        refresh=False,
    )

    assert [artifact.manifest_entry for artifact in artifacts] == ["./vendor/demo.whl"]
    assert artifacts[0].sha256 == hashlib.sha256(b"manual").hexdigest()
    assert not (tmp_path / ".blex").exists()


def test_wheel_resolution_mode_validation_and_empty_dependencies(tmp_path: Path) -> None:
    source = _source(tmp_path)
    base = {
        "project_root": tmp_path,
        "source_dir": source,
        "manifest": tomlkit.document(),
        "dependencies": (),
        "platforms": ("linux-x64",),
        "pip_command": "missing-pip",
    }
    assert (
        wheels.resolve_wheels(
            **base,
            python_version="3.11",
            automatic=True,
            refresh=False,
        )
        == ()
    )
    assert not (tmp_path / ".blex").exists()
    with pytest.raises(BlexError, match="Python target"):
        wheels.resolve_wheels(
            **base,
            python_version=None,
            automatic=True,
            refresh=False,
        )
    with pytest.raises(BlexError, match="refresh"):
        wheels.resolve_wheels(
            **base,
            python_version=None,
            automatic=False,
            refresh=True,
        )


def test_manual_wheel_manifest_shape_and_missing_file(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(BlexError, match="array"):
        wheels.resolve_wheels(
            project_root=tmp_path,
            source_dir=source,
            manifest={"wheels": "./demo.whl"},
            dependencies=(),
            python_version=None,
            platforms=(),
            automatic=False,
            pip_command="pip",
            refresh=False,
        )
    with pytest.raises(BlexError, match="inside source-dir"):
        wheels.resolve_wheels(
            project_root=tmp_path,
            source_dir=source,
            manifest={"wheels": ["./missing.whl"]},
            dependencies=(),
            python_version=None,
            platforms=(),
            automatic=False,
            pip_command="pip",
            refresh=False,
        )


def test_artifact_merge_conflict_and_deduplication(tmp_path: Path) -> None:
    first = wheels.WheelArtifact(tmp_path / "one", PurePosixPath("wheels/demo.whl"), "a")
    same = wheels.WheelArtifact(tmp_path / "two", PurePosixPath("wheels/demo.whl"), "a")
    conflict = wheels.WheelArtifact(tmp_path / "three", PurePosixPath("wheels/demo.whl"), "b")
    assert wheels._merge_artifacts((first, same)) == (first,)
    with pytest.raises(BlexError, match="conflict"):
        wheels._merge_artifacts((first, conflict))


@pytest.mark.parametrize(
    "entry",
    (
        "../demo.whl",
        "wheels\\demo.whl",
        "/demo.whl",
        "./x/./demo.whl",
        "wheels/C:demo.whl",
        "wheels/demo:stream.whl",
        "demo.zip",
    ),
)
def test_invalid_manual_wheel_paths_are_rejected(tmp_path: Path, entry: str) -> None:
    source = _source(tmp_path)
    manifest = tomlkit.document()
    manifest["wheels"] = [entry]
    with pytest.raises(BlexError):
        wheels.resolve_wheels(
            project_root=tmp_path,
            source_dir=source,
            manifest=manifest,
            dependencies=(),
            python_version=None,
            platforms=(),
            automatic=False,
            pip_command="pip",
            refresh=False,
        )


def test_nested_drive_relative_manual_wheel_cannot_reset_windows_join(tmp_path: Path) -> None:
    source = _source(tmp_path)
    (source / "demo.whl").write_bytes(b"must not be selected")

    with pytest.raises(BlexError, match="invalid manual Wheel path"):
        wheels.resolve_wheels(
            project_root=tmp_path,
            source_dir=source,
            manifest={"wheels": ["wheels/C:demo.whl"]},
            dependencies=(),
            python_version=None,
            platforms=(),
            automatic=False,
            pip_command="pip",
            refresh=False,
        )


def test_cache_hit_corruption_and_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    fake_pip = tmp_path / "fake-pip"
    fake_pip.write_bytes(b"pip")
    calls: list[str] = []
    monkeypatch.setenv("PIP_BLEX_TEST_TOKEN", "must-not-be-persisted")

    monkeypatch.setattr(wheels, "resolve_pip_executable", lambda _value, _root: fake_pip)
    monkeypatch.setattr(wheels, "get_pip_version", lambda _path, verbose=False: "pip 99")

    def fake_download(
        _executable: Path,
        *,
        dependencies: tuple[str, ...],
        python_version: str,
        abi: str,
        pip_platform: str,
        destination: Path,
        verbose: bool,
    ) -> tuple[CachedWheel, ...]:
        del dependencies, python_version, abi, verbose
        calls.append(pip_platform)
        path = destination / "demo-1-py3-none-any.whl"
        path.write_bytes(b"wheel")
        return (CachedWheel(path, path.name, sha256_file(path)),)

    monkeypatch.setattr(wheels, "download_wheels", fake_download)
    options = {
        "project_root": tmp_path,
        "source_dir": source,
        "manifest": tomlkit.document(),
        "dependencies": ("demo>=1",),
        "python_version": "3.11",
        "platforms": ("windows-x64",),
        "automatic": True,
        "pip_command": "fake-pip",
        "refresh": False,
    }

    first = wheels.resolve_wheels(**options)
    second = wheels.resolve_wheels(**options)
    assert calls == ["win_amd64"]
    assert first[0].sha256 == second[0].sha256

    fingerprint = wheel_fingerprint(("demo>=1",), "3.11", "windows-x64", fake_pip, "pip 99")
    entry = cache_entry_path(tmp_path, "3.11", "windows-x64", fingerprint)
    assert "must-not-be-persisted" not in (entry / "metadata.json").read_text(encoding="utf-8")
    (entry / first[0].source.name).write_bytes(b"corrupt")
    with pytest.raises(BlexError, match="hash mismatch"):
        wheels.resolve_wheels(**options)

    shutil.rmtree(entry)
    entry.write_bytes(b"not a directory")
    refreshed_options = {**options, "refresh": True}
    refreshed = wheels.resolve_wheels(**refreshed_options)
    assert calls == ["win_amd64", "win_amd64"]
    assert refreshed[0].source.is_file()


@pytest.mark.parametrize("transition", ("backup", "install"))
def test_cache_refresh_rolls_back_interrupt_after_real_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    fake_pip = tmp_path / "fake-pip"
    fake_pip.write_bytes(b"pip")
    dependencies = ("demo>=1",)
    python_version = "3.11"
    platform_name = "windows-x64"
    pip_version = "pip 99"
    content = b"old"

    def fake_download(
        _executable: Path,
        *,
        dependencies: tuple[str, ...],
        python_version: str,
        abi: str,
        pip_platform: str,
        destination: Path,
        verbose: bool,
    ) -> tuple[CachedWheel, ...]:
        del dependencies, python_version, abi, pip_platform, verbose
        path = destination / "demo-1-py3-none-any.whl"
        path.write_bytes(content)
        return (CachedWheel(path, path.name, sha256_file(path)),)

    monkeypatch.setattr(wheels, "download_wheels", fake_download)
    arguments = {
        "project_root": tmp_path,
        "dependencies": dependencies,
        "python_version": python_version,
        "platform_name": platform_name,
        "pip_executable": fake_pip,
        "pip_version": pip_version,
        "verbose": False,
    }
    wheels._resolve_cached_target(**arguments, refresh=False)
    fingerprint = wheel_fingerprint(
        dependencies,
        python_version,
        platform_name,
        fake_pip,
        pip_version,
    )
    entry = cache_entry_path(tmp_path, python_version, platform_name, fingerprint)
    original_rename = Path.rename
    interrupted = False

    def interrupt_after_rename(path: Path, target: Path) -> Path:
        nonlocal interrupted
        result = original_rename(path, target)
        is_backup = path == entry and ".backup-" in target.name
        is_install = target == entry and ".tmp-" in path.name
        if not interrupted and (
            (transition == "backup" and is_backup) or (transition == "install" and is_install)
        ):
            interrupted = True
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(Path, "rename", interrupt_after_rename)
    content = b"new"
    with pytest.raises(KeyboardInterrupt):
        wheels._resolve_cached_target(**arguments, refresh=True)

    assert interrupted is True
    wheel_path = entry / "demo-1-py3-none-any.whl"
    assert wheel_path.read_bytes() == b"old"
    assert (
        validate_cache_entry(
            entry,
            fingerprint=fingerprint,
            python_version=python_version,
            platform_name=platform_name,
            pip_executable=fake_pip,
            pip_version=pip_version,
            dependencies=dependencies,
        )[0].path
        == wheel_path
    )
    leftovers = [
        child.name
        for child in entry.parent.iterdir()
        if child.name.startswith(".tmp-") or child.name.startswith(".backup-")
    ]
    assert leftovers == []


def test_cache_temporary_creation_interrupt_cleans_created_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pip = tmp_path / "fake-pip"
    fake_pip.write_bytes(b"pip")
    dependencies = ("demo>=1",)
    fingerprint = wheel_fingerprint(
        dependencies,
        "3.11",
        "windows-x64",
        fake_pip,
        "pip 99",
    )
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
        if not interrupted and path.name.startswith(".tmp-"):
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(Path, "mkdir", interrupt_after_mkdir)
    with pytest.raises(KeyboardInterrupt):
        wheels._resolve_cached_target(
            project_root=tmp_path,
            dependencies=dependencies,
            python_version="3.11",
            platform_name="windows-x64",
            pip_executable=fake_pip,
            pip_version="pip 99",
            refresh=False,
            verbose=False,
        )

    assert interrupted is True
    cache_parent = tmp_path / ".blex" / "wheels" / "py3.11" / "windows-x64"
    assert not list(cache_parent.glob(".tmp-*"))
    assert not cache_entry_path(tmp_path, "3.11", "windows-x64", fingerprint).exists()


def test_cache_backup_cleanup_failure_keeps_installed_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pip = tmp_path / "fake-pip"
    fake_pip.write_bytes(b"pip")
    dependencies = ("demo>=1",)
    content = b"old"

    def fake_download(
        _executable: Path,
        *,
        dependencies: tuple[str, ...],
        python_version: str,
        abi: str,
        pip_platform: str,
        destination: Path,
        verbose: bool,
    ) -> tuple[CachedWheel, ...]:
        del dependencies, python_version, abi, pip_platform, verbose
        path = destination / "demo-1-py3-none-any.whl"
        path.write_bytes(content)
        return (CachedWheel(path, path.name, sha256_file(path)),)

    monkeypatch.setattr(wheels, "download_wheels", fake_download)
    arguments = {
        "project_root": tmp_path,
        "dependencies": dependencies,
        "python_version": "3.11",
        "platform_name": "windows-x64",
        "pip_executable": fake_pip,
        "pip_version": "pip 99",
        "verbose": False,
    }
    wheels._resolve_cached_target(**arguments, refresh=False)
    content = b"new"
    original_remove = wheels.remove_managed_tree

    def fail_partial_backup_cleanup(project_root: Path, target: Path) -> None:
        if ".backup-" in target.name:
            (target / "demo-1-py3-none-any.whl").unlink()
            raise OSError("simulated partial backup cleanup failure")
        original_remove(project_root, target)

    monkeypatch.setattr(wheels, "remove_managed_tree", fail_partial_backup_cleanup)
    with pytest.raises(BlexError, match="was installed.*backup"):
        wheels._resolve_cached_target(**arguments, refresh=True)

    fingerprint = wheel_fingerprint(
        dependencies,
        "3.11",
        "windows-x64",
        fake_pip,
        "pip 99",
    )
    entry = cache_entry_path(tmp_path, "3.11", "windows-x64", fingerprint)
    assert (entry / "demo-1-py3-none-any.whl").read_bytes() == b"new"
    backups = list(entry.parent.glob(".backup-*"))
    assert len(backups) == 1
    assert not (backups[0] / "demo-1-py3-none-any.whl").exists()
    assert not list(entry.parent.glob(".tmp-*"))


def test_refresh_rejects_reparse_cache_entry_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pip = tmp_path / "fake-pip"
    fake_pip.write_bytes(b"pip")
    dependencies = ("demo>=1",)
    fingerprint = wheel_fingerprint(dependencies, "3.11", "windows-x64", fake_pip, "pip 99")
    entry = cache_entry_path(tmp_path, "3.11", "windows-x64", fingerprint)
    entry.mkdir(parents=True)

    monkeypatch.setattr(wheels, "_is_link_or_reparse", lambda path: path == entry)

    def unexpected_download(*args: object, **kwargs: object) -> tuple[CachedWheel, ...]:
        pytest.fail("download must not start for a reparse cache entry")

    monkeypatch.setattr(wheels, "download_wheels", unexpected_download)
    with pytest.raises(BlexError, match="symlink or reparse point"):
        wheels._resolve_cached_target(
            project_root=tmp_path,
            dependencies=dependencies,
            python_version="3.11",
            platform_name="windows-x64",
            pip_executable=fake_pip,
            pip_version="pip 99",
            refresh=True,
            verbose=False,
        )

    assert entry.is_dir()


def test_platform_union_deduplicates_and_detects_content_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    fake_pip = tmp_path / "fake-pip"
    fake_pip.write_bytes(b"pip")
    monkeypatch.setattr(wheels, "resolve_pip_executable", lambda _value, _root: fake_pip)
    monkeypatch.setattr(wheels, "get_pip_version", lambda _path, verbose=False: "pip 99")
    different = False

    def fake_download(
        _executable: Path,
        *,
        dependencies: tuple[str, ...],
        python_version: str,
        abi: str,
        pip_platform: str,
        destination: Path,
        verbose: bool,
    ) -> tuple[CachedWheel, ...]:
        del dependencies, python_version, abi, verbose
        path = destination / "demo.whl"
        content = pip_platform.encode() if different else b"same"
        path.write_bytes(content)
        return (CachedWheel(path, path.name, sha256_file(path)),)

    monkeypatch.setattr(wheels, "download_wheels", fake_download)
    options = {
        "project_root": tmp_path,
        "source_dir": source,
        "manifest": tomlkit.document(),
        "dependencies": ("demo",),
        "python_version": "3.11",
        "platforms": ("windows-x64", "linux-x64"),
        "automatic": True,
        "pip_command": "fake-pip",
        "refresh": True,
    }
    assert len(wheels.resolve_wheels(**options)) == 1
    different = True
    with pytest.raises(BlexError, match="conflict"):
        wheels.resolve_wheels(**options)


def test_failed_download_removes_temporary_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    fake_pip = tmp_path / "pip"
    fake_pip.write_bytes(b"pip")
    monkeypatch.setattr(wheels, "resolve_pip_executable", lambda _value, _root: fake_pip)
    monkeypatch.setattr(wheels, "get_pip_version", lambda _path, verbose=False: "pip 99")

    def fail_download(*args: object, **kwargs: object) -> tuple[CachedWheel, ...]:
        raise BlexError("download failed")

    monkeypatch.setattr(wheels, "download_wheels", fail_download)
    with pytest.raises(BlexError, match="download failed"):
        wheels.resolve_wheels(
            project_root=tmp_path,
            source_dir=source,
            manifest=tomlkit.document(),
            dependencies=("demo",),
            python_version="3.11",
            platforms=("linux-x64",),
            automatic=True,
            pip_command="pip",
            refresh=False,
            verbose=True,
        )
    cache_parent = tmp_path / ".blex" / "wheels" / "py3.11" / "linux-x64"
    assert list(cache_parent.iterdir()) == []


def test_managed_directory_helpers(tmp_path: Path) -> None:
    wheels.ensure_blex_root(tmp_path)
    ignore = tmp_path / ".blex" / ".gitignore"
    ignore.write_text("custom", encoding="utf-8")
    wheels.ensure_blex_root(tmp_path)
    assert ignore.read_text(encoding="utf-8") == "custom\n*\n!.gitignore\n"

    managed_directory = tmp_path / ".blex" / "work" / "nested"
    wheels.ensure_managed_directory(tmp_path, managed_directory)
    managed_file = managed_directory / "temporary"
    managed_file.write_bytes(b"data")
    wheels.remove_managed_tree(tmp_path, managed_file)
    assert not managed_file.exists()
    wheels.remove_managed_tree(tmp_path, tmp_path / ".blex" / "work")
    assert not managed_directory.exists()

    with pytest.raises(BlexError, match="escapes"):
        wheels.ensure_managed_directory(tmp_path, tmp_path / "outside")
    with pytest.raises(BlexError, match="outside"):
        wheels.remove_managed_tree(tmp_path, tmp_path / "outside")
    with pytest.raises(BlexError, match="removable"):
        wheels.remove_managed_tree(tmp_path, tmp_path / ".blex" / "missing")


def test_managed_roots_reject_files(tmp_path: Path) -> None:
    (tmp_path / ".blex").write_bytes(b"file")
    with pytest.raises(BlexError, match="regular directory"):
        wheels.ensure_blex_root(tmp_path)


def test_managed_directory_rejects_file_component(tmp_path: Path) -> None:
    wheels.ensure_blex_root(tmp_path)
    (tmp_path / ".blex" / "blocked").write_bytes(b"file")
    with pytest.raises(BlexError, match="not a directory"):
        wheels.ensure_managed_directory(tmp_path, tmp_path / ".blex" / "blocked" / "child")
