import argparse


def configure_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("dev")
    parser.set_defaults(handler=handle)


def handle(_: argparse.Namespace) -> int:
    raise NotImplementedError("The dev command is not implemented")
