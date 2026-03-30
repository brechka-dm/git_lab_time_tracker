"""Launch the tracker; application code lives under `source/`."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    source_dir = Path(__file__).resolve().parent / "source"
    sys.path.insert(0, str(source_dir))
    runpy.run_path(str(source_dir / "main.py"), run_name="__main__")
