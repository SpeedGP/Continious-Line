"""Make the extension modules importable when running `python -m pytest`
from the continuous_line folder (or from anywhere)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
