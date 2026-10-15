"""Command-line interface. The only module that prints."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from entropass import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="entropass",
        description=(
            "Generate passwords and passphrases with secrets, "
            "and report their strength honestly, in bits."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    build_parser().parse_args(argv)
    return 0
