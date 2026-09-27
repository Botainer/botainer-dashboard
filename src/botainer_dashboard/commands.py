"""Build reviewed local CLI argv; never execute or import Botainer.

Source assumptions for the Botainer 0.1.0a5 target:
cli/{init,start,list_cmd,status,attach,stop}.py; core/composition.py's 16-hex
session IDs; state/dir.py's MY_BOTAINER override. Capability/identity verification
and lifecycle proof remain separate gates. A CommandPlan is NOT authorization,
readiness evidence, or a guarantee that a selected executable is trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from .context import (
    ContextError, ExecutionContext, RuntimeTarget, _text, canonical_directory,
)


class CommandError(ValueError):
    """The requested operation is unsupported or insufficiently scoped."""


@dataclass(frozen=True)
class CommandPlan:
    context: ExecutionContext
    action: str
    argv: Tuple[str, ...]
    cwd: Path
    terminal: bool = False

    @property
    def env_overrides(self) -> Tuple[Tuple[str, str], ...]:
        return (("MY_BOTAINER", str(self.context.state_root)),
                ("BOTAINER_NO_TIPS", "1"))

    def environment_from(self, trusted_base: Mapping[str, str]) -> Dict[str, str]:
        """Return a fresh environment; never read ambient os.environ here.

        The caller must supply a reviewed base environment. Remove inherited
        Python import/initialization settings, shell startup files, and stale
        Botainer child-process locations. This is not a process sandbox or a
        complete environment allowlist; runner policy remains a separate gate.
        """
        result: Dict[str, str] = {}
        for key, value in trusted_base.items():
            _text(key, "environment variable name")
            if "=" in key or not isinstance(value, str) or "\x00" in value:
                raise CommandError("invalid environment entry")
            if (key.startswith("PYTHON") or key.startswith("BOTAINER_STATE_")
                    or key in ("BASH_ENV", "ENV", "MY_BOTAINER")):
                continue
            result[key] = value
        result.update(self.env_overrides)
        return result


@dataclass(frozen=True)
class LocalCommandBuilder:
    context: ExecutionContext
    inventory_cwd: Path

    def __post_init__(self) -> None:
        if not isinstance(self.context, ExecutionContext):
            raise ContextError("builder requires an ExecutionContext")
        object.__setattr__(self, "inventory_cwd", canonical_directory(
            self.inventory_cwd, "inventory directory"))

    def _plan(self, action: str, args: Tuple[str, ...], cwd: Path,
              *, terminal: bool = False) -> CommandPlan:
        return CommandPlan(self.context, action,
                           self.context.launcher.argv_prefix + args, cwd, terminal)

    def _target(self, target: RuntimeTarget, *, existing: bool = True,
                session: bool = False, local_runtime: bool = False) -> None:
        if not isinstance(target, RuntimeTarget) or target.context != self.context:
            raise CommandError("target must match this exact execution context revision")
        if existing and target.project_id is None:
            raise CommandError("operation requires an identified project")
        if session and target.session_id is None:
            raise CommandError("operation requires an explicit full session ID")
        if not session and target.session_id is not None:
            raise CommandError("project operation must not target an existing session")
        if local_runtime and target.runtime != "docker":
            raise CommandError("Apptainer/HPC lifecycle commands require the later site adapter")

    def project_inventory(self) -> CommandPlan:
        return self._plan("project_inventory", ("list", "--json"), self.inventory_cwd)

    def session_inventory(self) -> CommandPlan:
        # Project-local status can create/chmod state; use the global JSON path.
        return self._plan("session_inventory", ("status", "--global", "--all", "--json"),
                          self.inventory_cwd)

    def init_project(self, target: RuntimeTarget, *, agent: str,
                     name: Optional[str] = None) -> CommandPlan:
        self._target(target, existing=False)
        if not isinstance(agent, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", agent):
            raise CommandError("agent must be an explicit base-agent name")
        args = ("init", "--non-interactive", f"--agent={agent}", f"--runtime={target.runtime}")
        if name is not None:
            _text(name, "project name")
            args += (f"--name={name}",)
        return self._plan("init", args, target.project_root)

    def start_session(self, target: RuntimeTarget, *,
                      confirm_capabilities: bool = False) -> CommandPlan:
        self._target(target, local_runtime=True)
        if type(confirm_capabilities) is not bool:
            raise CommandError("capability confirmation must be explicit boolean consent")
        args = ("start", "--runtime=docker", "--detach", "--json", "--no-auto-onboard")
        if confirm_capabilities:
            args += ("--yes",)
        return self._plan("start", args, target.project_root)

    def attach_session(self, target: RuntimeTarget) -> CommandPlan:
        self._target(target, session=True, local_runtime=True)
        return self._plan("attach", ("attach", "--", target.session_id),
                          target.project_root, terminal=True)

    def stop_session(self, target: RuntimeTarget) -> CommandPlan:
        self._target(target, session=True, local_runtime=True)
        return self._plan("stop", ("stop", "--", target.session_id), target.project_root)
