"""Character classes for passwords."""

from __future__ import annotations

import string
from dataclasses import dataclass

# Characters easily confused with one another in common fonts.
AMBIGUOUS = "0Oo1lI|"


@dataclass(frozen=True)
class CharClass:
    name: str
    chars: str


def build_classes(
    *,
    lower: bool = True,
    upper: bool = True,
    digits: bool = True,
    symbols: bool = True,
    symbol_chars: str | None = None,
    exclude_ambiguous: bool = False,
) -> list[CharClass]:
    """Build the enabled, disjoint character classes. Raises ValueError if unusable."""
    if symbol_chars is not None:
        if not symbols:
            raise ValueError("custom symbols were given but symbols are switched off")
        if not symbol_chars or not set(symbol_chars) <= set(string.punctuation):
            raise ValueError(
                "custom symbols must be ASCII punctuation, with no letters, digits or spaces"
            )
        symbol_chars = "".join(dict.fromkeys(symbol_chars))

    wanted = [
        ("lower", lower, string.ascii_lowercase),
        ("upper", upper, string.ascii_uppercase),
        ("digits", digits, string.digits),
        ("symbols", symbols, symbol_chars or string.punctuation),
    ]
    classes = []
    for name, enabled, chars in wanted:
        if not enabled:
            continue
        if exclude_ambiguous:
            chars = "".join(ch for ch in chars if ch not in AMBIGUOUS)
        if not chars:
            raise ValueError(f"no {name} are left once ambiguous characters are excluded")
        classes.append(CharClass(name, chars))

    if not classes:
        raise ValueError("at least one character class must be enabled")
    return classes
