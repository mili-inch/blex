from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import pytest
import tomlkit

from blex.core import project as project_module
from blex.core.config import BlexConfig, load_config, select_value
from blex.core.errors import ConfigurationError, ProjectError
from blex.core.project import (
    derive_names,
    load_pyproject,
    normalize_python_version,
    normalize_python_version_file,
    normalize_python_versions,
    project_relative_path,
    read_python_version,
    resolve_executable,
    resolve_project_path,
    resolve_project_root,
    resolve_source_dir,
    validate_description,
    validate_source_tree,
)


def test_load_empty_config() -> None:
    assert load_config(tomlkit.parse('[project]\nname = "demo"\n')) == BlexConfig()


def test_load_complete_config() -> None:
    document = tomlkit.parse(
        """
[tool.blex]
source-dir = "extension"
blender = "blender-bin"
pip = "pip-bin"

[tool.blex.dev]
link-mode = "copy"
reload-panel = false
wheels = false
debugpy = true
debugpy-host = "localhost"
debugpy-port = 1234
blend-file = "scene.blend"
user-resources = "seed"
python = "3.11"

[tool.blex.build]
output-dir = "stage"
wheels = false
python = ["3.14", "3.11"]
platforms = ["windows-arm64", "linux-x64"]
"""
    )
    config = load_config(document)
    assert config.source_dir == "extension"
    assert config.blender == "blender-bin"
    assert config.pip == "pip-bin"
    assert config.dev.link_mode == "copy"
    assert config.dev.reload_panel is False
    assert config.dev.wheels is False
    assert config.dev.debugpy is True
    assert config.dev.debugpy_host == "localhost"
    assert config.dev.debugpy_port == 1234
    assert config.dev.blend_file == "scene.blend"
    assert config.dev.user_resources == "seed"
    assert config.dev.python == "3.11"
    assert config.build.output_dir == "stage"
    assert config.build.wheels is False
    assert config.build.python == ("3.14", "3.11")
    assert config.build.platforms == ("windows-arm64", "linux-x64")


@pytest.mark.parametrize(
    "body",
    [
        "[tool.blex]\nunknown = true\n",
        "[tool.blex.dev]\nunknown = true\n",
        "[tool.blex.build]\nunknown = true\n",
        '[tool.blex]\nsource-dir = ""\n',
        "[tool.blex.dev]\nreload-panel = 1\n",
        '[tool.blex.dev]\nlink-mode = "hardlink"\n',
        "[tool.blex.dev]\ndebugpy-port = 0\n",
        "[tool.blex.build]\npython = []\n",
        '[tool.blex.build]\npython = ["3.11", "3.11"]\n',
        '[tool.blex.build]\nplatforms = ["linux-arm64"]\n',
        '[tool.blex.build]\nplatforms = ["linux-x64", "linux-x64"]\n',
    ],
)
def test_invalid_config_is_rejected(body: str) -> None:
    with pytest.raises((ConfigurationError, ProjectError)):
        load_config(tomlkit.parse(body))


def test_select_value_uses_cli_then_config_then_default() -> None:
    assert select_value("cli", "config", "default") == "cli"
    assert select_value(None, "config", "default") == "config"
    assert select_value(None, None, "default") == "default"
    assert select_value(False, True, True) is False


@pytest.mark.parametrize(
    ("base", "project_name", "manifest_id", "display_name"),
    [
        ("My Extension", "my-extension", "my_extension", "My Extension"),
        ("demo__ADD-on", "demo-add-on", "demo_add_on", "Demo Add On"),
        ("A  B", "a-b", "a_b", "A B"),
    ],
)
def test_name_generation(base: str, project_name: str, manifest_id: str, display_name: str) -> None:
    names = derive_names(base)
    assert names.project_name == project_name
    assert names.manifest_id == manifest_id
    assert names.display_name == display_name
    assert names.class_prefix == manifest_id.upper()


@pytest.mark.parametrize("base", ["", "123 demo", "class", "bad.name", "日本語"])
def test_invalid_name_generation(base: str) -> None:
    with pytest.raises(ProjectError):
        derive_names(base)


