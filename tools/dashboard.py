#!/usr/bin/env python3
"""Source-compatible entry point; installed users run botainer-dashboard."""
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[1]
_source = (_root.parent.parent if _root.name == "_resources"
           and _root.parent.name == "botainer_dashboard" else _root / "src")
sys.path.insert(0, str(_source))
from botainer_dashboard.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
