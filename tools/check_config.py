#!/usr/bin/env python3
"""Validate dashboard registration JSON without executing Botainer or SSH."""

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from botainer_dashboard.config import ConfigError, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Override the normal user config location")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "result": "valid registration configuration; executable identity and runtime unverified",
        "source": str(config.source_path) if config.source_path else "built-in defaults",
        "default_machine": config.default_machine,
        "machines": {
            name: {
                "transport": machine.transport,
                "default_installation": machine.default_installation,
                "installations": {
                    name: {"launcher_mode": install.launcher.mode,
                           "launcher_path": install.launcher.path,
                           "state_root": install.state_root}
                    for name, install in machine.installations.items()},
            } for name, machine in config.machines.items()},
        "side_effects": "none; no install, Botainer invocation, state creation or SSH connection",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
