"""V&V test configuration — makes vv_helpers importable."""

import pathlib
import sys

VV_DIR = str(pathlib.Path(__file__).resolve().parent)
if VV_DIR not in sys.path:
    sys.path.insert(0, VV_DIR)
