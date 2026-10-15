"""The entropy count is checked exactly against brute-force enumeration."""

import itertools
import math

import pytest

from entropass.entropy import bits, count_valid, floor_bits


def brute_force(class_sizes: list[int], required: list[bool], length: int) -> int:
    """Count length-L strings over disjoint classes that hit every required class."""
    labelled = [(i, j) for i, size in enumerate(class_sizes) for j in range(size)]
    needed = {i for i, req in enumerate(required) if req}
    return sum(needed <= {cls for cls, _ in s} for s in itertools.product(labelled, repeat=length))


GRID = [
    (sizes, required, length)
    for sizes in ([1], [2], [1, 1], [2, 1], [3, 2], [1, 2, 3], [2, 2, 1])
    for required in itertools.product([False, True], repeat=len(sizes))
    for length in range(0, 6)
]


@pytest.mark.parametrize(("sizes", "required", "length"), GRID)
def test_count_matches_brute_force(
    sizes: list[int], required: tuple[bool, ...], length: int
) -> None:
    req_sizes = [n for n, r in zip(sizes, required, strict=True) if r]
    assert count_valid(sum(sizes), req_sizes, length) == brute_force(sizes, list(required), length)


def test_hand_checked_example() -> None:
    # {a, b} and {1}, length 3, both required: 27 - 2**3 (no digit) - 1 (no letter) = 18.
    assert count_valid(3, [2, 1], 3) == 18


def test_shorter_than_the_required_classes_has_no_valid_strings() -> None:
    assert count_valid(94, [26, 26, 10, 32], 3) == 0


def test_no_required_classes_is_the_naive_count() -> None:
    assert count_valid(94, [], 20) == 94**20


def test_required_classes_cost_entropy_at_the_defaults() -> None:
    exact = bits(count_valid(94, [26, 26, 10, 32], 20))
    naive = 20 * math.log2(94)
    assert exact == pytest.approx(130.9259, abs=1e-4)
    assert 0.15 < naive - exact < 0.17


def test_long_passwords_stay_exact() -> None:
    count = count_valid(94, [26, 26, 10, 32], 1000)
    assert count < 94**1000
    assert bits(count) == pytest.approx(6554.5889, abs=1e-4)


@pytest.mark.parametrize(
    ("alphabet", "required", "length"),
    [(0, [], 5), (5, [0], 5), (5, [-1], 5), (5, [3, 3], 5), (5, [], -1)],
)
def test_impossible_configurations_are_rejected(
    alphabet: int, required: list[int], length: int
) -> None:
    with pytest.raises(ValueError):
        count_valid(alphabet, required, length)


def test_bits_of_nothing_is_an_error() -> None:
    with pytest.raises(ValueError):
        bits(0)


@pytest.mark.parametrize(
    ("count", "shown"),
    [(2**64, 64.0), (2**64 - 1, 63.9), (7776**5, 64.6), (94**20, 131.0), (1, 0.0), (2, 1.0)],
)
def test_display_rounds_down_never_up(count: int, shown: float) -> None:
    assert floor_bits(count) == shown


def is_exact_floor(shown: float, count: int) -> bool:
    """2**(t/10) <= count < 2**((t+1)/10), checked in integers."""
    tenths = round(shown * 10)
    return bool(2**tenths <= count**10 < 2 ** (tenths + 1))


def test_display_just_below_a_boundary_is_not_rounded_up() -> None:
    # 32 symbols, two required classes of 24 and 8 (lower + digits without ambiguous
    # characters), length 108: the true entropy is 539.99..., and float log2 says 540.0.
    count = count_valid(32, [24, 8], 108)
    assert count < 2**540
    assert floor_bits(count) == 539.9
    assert is_exact_floor(floor_bits(count), count)


@pytest.mark.parametrize("length", range(1, 300))
def test_display_is_the_exact_floor(length: int) -> None:
    for alphabet, required in [(32, [24, 8]), (94, [26, 26, 10, 32]), (62, []), (16, [8, 8])]:
        if length < len(required):
            continue  # no valid strings, covered by its own test
        count = count_valid(alphabet, required, length)
        assert is_exact_floor(floor_bits(count), count)


def test_floor_of_nothing_is_an_error() -> None:
    with pytest.raises(ValueError):
        floor_bits(0)
