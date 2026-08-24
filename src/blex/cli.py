"""Command-line parser for blex."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from blex import __version__
from blex.commands import dev as dev_command
from blex.commands import init as init_command
from blex.commands import stage as stage_command

PLATFORMS = (
    "windows-x64",
    "windows-arm64",
    "macos-x64",
    "macos-arm64",
    "linux-x64",
)


def _command_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser], name: str
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, allow_abbrev=False, add_help=False)
    parser.add_argument("--help", action="help", help="show this help message and exit")
    return parser


def create_parser() -> argparse.ArgumentParser:
    """Create the complete command-line parser."""
    parser = argparse.ArgumentParser(prog="blex", allow_abbrev=False, add_help=False)
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--verbose", action="store_true", help="show resolved paths and commands")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = _command_parser(subparsers, "init")
    init_parser.add_argument("directory", nargs="?", default=".")
    init_parser.add_argument("--force", action="store_true")
    init_parser.add_argument("--name")
    init_parser.add_argument("--description")
    init_parser.add_argument("--python", action="append")
    init_parser.add_argument("--blender")
    init_parser.add_argument("--source-dir")
    init_parser.set_defaults(handler=init_command.run)

    dev_parser = _command_parser(subparsers, "dev")
    dev_parser.add_argument("directory", nargs="?", default=".")
    dev_parser.add_argument("--source-dir")
    dev_parser.add_argument("--blender")
    dev_parser.add_argument("--blend-file")
    dev_parser.add_argument("--user-resources")
    dev_parser.add_argument("--link-mode", choices=("auto", "symlink", "copy"))
    dev_parser.add_argument("--reload-panel", action=argparse.BooleanOptionalAction, default=None)
    dev_parser.add_argument("--debugpy", action=argparse.BooleanOptionalAction, default=None)
    dev_parser.add_argument("--debugpy-host")
    dev_parser.add_argument("--debugpy-port", type=int)
    dev_parser.add_argument("--wheels", action=argparse.BooleanOptionalAction, default=None)
    dev_parser.add_argument("--python", action="append")
    dev_parser.add_argument("--pip")
    dev_parser.add_argument("--refresh-wheels", action="store_true")
    dev_parser.epilog = "Pass additional Blender arguments after an explicit -- delimiter."
    dev_parser.set_defaults(handler=dev_command.run)

    stage_parser = _command_parser(subparsers, "stage")
    stage_parser.add_argument("directory", nargs="?", default=".")
    stage_parser.add_argument("--source-dir")
    stage_parser.add_argument("--output-dir")
    stage_parser.add_argument("--wheels", action=argparse.BooleanOptionalAction, default=None)
    stage_parser.add_argument("--python", action="append")
    stage_parser.add_argument("--platform", action="append", choices=PLATFORMS)
    stage_parser.add_argument("--pip")
    stage_parser.add_argument("--refresh-wheels", action="store_true")
    stage_parser.set_defaults(handler=stage_command.run)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse an explicit argument sequence or ``sys.argv`` when omitted."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    blender_args: list[str] = []
    has_delimiter = "--" in arguments
    if has_delimiter:
        delimiter = arguments.index("--")
        blender_args = arguments[delimiter + 1 :]
        arguments = arguments[:delimiter]

    parser = create_parser()
    parsed = parser.parse_args(arguments)
    if has_delimiter and parsed.command != "dev":
        parser.error("Blender arguments are only supported by the dev command")
    if parsed.command in {"init", "dev"} and parsed.python is not None:
        if len(parsed.python) != 1:
            parser.error(f"--python may be specified only once for the {parsed.command} command")
        parsed.python = parsed.python[0]
    if parsed.command == "dev":
        parsed.blender_args = blender_args
    return parsed
