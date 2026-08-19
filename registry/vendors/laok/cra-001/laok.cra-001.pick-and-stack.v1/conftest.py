"""Make the bridge package importable when pytest is launched from anywhere."""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# The payment-boundary tests import x402_harness from the tests/ package.
_TESTS = os.path.join(_ROOT, "tests")
if os.path.isdir(_TESTS) and _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