@pytest.mark.parametrize(("raw", "expected"), [("3.11", "3.11"), (" 1.0 ", "1.0")])
def test_python_version(raw: str, expected: str) -> None:
    assert normalize_python_version(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["3", "3.11.2", "03.11", "3.01", "0.1", "3.11\n3.12", "\n3.11\n"],
)
def test_invalid_python_version(raw: str) -> None:
    with pytest.raises(ProjectError):
        normalize_python_version(raw)


def test_python_version_file_allows_one_line_ending_only() -> None:
    assert normalize_python_version_file(" 3.11 \n") == "3.11"
    for value in ("3.11\n\n", "\n3.11\n", "3.11\n3.12\n"):
        with pytest.raises(ProjectError, match="exactly one"):
            normalize_python_version_file(value)


def test_duplicate_python_version() -> None:
    with pytest.raises(ProjectError, match="duplicate"):
        normalize_python_versions(["3.11", " 3.11 "])


def test_description_rejects_newline_before_stripping() -> None:
    with pytest.raises(ProjectError):
        validate_description("description\n")
    assert validate_description(" description ") == "description"


def test_source_dir_must_be_safe_project_descendant(tmp_path: Path) -> None:
    root = resolve_project_root(tmp_path, must_exist=True)
    source = resolve_source_dir(root, "src/addon")
    assert source == root / "src" / "addon"
    assert project_relative_path(source, root) == "src/addon"
    for unsafe in (".", "..", ".blex/addon", ".git/addon"):
        with pytest.raises(ProjectError):
            resolve_source_dir(root, unsafe)


def test_non_string_name_version_and_description_are_rejected() -> None:
    invalid = cast(str, object())
    with pytest.raises(ProjectError):
        derive_names(invalid)
    with pytest.raises(ProjectError):
        normalize_python_version(invalid)
    with pytest.raises(ProjectError):
        validate_description(invalid)
    with pytest.raises(ProjectError):
        validate_description(" \t ")
    with pytest.raises(ProjectError):
        validate_description("bad\x00description")


def test_project_root_and_path_validation(tmp_path: Path) -> None:
    file_path = tmp_path / "file"
    file_path.write_text("file", encoding="utf-8")
    with pytest.raises(ProjectError, match="not a directory"):
        resolve_project_root(file_path, must_exist=False)
    with pytest.raises(ProjectError, match="does not exist"):
        resolve_project_root(tmp_path / "missing", must_exist=True)
    with pytest.raises(ProjectError, match="NUL"):
        resolve_project_root("bad\x00root", must_exist=False)
    with pytest.raises(ProjectError, match="non-empty"):
        resolve_project_path(tmp_path, "", "test path")
    with pytest.raises(ProjectError, match="NUL"):
        resolve_project_path(tmp_path, "bad\x00path", "test path")
    assert resolve_project_path(tmp_path, "child", "test path") == tmp_path / "child"
    with pytest.raises(ProjectError, match="not a directory"):
        resolve_source_dir(tmp_path, "file")
    with pytest.raises(ProjectError, match="outside"):
        project_relative_path(tmp_path.parent, tmp_path)
    with pytest.raises(ProjectError, match="strict descendant"):
        project_relative_path(tmp_path, tmp_path)


def test_source_tree_with_regular_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("content", encoding="utf-8")
    validate_source_tree(source)
    with pytest.raises(ProjectError, match="not a directory"):
        validate_source_tree(nested / "file.txt")


