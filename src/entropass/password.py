"""Password generation, uniform over every string that satisfies the settings."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence

from entropass.charsets import CharClass
from entropass.entropy import count_valid


def count_passwords(classes: Sequence[CharClass], length: int, *, require: bool = True) -> int:
    """Number of distinct passwords the generator can produce, all equally likely."""
    alphabet_size = sum(len(c.chars) for c in classes)
    required = [len(c.chars) for c in classes] if require else []
    return count_valid(alphabet_size, required, length)


def generate_password(
    classes: Sequence[CharClass],
    length: int,
    *,
    require: bool = True,
    randbelow: Callable[[int], int] = secrets.randbelow,
) -> str:
    """Generate a password; with ``require``, it contains at least one of every class.

    Each character is drawn uniformly from the whole alphabet and a password missing a
    required class is thrown away and redrawn. Conditioning a uniform draw on an event
    leaves it uniform over that event, so every valid password is equally likely and
    ``count_passwords`` gives the exact entropy. (Placing one character of each class and
    shuffling, the usual shortcut, is not uniform.)
    """
    if not classes:
        raise ValueError("at least one character class is needed")
    if length < 1:
        raise ValueError("length must be at least 1")
    if require and length < len(classes):
        raise ValueError(
            f"length must be at least {len(classes)} to include one of each required class"
        )

    alphabet = "".join(c.chars for c in classes)
    class_sets = [frozenset(c.chars) for c in classes] if require else []
    while True:
        candidate = "".join(alphabet[randbelow(len(alphabet))] for _ in range(length))
        if all(not cls.isdisjoint(candidate) for cls in class_sets):
            return candidate
