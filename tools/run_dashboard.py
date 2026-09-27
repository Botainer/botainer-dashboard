#!/usr/bin/env python3
"""Launch using the already-installed project environment; never install anything."""
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[1]
_source = (_root.parent.parent if _root.name == "_resources"
           and _root.parent.name == "botainer_dashboard" else _root / "src")
sys.path.insert(0, str(_source))
from botainer_dashboard.launcher import main

if __name__ == "__main__":
    main()
