"""Password generation: the right characters, every required class, and uniformity."""

import inspect
import itertools
import random
import secrets
from collections import Counter
from collections.abc import Callable

import pytest

from entropass.charsets import AMBIGUOUS, CharClass, build_classes
from entropass.password import count_passwords, generate_password

TINY = [CharClass("letters", "a"), CharClass("digits", "1")]
# Every string over {a, 1} of length 4 except "aaaa" and "1111".
TINY_VALID = sorted("".join(p) for p in itertools.product("a1", repeat=4) if "a" in p and "1" in p)
SAMPLES = 1000 * len(TINY_VALID)
# Chi-square critical value for 13 degrees of freedom at p = 0.001.
CHI2_CRIT_DF13 = 34.528


def chi_square(draw: Callable[[], str]) -> float:
    counts = Counter(draw() for _ in range(SAMPLES))
    assert set(counts) <= set(TINY_VALID)
    expected = SAMPLES / len(TINY_VALID)
    return sum((counts[s] - expected) ** 2 / expected for s in TINY_VALID)


def test_generation_is_uniform_over_valid_strings() -> None:
    rng = random.Random(20261015)
    stat = chi_square(lambda: generate_password(TINY, 4, randbelow=rng.randrange))
    assert stat < CHI2_CRIT_DF13


def test_one_of_each_then_shuffle_is_not_uniform() -> None:
    """Documents why entropass rejects and redraws instead of the common shortcut."""
    rng = random.Random(20261015)

    def shortcut() -> str:
        chars = ["a", "1", rng.choice("a1"), rng.choice("a1")]
        rng.shuffle(chars)
        return "".join(chars)

    assert chi_square(shortcut) > CHI2_CRIT_DF13


def test_tiny_count_matches_the_valid_set() -> None:
    assert count_passwords(TINY, 4) == len(TINY_VALID)
    assert count_passwords(TINY, 4, require=False) == 16


@pytest.mark.parametrize("length", [4, 5, 12, 20, 64])
def test_default_passwords_use_every_class(length: int) -> None:
    classes = build_classes()
    alphabet = "".join(c.chars for c in classes)
    for _ in range(200):
        pw = generate_password(classes, length)
        assert len(pw) == length
        assert set(pw) <= set(alphabet)
        for cls in classes:
            assert set(pw) & set(cls.chars), cls.name


def test_without_requirement_a_class_may_be_missing() -> None:
    classes = [CharClass("letters", "a"), CharClass("digits", "1")]
    seen = {generate_password(classes, 1, require=False) for _ in range(200)}
    assert seen == {"a", "1"}


def test_uses_secrets_by_default() -> None:
    default = inspect.signature(generate_password).parameters["randbelow"].default
    assert default is secrets.randbelow


def test_too_short_for_the_required_classes_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 4"):
        generate_password(build_classes(), 3)


@pytest.mark.parametrize("length", [0, -1])
def test_non_positive_length_is_rejected(length: int) -> None:
    with pytest.raises(ValueError):
        generate_password(build_classes(), length, require=False)


def test_no_classes_is_rejected() -> None:
    with pytest.raises(ValueError):
        generate_password([], 10)


class TestBuildClasses:
    def test_defaults_are_the_94_printable_ascii_symbols(self) -> None:
        classes = build_classes()
        assert [c.name for c in classes] == ["lower", "upper", "digits", "symbols"]
        alphabet = "".join(c.chars for c in classes)
        assert len(alphabet) == len(set(alphabet)) == 94
        assert all(33 <= ord(ch) <= 126 for ch in alphabet)

    def test_classes_can_be_switched_off(self) -> None:
        classes = build_classes(upper=False, symbols=False)
        assert [c.name for c in classes] == ["lower", "digits"]

    def test_excluding_ambiguous_characters(self) -> None:
        alphabet = "".join(c.chars for c in build_classes(exclude_ambiguous=True))
        assert not set(alphabet) & set(AMBIGUOUS)
        assert len(alphabet) == 94 - len(AMBIGUOUS)

    def test_custom_symbols_are_deduplicated_in_order(self) -> None:
        classes = build_classes(symbol_chars="!@!#@")
        assert classes[-1] == CharClass("symbols", "!@#")

    @pytest.mark.parametrize("chars", ["", "a", "!a", "! ", "\t", "é", "!\n"])
    def test_custom_symbols_must_be_ascii_punctuation(self, chars: str) -> None:
        with pytest.raises(ValueError, match="symbols"):
            build_classes(symbol_chars=chars)

    def test_a_class_emptied_by_exclusion_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="symbols"):
            build_classes(symbol_chars="|", exclude_ambiguous=True)

    def test_symbols_given_but_switched_off_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="symbols"):
            build_classes(symbols=False, symbol_chars="!")

    def test_switching_everything_off_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            build_classes(lower=False, upper=False, digits=False, symbols=False)
