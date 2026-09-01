"""Exact integer mathematics for GLD management Research F0."""

from __future__ import annotations

from .errors import ManagementResearchError


PPM = 1_000_000


def round_half_even_div(numerator: int, denominator: int) -> int:
    """Divide exact integers using symmetric round-to-nearest, ties-to-even."""

    if type(numerator) is not int or type(denominator) is not int:
        raise ManagementResearchError("INTEGER_MATH_INPUT_INVALID")
    if denominator == 0:
        raise ManagementResearchError("INTEGER_MATH_DIVISION_BY_ZERO")
    negative = (numerator < 0) != (denominator < 0)
    absolute_numerator = abs(numerator)
    absolute_denominator = abs(denominator)
    quotient, remainder = divmod(absolute_numerator, absolute_denominator)
    doubled = remainder * 2
    if doubled > absolute_denominator or (
        doubled == absolute_denominator and quotient % 2 == 1
    ):
        quotient += 1
    return -quotient if negative else quotient


def ppm_multiply(left_ppm: int, right_ppm: int) -> int:
    """Multiply two ppm-scaled integers with Research F0 rounding."""

    return round_half_even_div(left_ppm * right_ppm, PPM)


def clamp(value: int, minimum: int, maximum: int) -> int:
    """Clamp an exact integer to an inclusive range."""

    return min(maximum, max(minimum, value))


def sign(value: int) -> int:
    """Return -1, 0, or +1 for an exact integer."""

    return (value > 0) - (value < 0)


def ceil_fraction(value: int, numerator: int, denominator: int) -> int:
    """Return ceil(value * numerator / denominator) for nonnegative inputs."""

    if min(value, numerator) < 0 or denominator <= 0:
        raise ManagementResearchError("INTEGER_CEILING_INPUT_INVALID")
    return (value * numerator + denominator - 1) // denominator


__all__ = [
    "PPM",
    "ceil_fraction",
    "clamp",
    "ppm_multiply",
    "round_half_even_div",
    "sign",
]
