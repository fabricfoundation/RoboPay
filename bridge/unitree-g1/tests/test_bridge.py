"""Bridge integration tests for unitree-g1 (Tier 1 planar biped).

The full manifest-vs-code contract lives in tests/test_profiles.py. This module
re-exports those tests so the bridge integration suite and the profile suite
are collected together (and can never drift from each other).
"""
from tests.test_profiles import *  # noqa: F401,F403
