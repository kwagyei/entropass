"""Diceware passphrases from the EFF large wordlist."""

from __future__ import annotations

import functools
import secrets
from collections.abc import Callable
from importlib.resources import files

DICE_PER_WORD = 5
WORDLIST_SIZE = 6**DICE_PER_WORD  # 7776: one word for every roll of five dice
_DIE_FACES = "123456"


@functools.cache
def load_wordlist() -> tuple[str, ...]:
    """The words in roll order: index 0 is 11111, index 7775 is 66666."""
    text = (files("entropass") / "data" / "eff_large_wordlist.txt").read_text("ascii")
    # The file's integrity is pinned by a SHA-256 test, not checked at run time: anyone who
    # can alter the installed file can alter a run-time check too.
    return tuple(line.split("\t", 1)[1] for line in text.splitlines())


def generate_passphrase(
    count: int, *, randbelow: Callable[[int], int] = secrets.randbelow
) -> list[str]:
    """Choose ``count`` words independently and uniformly from the wordlist."""
    if count < 1:
        raise ValueError("a passphrase needs at least 1 word")
    words = load_wordlist()
    return [words[randbelow(WORDLIST_SIZE)] for _ in range(count)]


def parse_roll(text: str) -> int:
    """Turn a roll of five dice into a word index.

    "41623", "4 1 6 2 3" and "4,1,6,2,3" are all accepted: ASCII whitespace and commas are
    ignored. Anything else, including non-ASCII digits and spaces, is rejected.
    """
    digits = "".join(ch for ch in text if ch not in " \t\r\n,")
    if len(digits) != DICE_PER_WORD or not set(digits) <= set(_DIE_FACES):
        raise ValueError("a roll is five dice, each 1 to 6, for example 41623")
    index = 0
    for digit in digits:
        index = index * 6 + _DIE_FACES.index(digit)
    return index


def check_separator(sep: str) -> None:
    """Reject a separator that could make two word sequences join to the same string.

    If no separator character occurs in any word, a passphrase splits back into exactly
    one sequence of words, so the entropy stays exactly ``count * log2(7776)``. Hyphens
    are therefore out: four words (drop-down, felt-tip, t-shirt, yo-yo) contain one.
    """
    if not sep:
        raise ValueError("the separator must not be empty")
    if not all(" " <= ch <= "~" for ch in sep):
        raise ValueError("the separator must be printable ASCII")
    used = frozenset("".join(load_wordlist()))
    for ch in sep:
        if ch in used:
            raise ValueError(
                f"separator character {ch!r} appears in the wordlist; a separator must "
                "not, so that every passphrase splits back into exactly one list of words"
            )