def _make_symlink_or_skip(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")


def test_source_tree_symlink_validation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = source / "target.txt"
    target.write_text("content", encoding="utf-8")
    link = source / "internal.txt"
    _make_symlink_or_skip(link, target, directory=False)
    validate_source_tree(source)

    link.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    _make_symlink_or_skip(link, outside, directory=False)
    with pytest.raises(ProjectError, match="outside"):
        validate_source_tree(source)

    link.unlink()
    missing = source / "missing.txt"
    _make_symlink_or_skip(link, missing, directory=False)
    with pytest.raises(ProjectError, match="broken"):
        validate_source_tree(source)


def test_source_tree_rejects_directory_symlink_cycle(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    _make_symlink_or_skip(nested / "loop", source, directory=True)
    with pytest.raises(ProjectError, match="cyclic"):
        validate_source_tree(source)


def test_project_root_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _make_symlink_or_skip(link, real, directory=True)
    with pytest.raises(ProjectError, match="symlink"):
        resolve_project_root(link, must_exist=True)


def test_resolve_executable_path_and_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = Path(sys.executable)
    assert resolve_executable(str(executable), tmp_path, "tool") == executable.resolve()
    monkeypatch.setattr(project_module.shutil, "which", lambda _value: str(executable))
    assert resolve_executable("fake-command", tmp_path, "tool") == executable.resolve()
    monkeypatch.setattr(project_module.shutil, "which", lambda _value: None)
    with pytest.raises(ProjectError, match="PATH"):
        resolve_executable("fake-command", tmp_path, "tool")
    with pytest.raises(ProjectError, match="non-empty"):
        resolve_executable("", tmp_path, "tool")
    with pytest.raises(ProjectError, match="drive-relative"):
        resolve_executable("C:program.exe", tmp_path, "tool")
    with pytest.raises(ProjectError, match="NUL"):
        resolve_executable("bad\x00program", tmp_path, "tool")
    with pytest.raises(ProjectError, match="cannot resolve"):
        resolve_executable("missing/program", tmp_path, "tool")
    app = tmp_path / "Blender.app"
    app.mkdir()
    with pytest.raises(ProjectError, match="regular executable"):
        resolve_executable(str(app), tmp_path, "tool")


def test_read_python_version(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="does not exist"):
        read_python_version(tmp_path)
    version = tmp_path / ".python-version"
    version.write_text(" 3.14 \n", encoding="utf-8")
    assert read_python_version(tmp_path) == "3.14"
    version.write_text("03.14\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="invalid"):
        read_python_version(tmp_path)
    version.write_text("3.14\n\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="exactly one"):
        read_python_version(tmp_path)
    version.write_bytes(b"\xff")
    with pytest.raises(ProjectError, match="cannot read"):
        read_python_version(tmp_path)


def test_load_pyproject(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    with pytest.raises(ProjectError, match="does not exist"):
        load_pyproject(tmp_path)
    path.write_text(
        """
[project]
name = "demo"
dependencies = ["example>=1", "other; python_version > '3.10'"]

[tool.blex]
source-dir = "src/demo"
""",
        encoding="utf-8",
    )
    project = load_pyproject(tmp_path)
    assert project.root == tmp_path
    assert project.path == path
    assert project.name == "demo"
    assert project.dependencies == ("example>=1", "other; python_version > '3.10'")
    assert project.config.source_dir == "src/demo"


@pytest.mark.parametrize(
    "text",
    [
        "not = [toml",
        "value = 1\n",
        "project = 1\n",
        '[project]\nname = ""\n',
        '[project]\nname = "demo"\ndependencies = "bad"\n',
        '[project]\nname = "demo"\ndependencies = [1]\n',
    ],
)
def test_load_pyproject_rejects_invalid_metadata(tmp_path: Path, text: str) -> None:
    (tmp_path / "pyproject.toml").write_text(text, encoding="utf-8")
    with pytest.raises(ProjectError):
        load_pyproject(tmp_path)


def test_path_resolution_os_errors_are_handled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_resolve(_path: Path, *, strict: bool = False) -> Path:
        del strict
        raise OSError("injected resolution failure")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(ProjectError, match="cannot resolve project root"):
        resolve_project_root(tmp_path, must_exist=False)
    with pytest.raises(ProjectError, match="cannot resolve test path"):
        resolve_project_path(tmp_path, "child", "test path")


def test_source_tree_inspection_error_is_handled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()

    def fail_scandir(_path: Path) -> list[object]:
        raise OSError("injected scan failure")

    monkeypatch.setattr(project_module.os, "scandir", fail_scandir)
    with pytest.raises(ProjectError, match="cannot inspect"):
        validate_source_tree(source)
