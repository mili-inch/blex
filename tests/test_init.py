from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import tomlkit

from blex.commands import init as init_command
from blex.core.errors import BlexError, InitError


def _args(directory: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "directory": str(directory),
        "force": False,
        "name": None,
        "description": None,
        "python": None,
        "blender": None,
        "source_dir": None,
        "verbose": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_init_creates_complete_project(tmp_path: Path) -> None:
    root = tmp_path / "My Extension"
    assert init_command.run(_args(root)) == 0

    expected = {
        "pyproject.toml",
        ".python-version",
        "README.md",
        "LICENSE",
        ".gitignore",
        "src/my_extension/blender_manifest.toml",
        "src/my_extension/__init__.py",
        "src/my_extension/operators.py",
        "src/my_extension/panels.py",
    }
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    assert expected == actual
    assert not (root / "dist").exists()
    assert not (root / ".blex").exists()
    assert (root / ".python-version").read_bytes() == b"3.11\n"

    pyproject = tomlkit.parse((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "my-extension"
    assert pyproject["project"]["requires-python"] == ">=3.11"
    assert pyproject["tool"]["blex"]["source-dir"] == "src/my_extension"
    assert pyproject["tool"]["blex"]["stage"]["output-dir"] == "dist"
    assert "build-system" not in pyproject
    assert "dependency-groups" not in pyproject

    manifest_text = (root / "src/my_extension/blender_manifest.toml").read_text(encoding="utf-8")
    manifest = tomlkit.parse(manifest_text)
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["version"] == "1.0.0"
    assert manifest["id"] == "my_extension"
    assert manifest["license"] == ["SPDX:GPL-3.0-or-later"]
    assert "platforms" not in manifest
    assert "wheels" not in manifest
    assert "# [permissions]" in manifest_text
    assert "# [build]" in manifest_text

    operator = (root / "src/my_extension/operators.py").read_text(encoding="utf-8")
    assert "class MY_EXTENSION_OT_hello" in operator
    assert 'bl_idname = "my_extension.hello"' in operator
    assert "Hello from My Extension!" in operator
    assert (root / "README.md").read_text(encoding="utf-8") == (
        "# My Extension\n\nAdd your description here\n\n"
        "## Development\n\n```console\nblex dev\n```\n"
    )
    assert b"\r\n" not in (root / "pyproject.toml").read_bytes()
    assert "GNU GENERAL PUBLIC LICENSE" in (root / "LICENSE").read_text(encoding="utf-8")


def test_existing_marker_requires_force_without_modification(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    marker = root / "pyproject.toml"
    marker.write_bytes(b"not valid TOML")
    with pytest.raises(InitError, match="--force"):
        init_command.run(_args(root))
    assert marker.read_bytes() == b"not valid TOML"
    assert list(root.iterdir()) == [marker]


def test_force_merges_missing_fields_and_preserves_existing_content(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "custom_source"
    source.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """# project comment
[project]
name = "kept-name"
description = "Kept description"
requires-python = ">=3.8"

[dependency-groups]
dev = ["pytest==9"]

[tool.blex]
source-dir = "custom_source"
custom-preserved = true
""".replace("custom-preserved = true\n", ""),
        encoding="utf-8",
    )
    (source / "blender_manifest.toml").write_text(
        """# manifest comment
id = "kept_addon"
name = "Kept Addon"
custom = "preserved"
""",
        encoding="utf-8",
    )
    (source / "operators.py").write_text("# user operator\n", encoding="utf-8")
    (root / ".gitignore").write_text("custom/\n.venv/\n", encoding="utf-8")
    (root / "README.md").write_text("user README\n", encoding="utf-8")

    assert init_command.run(_args(root, force=True, python="3.10")) == 0
    pyproject_text = (root / "pyproject.toml").read_text(encoding="utf-8")
    pyproject = tomlkit.parse(pyproject_text)
    assert "# project comment" in pyproject_text
    assert pyproject["project"]["name"] == "kept-name"
    assert pyproject["project"]["requires-python"] == ">=3.8"
    assert list(pyproject["dependency-groups"]["dev"]) == ["pytest==9"]
    assert pyproject["tool"]["blex"]["source-dir"] == "custom_source"
    assert pyproject["tool"]["blex"]["blender"] == "blender"
    assert pyproject["project"]["license"] == "GPL-3.0-or-later"

    manifest_text = (source / "blender_manifest.toml").read_text(encoding="utf-8")
    manifest = tomlkit.parse(manifest_text)
    assert "# manifest comment" in manifest_text
    assert manifest["id"] == "kept_addon"
    assert manifest["name"] == "Kept Addon"
    assert manifest["custom"] == "preserved"
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["type"] == "add-on"
    assert "# [permissions]" not in manifest_text
    assert (source / "operators.py").read_text(encoding="utf-8") == "# user operator\n"
    assert (root / "README.md").read_text(encoding="utf-8") == "user README\n"
    assert (root / ".python-version").read_text(encoding="utf-8") == "3.10\n"
    gitignore = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert gitignore.count(".venv/") == 1
    assert "custom/" in gitignore
    assert "!.blex/.gitignore" in gitignore


def test_force_rejects_explicit_conflicts(tmp_path: Path) -> None:
    root = tmp_path / "project"
    init_command.run(_args(root, name="First Name"))
    with pytest.raises(InitError, match="--name conflicts"):
        init_command.run(_args(root, force=True, name="Second Name"))
    with pytest.raises(InitError, match="--description conflicts"):
        init_command.run(_args(root, force=True, description="different"))
    with pytest.raises(InitError, match="--source-dir conflicts"):
        init_command.run(_args(root, force=True, source_dir="src/elsewhere"))


def test_python_option_wins_without_touching_blender(tmp_path: Path) -> None:
    root = tmp_path / "project"
    assert (
        init_command.run(_args(root, python="2.7", blender="definitely-not-a-real-blender-command"))
        == 0
    )
    assert (root / ".python-version").read_text(encoding="utf-8") == "2.7\n"
    assert not (root / ".blex").exists()
    pyproject = tomlkit.parse((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["requires-python"] == ">=3.11"
    assert pyproject["tool"]["blex"]["blender"] == "blender"


def test_description_is_escaped_without_treating_user_braces_as_template_tokens(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    description = 'Use "{{value}}" from C:\\data'
    init_command.run(_args(root, description=description))
    pyproject = tomlkit.parse((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["description"] == description
    assert description in (root / "README.md").read_text(encoding="utf-8")


def test_blender_python_detection_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Blender log\nBLEX_PYTHON_VERSION=3.14\n",
            stderr="",
        )

    monkeypatch.setattr(init_command.subprocess, "run", fake_run)
    assert init_command.run(_args(root, blender=sys.executable, verbose=True)) == 0
    assert len(calls) == 1
    assert calls[0][1:4] == ["--background", "--factory-startup", "--python-expr"]
    assert (root / ".python-version").read_text(encoding="utf-8") == "3.14\n"
    cache_path = root / ".blex/cache/blender-python.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["format"] == 1
    assert len(cache["entries"]) == 1
    assert (root / ".blex/.gitignore").read_text(encoding="utf-8") == "*\n!.gitignore\n"

    def fail_run(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("cache hit must not execute Blender")

    monkeypatch.setattr(init_command.subprocess, "run", fail_run)
    assert init_command.run(_args(root, force=True, blender=sys.executable, verbose=True)) == 0


def test_corrupt_blender_cache_is_an_unexpected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0, stdout="BLEX_PYTHON_VERSION=3.11\n", stderr=""
        )

    monkeypatch.setattr(init_command.subprocess, "run", fake_run)
    init_command.run(_args(root, blender=sys.executable))
    cache_path = root / ".blex/cache/blender-python.json"
    cache_path.write_text('{"format": 1, "entries": {}, "extra": true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="top-level keys"):
        init_command.run(_args(root, force=True, blender=sys.executable))


def test_invalid_existing_python_version_does_not_create_files(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    version = root / ".python-version"
    version.write_text("3.11\n3.12\n", encoding="utf-8")
    with pytest.raises(BlexError, match="invalid Python version"):
        init_command.run(_args(root))
    assert list(root.iterdir()) == [version]


def test_existing_python_version_rejects_extra_blank_line(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    version = root / ".python-version"
    version.write_text("3.11\n\n", encoding="utf-8")

    with pytest.raises(BlexError, match="exactly one"):
        init_command.run(_args(root))

    assert list(root.iterdir()) == [version]


def test_transaction_rolls_back_created_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    original_atomic_write = init_command._atomic_write
    calls = 0

    def fail_once(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected failure")
        original_atomic_write(path, content)

    monkeypatch.setattr(init_command, "_atomic_write", fail_once)
    with pytest.raises(InitError, match="failed to write"):
        init_command.run(_args(root))
    assert not root.exists()


def test_force_adds_missing_project_without_modifying_dependency_groups(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        """
[dependency-groups]
dev = ["pytest"]

[tool.unrelated]
value = true
""",
        encoding="utf-8",
    )
    assert init_command.run(_args(root, force=True, name="Demo Addon")) == 0
    document = tomlkit.parse((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert document["project"]["name"] == "demo-addon"
    assert list(document["dependency-groups"]["dev"]) == ["pytest"]
    assert document["tool"]["unrelated"]["value"] is True


def test_force_preserves_dependency_groups_without_validation(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n[dependency-groups]\ndev = 1\n',
        encoding="utf-8",
    )

    assert init_command.run(_args(root, force=True)) == 0

    document = tomlkit.parse((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert document["dependency-groups"]["dev"] == 1


def test_force_does_not_renormalize_complete_existing_names(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "extension"
    source.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """
[project]
name = "existing.project"

[tool.blex]
source-dir = "extension"
""",
        encoding="utf-8",
    )
    (source / "blender_manifest.toml").write_text(
        'id = "existing_addon"\nname = "Existing: Add-on"\n', encoding="utf-8"
    )
    assert init_command.run(_args(root, force=True)) == 0
    project = tomlkit.parse((root / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = tomlkit.parse((source / "blender_manifest.toml").read_text(encoding="utf-8"))
    assert project["project"]["name"] == "existing.project"
    assert manifest["id"] == "existing_addon"
    assert manifest["name"] == "Existing: Add-on"


@pytest.mark.parametrize(
    "project_text",
    [
        "project = 1\n",
        "[project]\nname = 1\n",
        '[project]\nname = "demo"\ndescription = 1\n',
        '[project]\nname = "demo"\nrequires-python = 3.11\n',
        '[project]\nname = "demo"\ndependencies = "bad"\n',
        '[project]\nname = "demo"\ndependencies = [1]\n',
    ],
)
def test_force_rejects_invalid_existing_project_types(tmp_path: Path, project_text: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(project_text, encoding="utf-8")
    with pytest.raises(InitError):
        init_command.run(_args(root, force=True))


def test_force_rejects_invalid_toml(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("broken = [toml", encoding="utf-8")
    with pytest.raises(InitError, match="cannot parse"):
        init_command.run(_args(root, force=True))


def test_manifest_marker_without_pyproject_requires_force(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    source = root / "src/demo"
    source.mkdir(parents=True)
    manifest = source / "blender_manifest.toml"
    manifest.write_text('id = "demo"\nname = "Demo"\n', encoding="utf-8")
    with pytest.raises(InitError, match="--force"):
        init_command.run(_args(root))
    assert not (root / "pyproject.toml").exists()


def test_force_merges_internal_symlinked_manifest_without_replacing_link(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "demo"
    source.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n\n[tool.blex]\nsource-dir = "src/demo"\n',
        encoding="utf-8",
    )
    target = source / "manifest-source.toml"
    target.write_text(
        '# linked manifest\nid = "demo"\nname = "Demo"\n',
        encoding="utf-8",
    )
    manifest = source / "blender_manifest.toml"
    try:
        manifest.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    assert init_command.run(_args(root, force=True)) == 0

    assert manifest.is_symlink()
    merged = tomlkit.parse(target.read_text(encoding="utf-8"))
    assert merged["schema_version"] == "1.0.0"
    assert merged["id"] == "demo"
    assert "# linked manifest" in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("manifest_points_to_sample", [True, False])
def test_force_rejects_manifest_and_sample_path_alias(
    tmp_path: Path,
    manifest_points_to_sample: bool,
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "demo"
    source.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n\n[tool.blex]\nsource-dir = "src/demo"\n',
        encoding="utf-8",
    )
    manifest = source / "blender_manifest.toml"
    operators = source / "operators.py"
    content = b'id = "demo"\nname = "Demo"\n'
    physical = operators if manifest_points_to_sample else manifest
    physical.write_bytes(content)
    link = manifest if manifest_points_to_sample else operators
    try:
        link.symlink_to(physical)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(InitError, match="must not alias"):
        init_command.run(_args(root, force=True))

    assert physical.read_bytes() == content
    assert link.is_symlink()


@pytest.mark.parametrize(
    ("manifest_id", "manifest_name", "match"),
    [
        ("other", "Demo Addon", "manifest id"),
        ("demo_addon", "Other Name", "manifest name"),
    ],
)
def test_name_conflict_is_checked_against_manifest(
    tmp_path: Path, manifest_id: str, manifest_name: str, match: str
) -> None:
    root = tmp_path / "project"
    source = root / "src/demo_addon"
    source.mkdir(parents=True)
    (source / "blender_manifest.toml").write_text(
        f'id = "{manifest_id}"\nname = "{manifest_name}"\n', encoding="utf-8"
    )
    with pytest.raises(InitError, match=match):
        init_command.run(_args(root, force=True, name="Demo Addon"))


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        ("timeout", "timed out"),
        ("nonzero", "exited with code 7"),
        ("missing", "exactly one result"),
        ("multiple", "exactly one result"),
        ("invalid", "invalid version"),
        ("oserror", "failed to execute"),
    ],
)
def test_blender_detection_failures_are_handled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    message: str,
) -> None:
    root = tmp_path / "project"

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(
                command, 30, output=b"partial out", stderr=b"partial err"
            )
        if outcome == "oserror":
            raise OSError("injected process failure")
        if outcome == "nonzero":
            return subprocess.CompletedProcess(command, 7, stdout="out", stderr="err")
        if outcome == "missing":
            return subprocess.CompletedProcess(command, 0, stdout="Blender log\n", stderr="")
        if outcome == "multiple":
            output = "BLEX_PYTHON_VERSION=3.11\nBLEX_PYTHON_VERSION=3.12\n"
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
        return subprocess.CompletedProcess(
            command, 0, stdout="BLEX_PYTHON_VERSION=03.11\n", stderr=""
        )

    monkeypatch.setattr(init_command.subprocess, "run", fake_run)
    with pytest.raises(InitError, match=message):
        init_command.run(_args(root, blender=sys.executable))
    assert not root.exists()


@pytest.mark.parametrize(
    "cache",
    [
        [],
        {"format": 2, "entries": {}},
        {"format": 1, "entries": []},
        {"format": 1, "entries": {"bad": {"python": "3.11", "executable": "x"}}},
        {"format": 1, "entries": {"0" * 64: []}},
        {"format": 1, "entries": {"0" * 64: {"python": "3.11", "extra": "x"}}},
        {"format": 1, "entries": {"0" * 64: {"python": 311, "executable": "x"}}},
        {"format": 1, "entries": {"0" * 64: {"python": "03.11", "executable": "x"}}},
        {"format": 1, "entries": {"0" * 64: {"python": "3.011", "executable": "x"}}},
    ],
)
def test_blender_cache_schema_corruption_raises_raw_error(cache: object) -> None:
    with pytest.raises((TypeError, ValueError, KeyError)):
        init_command._validate_blender_cache(cache)


def test_existing_file_transaction_is_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    init_command.run(_args(root))
    pyproject_path = root / "pyproject.toml"
    pyproject_path.write_text(
        pyproject_path.read_text(encoding="utf-8").replace('license = "GPL-3.0-or-later"\n', ""),
        encoding="utf-8",
    )
    pyproject_before = pyproject_path.read_bytes()
    python_before = (root / ".python-version").read_bytes()
    original_atomic_write = init_command._atomic_write
    calls = 0

    def fail_once(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected update failure")
        original_atomic_write(path, content)

    monkeypatch.setattr(init_command, "_atomic_write", fail_once)
    with pytest.raises(InitError, match="failed to write"):
        init_command.run(_args(root, force=True, python="3.14"))
    assert pyproject_path.read_bytes() == pyproject_before
    assert (root / ".python-version").read_bytes() == python_before


@pytest.mark.parametrize("existing", [False, True])
def test_atomic_write_interrupt_after_replace_is_rolled_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "metadata.toml"
    original = b"original\n" if existing else None
    if original is not None:
        target.write_bytes(original)
    real_atomic_write = init_command._atomic_write
    interrupted = False

    def interrupt_once(path: Path, content: bytes) -> None:
        nonlocal interrupted
        real_atomic_write(path, content)
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(init_command, "_atomic_write", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        init_command._apply_transaction([init_command._Write(target, b"replacement\n")])

    if original is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == original


def test_atomic_write_cleans_temp_when_interrupted_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "metadata.toml"
    real_open = Path.open
    interrupted = False

    def interrupt_once(path: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal interrupted
        stream = real_open(path, *args, **kwargs)
        if path.name.startswith(".blex-init-") and not interrupted:
            interrupted = True
            stream.close()
            raise KeyboardInterrupt
        return stream

    monkeypatch.setattr(Path, "open", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        init_command._atomic_write(target, b"content\n")

    assert interrupted is True
    assert not target.exists()
    assert list(tmp_path.glob(".blex-init-*")) == []


def test_directory_creation_interrupt_after_mkdir_is_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "new" / "nested" / "file.txt"
    interrupted_path = target.parent.parent
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
        if path == interrupted_path and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(Path, "mkdir", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        init_command._apply_transaction([init_command._Write(target, b"content\n")])

    assert not interrupted_path.exists()


@pytest.mark.parametrize(
    ("relative_path", "force"),
    [
        ("pyproject.toml", True),
        ("README.md", False),
        ("LICENSE", False),
        (".python-version", False),
        (".gitignore", False),
    ],
)
def test_init_rejects_broken_symlink_at_generated_file_target(
    tmp_path: Path,
    relative_path: str,
    force: bool,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    link = root / relative_path
    try:
        link.symlink_to(root / "missing-target")
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(InitError):
        init_command.run(_args(root, force=force))

    assert link.is_symlink()


def test_init_rejects_broken_blender_cache_link_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    cache = root / ".blex" / "cache" / "blender-python.json"
    cache.parent.mkdir(parents=True)
    try:
        cache.symlink_to(root / "missing-cache")
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    def unexpected_run(*args: object, **kwargs: object) -> None:
        pytest.fail("Blender must not run when its managed cache path is unsafe")

    monkeypatch.setattr(init_command.subprocess, "run", unexpected_run)
    with pytest.raises(InitError, match="cache must be a regular file"):
        init_command.run(_args(root, blender=sys.executable))

    assert cache.is_symlink()


@pytest.mark.parametrize("filename", ["README.md", "LICENSE"])
def test_init_rejects_generated_file_directory_collision(tmp_path: Path, filename: str) -> None:
    root = tmp_path / "project"
    (root / filename).mkdir(parents=True)
    with pytest.raises(InitError, match="regular file"):
        init_command.run(_args(root))


def test_init_rejects_managed_directory_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".blex").write_text("not a directory", encoding="utf-8")
    with pytest.raises(InitError, match="managed directory"):
        init_command.run(_args(root, blender=sys.executable))


def test_template_and_gitignore_validation_helpers(tmp_path: Path) -> None:
    with pytest.raises(InitError, match="missing placeholder"):
        init_command._render("text", {"UNKNOWN": "value"})
    assert init_command._render("{{user-value}}", {}) == b"{{user-value}}\n"
    assert init_command._render("text", {}) == b"text\n"
    path = tmp_path / ".gitignore"
    with pytest.raises(InitError, match="UTF-8"):
        init_command._merge_gitignore(b"\xff", ("required",), path)
    assert init_command._process_output(None) == ""
    assert init_command._process_output(b"\xff") == "�"
    assert init_command._process_output("text") == "text"
