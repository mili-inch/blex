import argparse


def configure_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("init")
    parser.set_defaults(handler=handle)


def handle(_: argparse.Namespace) -> int:
    raise NotImplementedError("The init command is not implemented")
