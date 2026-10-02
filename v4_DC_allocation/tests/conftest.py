"""conftest.py — add v4_DC_allocation/ to sys.path so tests can import from logic.*"""
import sys
import os

# Add v4_DC_allocation/ directory to sys.path so `from logic.classify import ...` works
_HERE = os.path.dirname(__file__)           # .../v4_DC_allocation/tests/
_V4_ROOT = os.path.abspath(os.path.join(_HERE, ".."))  # .../v4_DC_allocation/
if _V4_ROOT not in sys.path:
    sys.path.insert(0, _V4_ROOT)
