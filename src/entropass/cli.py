"""Command-line interface. The only module that prints.

stdout carries the secret and nothing else; the entropy summary goes to stderr, so
``entropass | pbcopy`` and ``pw=$(entropass)`` capture exactly the secret.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from entropass import __version__
from entropass.charsets import CharClass, build_classes
from entropass.entropy import bits, floor_1dp
from entropass.password import count_passwords, generate_password

MAX_LENGTH = 1024
COMMANDS = {"password"}


def _length(value: str) -> int:
    try:
        length = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"length must be a whole number, not {value!r}") from None
    if not 1 <= length <= MAX_LENGTH:
        raise argparse.ArgumentTypeError(f"length must be between 1 and {MAX_LENGTH}")
    return length


def describe(shown: float, length: int, classes: Sequence[CharClass], *, require: bool) -> str:
    """The entropy line printed on stderr."""
    alphabet = sum(len(c.chars) for c in classes)
    names = ", ".join(c.name for c in classes)
    if len(classes) == 1:
        detail = f" ({names})"
    elif require:
        detail = f", with at least one each of {names}"
    else:
        detail = f" ({names}, none required)"
    chars = "character" if length == 1 else "characters"
    return (
        f"{shown:.1f} bits of entropy: {length} {chars} from a {alphabet}-symbol alphabet{detail}"
    )


def _password(args: argparse.Namespace) -> int:
    parser: argparse.ArgumentParser = args.parser
    try:
        classes = build_classes(
            lower=args.lower,
            upper=args.upper,
            digits=args.digits,
            symbols=args.symbols,
            symbol_chars=args.symbol_chars,
            exclude_ambiguous=args.exclude_ambiguous,
        )
        password = generate_password(classes, args.length, require=args.require)
    except ValueError as exc:
        parser.error(str(exc))

    print(password)
    if not args.quiet:
        shown = floor_1dp(bits(count_passwords(classes, args.length, require=args.require)))
        print(describe(shown, args.length, classes, require=args.require), file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="entropass",
        description=(
            "Generate passwords with secrets, and report their strength honestly, in bits."
        ),
        epilog="Without a command, entropass runs `password` with any options given.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command", required=True)

    pw = commands.add_parser(
        "password",
        help="generate a random password",
        description="Generate a random password from the chosen character classes.",
    )
    pw.add_argument(
        "-l", "--length", type=_length, default=20, help="number of characters (default: 20)"
    )
    for name in ("lower", "upper", "digits", "symbols"):
        pw.add_argument(f"--no-{name}", dest=name, action="store_false", help=f"leave out {name}")
    pw.add_argument(
        "--symbols",
        dest="symbol_chars",
        metavar="CHARS",
        help="use only these symbols; write --symbols=CHARS if they start with - (default: all)",
    )
    pw.add_argument(
        "--exclude-ambiguous",
        action="store_true",
        help="leave out characters that are easy to confuse: 0 O o 1 l I |",
    )
    pw.add_argument(
        "--no-require",
        dest="require",
        action="store_false",
        help="allow passwords that miss a class (by default each class appears at least once)",
    )
    pw.add_argument("-q", "--quiet", action="store_true", help="do not print the entropy line")
    pw.set_defaults(run=_password, parser=pw)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # Without a command, arguments go to `password`: `entropass -l 12` works, and a bare
    # `entropass` generates a password with the defaults.
    if not argv or argv[0] not in COMMANDS | {"-h", "--help", "--version"}:
        argv = ["password", *argv]
    args = parser.parse_args(argv)
    try:
        code: int = args.run(args)
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader went away (`entropass | head -c 8`). Point stdout at devnull so the
        # interpreter's final flush cannot raise again, and fail without a traceback.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 1
    return code
