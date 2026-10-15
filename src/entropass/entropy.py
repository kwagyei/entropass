"""Exact entropy of generated secrets.

Every figure here is log2 of an exact count of equally likely outcomes, so it is the
entropy an attacker faces who knows the generator and the settings used.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import combinations


def count_valid(alphabet_size: int, required_sizes: Sequence[int], length: int) -> int:
    """Count strings of ``length`` over the alphabet that contain every required class.

    The classes are disjoint subsets of the alphabet; ``required_sizes`` holds the size of
    each class that must appear at least once. By inclusion-exclusion over the required
    classes that are missing:

        count = sum over S of (-1)^|S| * (alphabet_size - sum of sizes in S) ^ length
    """
    if alphabet_size < 1:
        raise ValueError("the alphabet must not be empty")
    if any(size < 1 for size in required_sizes):
        raise ValueError("a required class must not be empty")
    if sum(required_sizes) > alphabet_size:
        raise ValueError("required classes cannot be larger than the alphabet")
    if length < 0:
        raise ValueError("length must not be negative")

    return sum(
        (-1) ** k * (alphabet_size - sum(missing)) ** length
        for k in range(len(required_sizes) + 1)
        for missing in combinations(required_sizes, k)
    )


def bits(count: int) -> float:
    """Entropy in bits of a uniform choice among ``count`` outcomes."""
    if count < 1:
        raise ValueError("there are no outcomes to choose from")
    return math.log2(count)


def floor_1dp(value: float) -> float:
    """Round down to one decimal place, so displayed strength is never overstated."""
    return math.floor(value * 10) / 10
