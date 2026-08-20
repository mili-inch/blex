"""Top-level blex command entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import cast

from blex.cli import parse_args
from blex.core.errors import BlexError


def main(argv: Sequence[str] | None = None) -> int:
    """Run blex and return its process exit code."""
    try:
        try:
            args = parse_args(argv)
        except SystemExit as error:
            return cast(int, error.code)
        handler = cast(Callable[[argparse.Namespace], int], args.handler)
        return handler(args)
    except KeyboardInterrupt:
        return 130
    except BlexError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
