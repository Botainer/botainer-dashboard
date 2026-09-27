#!/usr/bin/env python3
"""Execute an approved native hook with isolated broker child imports.

The native hook runner still owns hook containment, its scrubbed environment,
timeout and contribution parsing. This fixed bootstrap isolates child imports
without changing an installed hook: the two reviewed broker start hooks may
spawn only their expected daemon, through this same isolated trusted bootstrap.
"""
from contextlib import contextmanager
from pathlib import Path
import runpy
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
if REPO.name == "_resources" and REPO.parent.name == "botainer_dashboard":
    # Preserve the selected Botainer interpreter's dependencies. Only this
    # package is imported from the dashboard environment, never its complete
    # site-packages (which can contain incompatible native Python wheels).
    import importlib.util
    _package = REPO.parent
    _spec = importlib.util.spec_from_file_location("botainer_dashboard", _package / "__init__.py",
                                                   submodule_search_locations=[str(_package)])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["botainer_dashboard"] = _module
    _spec.loader.exec_module(_module)
else:
    sys.path.insert(0, str(REPO / "src"))

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.ordinary_local import OrdinaryLocalProfile
from botainer_dashboard.import_policy import source_imports


def broker_hook(profile, hook):
    """Recognize exact approved first-party paths, not just a file basename."""
    return any(hook == Path(root) / "plugins" / plugin / "hooks/start_broker.py"
               for root in (profile.data["source_root"], profile.data["state_root"])
               for plugin in ("agent-claude-broker", "agent-codex-broker"))


def selected_source(profile):
    source = Path(profile.data["source_root"])
    sys.path.insert(0, str(source))
    import botainer
    if Path(botainer.__file__).resolve() != source / "botainer/__init__.py":
        raise BackendUnavailable("ordinary-hook-botainer-source-changed")


def broker_command(profile, hook):
    # Re-enter our fixed file in isolated mode. The child rechecks profile,
    # hook and source identity before importing the real daemon. This does not
    # rely on user-site, PYTHONPATH or an editable-install .pth selection.
    return [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
            "--broker-child", str(profile.path), str(hook)]


@contextmanager
def isolated_broker_spawn(profile, hook):
    original = subprocess.Popen
    guarded = broker_hook(profile, hook)

    def spawn(args, *positional, **kwargs):
        # The alpha5 candidate adds isolated startup to the native daemon.
        # Accept only the two reviewed exact shapes; both still re-enter our
        # fixed child, which rechecks the selected source and hook identity.
        commands = ([sys.executable, "-m", "botainer.broker.daemon_main"],
                    [sys.executable, "-I", "-B", "-m", "botainer.broker.daemon_main"])
        if (not isinstance(args, (list, tuple))
                or list(args) not in commands
                or kwargs.get("shell") or kwargs.get("executable") is not None):
            raise BackendUnavailable("ordinary-broker-child-command-changed")
        return original(broker_command(profile, hook), *positional, **kwargs)

    if guarded:
        subprocess.Popen = spawn
    try:
        yield
    finally:
        if guarded:
            subprocess.Popen = original


def main(arguments=None):
    args = list(sys.argv[1:] if arguments is None else arguments)
    child = len(args) == 3 and args[0] == "--broker-child"
    if child:
        args = args[1:]
    if len(args) != 2:
        raise BackendUnavailable("ordinary-hook-bootstrap-arguments-invalid")
    profile = OrdinaryLocalProfile(Path(args[0]))
    profile.verify()
    hook = Path(args[1])
    profile.verify_hook(hook)
    selected_source(profile)
    if child and not broker_hook(profile, hook):
        raise BackendUnavailable("ordinary-broker-child-hook-invalid")
    previous = sys.argv
    try:
        if child:
            sys.argv = ["botainer.broker.daemon_main"]
            runpy.run_module("botainer.broker.daemon_main", run_name="__main__")
        else:
            sys.argv = [str(hook)]
            with isolated_broker_spawn(profile, hook):
                runpy.run_path(str(hook), run_name="__main__")
    finally:
        sys.argv = previous


if __name__ == "__main__":
    try:
        with source_imports():
            main()
    except Exception as error:
        print("Dashboard hook refused: " + str(error), file=sys.stderr)
        raise SystemExit(2)
