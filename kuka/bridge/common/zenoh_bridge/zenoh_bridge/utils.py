"""Utility functions shared across all adapters."""


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
