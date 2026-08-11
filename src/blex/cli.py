import argparse

from blex.commands import build, dev, init


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blex",
        description="Blender Extension Helper",
    )
