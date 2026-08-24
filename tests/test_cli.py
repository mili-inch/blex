from __future__ import annotations

import argparse
from pathlib import Path
import runpy

import pytest

import blex.main as main_module
from blex.cli import create_parser, parse_args
from blex.core.errors import BlexError


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main_module.main(["--version"]) == 0
    assert capsys.readouterr().out == "blex 0.1.0\n"


def test_subcommand_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    assert main_module.main([]) == 2
    assert "required" in capsys.readouterr().err


def test_embedded_nul_paths_are_reported_as_command_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main_module.main(["init", "bad\x00root"]) == 1
    assert "NUL" in capsys.readouterr().err

    root = tmp_path / "project"
    assert main_module.main(["init", str(root), "--source-dir", "bad\x00source"]) == 1
    assert "NUL" in capsys.readouterr().err
    assert not root.exists()


def test_top_level_and_command_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main_module.main(["--help"]) == 0
    assert "{init,dev,stage}" in capsys.readouterr().out
    assert main_module.main(["stage", "--help"]) == 0
    assert "--platform" in capsys.readouterr().out


def test_short_help_option_is_not_provided(capsys: pytest.CaptureFixture[str]) -> None:
    assert main_module.main(["-h"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err


def test_verbose_is_only_accepted_before_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main_module.main(["init", "--verbose"]) == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_dev_boolean_options_are_tristate() -> None:
    parser = create_parser()
    defaults = parser.parse_args(["dev"])
    assert defaults.reload_panel is None
    assert defaults.debugpy is None
    assert defaults.wheels is None

    disabled = parser.parse_args(["dev", "--no-reload-panel", "--no-debugpy", "--no-wheels"])
    assert disabled.reload_panel is False
    assert disabled.debugpy is False
    assert disabled.wheels is False


def test_dev_blender_arguments_require_and_strip_delimiter() -> None:
    args = parse_args(["dev", "--", "--background", "--factory-startup"])
    assert args.directory == "."
    assert args.blender_args == ["--background", "--factory-startup"]

    with pytest.raises(SystemExit) as error:
        parse_args(["dev", "--background"])
    assert error.value.code == 2

    with pytest.raises(SystemExit) as wrong_command:
        parse_args(["stage", "--"])
    assert wrong_command.value.code == 2


@pytest.mark.parametrize("command", ["init", "dev"])
def test_single_target_python_may_only_be_specified_once(command: str) -> None:
    assert parse_args([command, "--python", "3.11"]).python == "3.11"
    with pytest.raises(SystemExit) as error:
        parse_args([command, "--python", "3.11", "--python", "3.12"])
    assert error.value.code == 2


def test_stage_repeated_targets_preserve_cli_order() -> None:
    args = create_parser().parse_args(
        [
            "stage",
            "--python",
            "3.11",
            "--python",
            "3.14",
            "--platform",
            "linux-x64",
            "--platform",
            "windows-arm64",
        ]
    )
    assert args.python == ["3.11", "3.14"]
    assert args.platform == ["linux-x64", "windows-arm64"]


def test_init_and_dev_options_have_exact_destinations() -> None:
    init_args = parse_args(
        [
            "--verbose",
            "init",
            "project",
            "--force",
            "--name",
            "Example",
            "--description",
            "Description",
            "--python",
            "3.11",
            "--blender",
            "blender-bin",
            "--source-dir",
            "extension",
        ]
    )
    assert init_args.verbose is True
    assert init_args.directory == "project"
    assert init_args.force is True
    assert init_args.source_dir == "extension"
    assert init_args.python == "3.11"

    dev_args = parse_args(
        [
            "dev",
            "project",
            "--blender",
            "blender-bin",
            "--blend-file",
            "scene.blend",
            "--user-resources",
            "seed",
            "--link-mode",
            "copy",
            "--debugpy-host",
            "localhost",
            "--debugpy-port",
            "5678",
            "--python",
            "3.14",
            "--pip",
            "pip-bin",
            "--refresh-wheels",
        ]
    )
    assert dev_args.blend_file == "scene.blend"
    assert dev_args.user_resources == "seed"
    assert dev_args.debugpy_port == 5678
    assert dev_args.refresh_wheels is True


def test_handled_error_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(_args: argparse.Namespace) -> int:
        raise BlexError("broken project")

    monkeypatch.setattr(
        main_module,
        "parse_args",
        lambda _argv: argparse.Namespace(handler=fail),
    )
    assert main_module.main([]) == 1
    assert capsys.readouterr().err == "error: broken project\n"


def test_handler_status_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    def succeed(_args: argparse.Namespace) -> int:
        return 23

    monkeypatch.setattr(
        main_module,
        "parse_args",
        lambda _argv: argparse.Namespace(handler=succeed),
    )
    assert main_module.main([]) == 23


def test_keyboard_interrupt_returns_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_args: argparse.Namespace) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        main_module,
        "parse_args",
        lambda _argv: argparse.Namespace(handler=interrupt),
    )
    assert main_module.main([]) == 130


def test_keyboard_interrupt_while_parsing_returns_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_argv: object) -> argparse.Namespace:
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "parse_args", interrupt)
    assert main_module.main([]) == 130


def test_unexpected_error_is_not_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_args: argparse.Namespace) -> int:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(
        main_module,
        "parse_args",
        lambda _argv: argparse.Namespace(handler=fail),
    )
    with pytest.raises(RuntimeError, match="unexpected"):
        main_module.main([])


def test_python_module_entry_raises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "main", lambda: 7)
    with pytest.raises(SystemExit) as error:
        runpy.run_module("blex.__main__", run_name="__main__")
    assert error.value.code == 7
