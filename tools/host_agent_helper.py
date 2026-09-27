#!/usr/bin/env python3
"""Fixed direct-exec bootstrap for explicitly selected installed host agents.

Invoked through trusted Python -I -S. No shell, arbitrary arguments, agent
permission overrides, credential copying, or fallback process are provided.
"""
import hashlib
import json
import os
import sys


def main():
    if len(sys.argv) != 6 or sys.argv[1] not in {"codex", "claude"}:
        raise SystemExit("invalid host agent selection")
    agent, executable, device, inode, expected = sys.argv[1:]
    if not os.path.isabs(executable) or not os.access(executable, os.X_OK):
        raise SystemExit("host agent executable unavailable")
    directory = os.stat(".")
    if [directory.st_dev, directory.st_ino] != [int(device), int(inode)]:
        raise SystemExit("host project directory changed before launch")
    digest = hashlib.sha256()
    with open(executable, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise SystemExit("host agent executable changed before launch")
    environment = dict(os.environ)
    # The program is a host agent, not a tmux control client. This is not an
    # isolation boundary: a same-account process still has its normal access.
    environment.pop("TMUX", None)
    environment.pop("TMUX_PANE", None)
    arguments = [executable]
    if agent == "claude":
        updates = {"DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1",
                   "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                   "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
                   "FORCE_AUTOUPDATE_PLUGINS": "0"}
        environment.update(updates)
        # Command-line settings have higher priority than project settings.
        # Preserve the installed agent's own trust and permission workflow.
        arguments += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                      "--settings", json.dumps({"env": updates, "disableAllHooks": True}, separators=(",", ":"))]
    os.execve(executable, arguments, environment)


if __name__ == "__main__":
    main()
