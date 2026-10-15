"""Command-line interface. The only module that prints.

stdout carries the secret and nothing else; the entropy summary goes to stderr, so
``pw=$(entropass)`` captures exactly the secret (a pipe also gets the trailing newline).
"""

from __future__ import annotations

import argparse
import errno
import getpass
import os
import shutil
import sys
from collections.abc import Sequence

from entropass import __version__, clipboard
from entropass.charsets import CharClass, build_classes
from entropass.entropy import floor_bits
from entropass.passphrase import (
    WORDLIST_SIZE,
    check_separator,
    generate_passphrase,
    load_wordlist,
    parse_roll,
)
from entropass.password import count_passwords, generate_password

MAX_LENGTH = 1024
MAX_WORDS = 100
MAX_CLEAR_AFTER = 86400  # one day
COMMANDS = {"password", "passphrase"}
EFF_CREDIT = (
    "Words come from the EFF Large Wordlist for Passphrases by Joseph Bonneau and the "
    "Electronic Frontier Foundation (https://www.eff.org/dice), CC BY 4.0."
)


class InputError(Exception):
    """Bad input read at run time (not a bad option): exit status 1."""


def _length(value: str) -> int:
    try:
        length = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"length must be a whole number, not {value!r}") from None
    if not 1 <= length <= MAX_LENGTH:
        raise argparse.ArgumentTypeError(f"length must be between 1 and {MAX_LENGTH}")
    return length


def _words(value: str) -> int:
    try:
        words = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"words must be a whole number, not {value!r}") from None
    if not 1 <= words <= MAX_WORDS:
        raise argparse.ArgumentTypeError(f"words must be between 1 and {MAX_WORDS}")
    return words


def _seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"must be a whole number of seconds, not {value!r}"
        ) from None
    if not 0 <= seconds <= MAX_CLEAR_AFTER:
        raise argparse.ArgumentTypeError(f"must be between 0 and {MAX_CLEAR_AFTER} seconds")
    return seconds


def _deliver(args: argparse.Namespace, secret: str, entropy_line: str) -> int:
    """Print the secret on stdout, or copy it with --copy; then the entropy line on stderr."""
    if not args.copy:
        print(secret)
        if not args.quiet:
            print(entropy_line, file=sys.stderr)
        return 0

    # With --copy the secret is never printed, not even when copying fails: printing it
    # would leave it in the terminal's scrollback, which is what --copy avoids.
    try:
        backend = clipboard.detect(sys.platform, os.environ, shutil.which)
        clipboard.copy(backend, secret)
    except clipboard.ClipboardUnavailable as exc:
        print(f"entropass: error: {exc}", file=sys.stderr)
        return 1
    if args.clear_after:
        clipboard.spawn_clearer(args.clear_after, clipboard.digest(secret))
    if not args.quiet:
        if args.clear_after:
            when = f"It clears in {args.clear_after} s if it still holds it."
        else:
            when = "It will not be cleared."
        print(f"Copied the {args.command} to the clipboard. {when}", file=sys.stderr)
        print(entropy_line, file=sys.stderr)
    return 0


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--copy",
        action="store_true",
        help="copy to the clipboard instead of printing, and clear it later",
    )
    parser.add_argument(
        "--clear-after",
        type=_seconds,
        default=30,
        metavar="SECS",
        help="with --copy, clear the clipboard after this many seconds if it still holds "
        "the secret; 0 never clears (default: 30)",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="do not print the entropy line")


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

    shown = floor_bits(count_passwords(classes, args.length, require=args.require))
    return _deliver(args, password, describe(shown, args.length, classes, require=args.require))


