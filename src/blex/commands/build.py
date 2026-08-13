import argparse


def configure_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("build")
    parser.set_defaults(handler=handle)


def handle(_: argparse.Namespace) -> int:
    raise NotImplementedError("The build command is not implemented")
