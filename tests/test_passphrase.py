"""Passphrases: uniform word choice, the separator rule and physical dice rolls."""

import inspect
import random
import secrets

import pytest

from entropass.passphrase import (
    WORDLIST_SIZE,
    check_separator,
    generate_passphrase,
    load_wordlist,
    parse_roll,
)


def test_words_come_from_the_list_at_the_drawn_index() -> None:
    draws = iter([0, 7775, 1234])
    words = generate_passphrase(3, randbelow=lambda n: next(draws))
    wordlist = load_wordlist()
    assert words == [wordlist[0], wordlist[7775], wordlist[1234]]
    assert words[:2] == ["abacus", "zoom"]


def test_every_draw_is_over_the_whole_list() -> None:
    seen: list[int] = []

    def spy(n: int) -> int:
        seen.append(n)
        return 0

    generate_passphrase(6, randbelow=spy)
    assert seen == [WORDLIST_SIZE] * 6


def test_seeded_draws_are_reproducible_and_varied() -> None:
    a = generate_passphrase(8, randbelow=random.Random(1).randrange)
    b = generate_passphrase(8, randbelow=random.Random(1).randrange)
    assert a == b and len(set(a)) > 1


def test_uses_secrets_by_default() -> None:
    default = inspect.signature(generate_passphrase).parameters["randbelow"].default
    assert default is secrets.randbelow


@pytest.mark.parametrize("count", [0, -1])
def test_at_least_one_word(count: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        generate_passphrase(count)


class TestParseRoll:
    @pytest.mark.parametrize(
        ("text", "index"),
        [
            ("11111", 0),
            ("66666", 7775),
            ("1 1 1 1 2", 1),
            ("  2 1 1 1 1\n", 1296),
            ("6,6,6,6,5", 7774),
            ("1\t1\t1\t1\t2\r\n", 1),
        ],
    )
    def test_valid_rolls(self, text: str, index: int) -> None:
        assert parse_roll(text) == index

    @pytest.mark.parametrize(
        "text", ["", "1111", "111111", "11117", "01111", "1111a", "one", "1 1 1 1", "\u0661" * 5]
    )
    def test_invalid_rolls(self, text: str) -> None:
        with pytest.raises(ValueError, match="five dice"):
            parse_roll(text)

    def test_roll_index_matches_the_eff_code(self) -> None:
        # EFF's code for "zoology" is 66665.
        assert load_wordlist()[parse_roll("66665")] == "zoology"


class TestSeparator:
    @pytest.mark.parametrize("sep", [" ", ".", "_", "+", "/", "#", "..", "7", "A"])
    def test_allowed(self, sep: str) -> None:
        check_separator(sep)

    @pytest.mark.parametrize("sep", ["-", "a", " x ", "e.", "--"])
    def test_characters_found_in_words_are_rejected(self, sep: str) -> None:
        with pytest.raises(ValueError, match="appears in the wordlist"):
            check_separator(sep)

    @pytest.mark.parametrize("sep", ["", "\t", "\n", "é", "\x00"])
    def test_empty_control_and_non_ascii_are_rejected(self, sep: str) -> None:
        with pytest.raises(ValueError, match="separator"):
            check_separator(sep)
