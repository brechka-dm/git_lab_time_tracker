"""Launch the tracker; application code lives under `source/`."""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "source" / "main.py"), run_name="__main__")