def _read_rolls(count: int) -> list[int]:
    """Read one roll of five dice per word. The rolls are the passphrase: never echo them."""
    if sys.stdin is None:  # started with stdin closed, e.g. `entropass passphrase --dice <&-`
        raise InputError("there is no input to read the rolls from")
    if sys.stdin.isatty():
        rolls = []
        for n in range(1, count + 1):
            prompt = f"Word {n} of {count}: roll five dice and enter them (e.g. 4 1 6 2 3): "
            while True:
                try:
                    text = getpass.getpass(prompt)
                except EOFError:
                    print(file=sys.stderr)  # end the prompt line before the error
                    raise InputError("no roll entered") from None
                try:
                    rolls.append(parse_roll(text))
                    break
                except ValueError as exc:
                    print(exc, file=sys.stderr)
        return rolls

    try:
        piped = sys.stdin.read()
    except UnicodeDecodeError:
        raise InputError("the rolls could not be read as text") from None
    lines = [line for line in piped.splitlines() if line.strip()]
    if len(lines) != count:
        raise InputError(f"expected {count} rolls, one per line, but read {len(lines)}")
    rolls = []
    for n, line in enumerate(lines, 1):
        try:
            rolls.append(parse_roll(line))
        except ValueError:
            raise InputError(f"roll {n} is not five dice, each 1 to 6") from None
    return rolls


def _passphrase(args: argparse.Namespace) -> int:
    parser: argparse.ArgumentParser = args.parser
    try:
        check_separator(args.sep)
    except ValueError as exc:
        parser.error(str(exc))

    if args.dice:
        try:
            rolls = _read_rolls(args.words)
        except InputError as exc:
            print(f"entropass: error: {exc}", file=sys.stderr)
            return 1
        wordlist = load_wordlist()
        words = [wordlist[i] for i in rolls]
    else:
        words = generate_passphrase(args.words)

    shown = floor_bits(WORDLIST_SIZE**args.words)
    noun = "word" if args.words == 1 else "words"
    fair = ", assuming fair dice" if args.dice else ""
    return _deliver(
        args,
        args.sep.join(words),
        f"{shown:.1f} bits of entropy: {args.words} {noun} from the EFF large wordlist "
        f"({WORDLIST_SIZE} words){fair}",
    )


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
    _add_output_options(pw)
    pw.set_defaults(run=_password, parser=pw)

    pp = commands.add_parser(
        "passphrase",
        help="generate a diceware passphrase",
        description="Generate a passphrase of random words, or of words from your own dice.",
        epilog=EFF_CREDIT,
    )
    pp.add_argument("-w", "--words", type=_words, default=6, help="number of words (default: 6)")
    pp.add_argument(
        "--sep",
        default=" ",
        help="text between words (default: a space); no lowercase letters and no -",
    )
    pp.add_argument(
        "--dice",
        action="store_true",
        help="use your own rolls of five dice per word instead of the computer's randomness",
    )
    _add_output_options(pp)
    pp.set_defaults(run=_passphrase, parser=pp)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    try:
        try:
            return _dispatch(argv)
        except KeyboardInterrupt:
            print(file=sys.stderr)  # end the prompt line the user interrupted
            return 130
        finally:
            # Flush here, inside the handler, rather than at interpreter exit. This also
            # covers --help, --version and usage errors, which leave through SystemExit.
            sys.stdout.flush()
    except OSError as exc:
        if not _is_closed_pipe(exc):
            raise
        # The reader went away (`entropass | head -c 8`). Point stdout at devnull so the
        # interpreter's final flush cannot raise again, and fail without a traceback.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
        return 1


def _is_closed_pipe(exc: OSError) -> bool:
    # Windows reports a write to a closed pipe as EINVAL rather than EPIPE.
    return isinstance(exc, BrokenPipeError) or exc.errno in (errno.EPIPE, errno.EINVAL)


def _dispatch(argv: Sequence[str] | None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # Without a command, arguments go to `password`: `entropass -l 12` works, and a bare
    # `entropass` generates a password with the defaults.
    if not argv or argv[0] not in COMMANDS | {"-h", "--help", "--version"}:
        argv = ["password", *argv]
    args = parser.parse_args(argv)
    code: int = args.run(args)
    return code
