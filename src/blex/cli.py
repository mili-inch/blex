import argparse
from importlib.metadata import version

from blex.commands import build, dev, init


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blex",
        description="Development tool for Blender Extensions",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version('blex')}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    init.configure_parser(subparsers)
    dev.configure_parser(subparsers)
    build.configure_parser(subparsers)

    return parser


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()

    return args.handler(args)