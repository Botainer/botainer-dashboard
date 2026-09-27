"""Keep container project data separate from trusted local application/control paths.

These are host-selected paths, never a browser-supplied allowlist. A broad new
project parent may contain both kinds of directory; only the actual target is
checked. Native Botainer mount policy and consent still apply independently.
"""
from pathlib import Path
import sys

from .backend_errors import BackendUnavailable


def protected_local_paths(profile, application_root, *, data_directory=None, extra=()):
    d = profile.data
    application = Path(application_root)
    paths = [application, Path(profile.path), Path(d["source_root"]), Path(d["state_root"])]
    executables = [Path(d["python"]), Path(d["python"]).resolve()]
    if application.name == "_resources" and application.parent.name == "botainer_dashboard":
        paths.append(application.parent)
        # In the service this is the dashboard environment. In a fixed helper
        # it is the selected Botainer environment; the service passes its own
        # additional protected paths in the private request as well.
        paths.append(Path(sys.prefix))
        # An installed venv may use a base interpreter/stdlib outside its own
        # environment. Framework Python layouts also need the actual base
        # prefix, rather than guessing the stdlib from the executable's parent.
        paths.extend(Path(sys.base_prefix) / name for name in ("bin", "lib", "lib64"))
        executables.extend((Path(sys.executable), Path(sys.executable).resolve()))
    if data_directory is not None:
        paths.append(Path(data_directory))
    if "terminal_owner" in d:
        paths.extend((Path(d["terminal_owner"]["control_root"]), Path(d["terminal_owner"]["path"])))
    # Native policy blocks conventional Docker sockets; also cover the exact
    # selected custom Unix endpoint, wherever the operator keeps it.
    paths.append(Path(d["docker"]["host"][len("unix://"):]))
    # Include both a venv's entry path and its resolved base interpreter. Conda
    # and ordinary system Python need protection too; pyvenv.cfg is not a
    # prerequisite. Protect code directories without treating all of /usr (or
    # an arbitrary broad installation parent) as application data.
    for executable in executables:
        prefix = executable.parent.parent
        paths.extend((executable, executable.parent, prefix / "lib", prefix / "lib64"))
    paths.extend(Path(path) for path in extra)
    paths.extend(Path(path) for path in d.get("support_hashes", {}))
    paths.extend(Path(path) for path in d.get("approved_hooks", {}))
    if any(not p.is_absolute() or ".." in p.parts for p in paths):
        raise BackendUnavailable("ordinary-protected-paths-invalid")
    # Most plugin pins already sit inside protected source/state directories.
    # Keep only outermost boundaries to bound owner-private operation requests.
    selected = set()
    for path in sorted(set(paths), key=lambda p: (len(p.parts), str(p))):
        if not any(parent in selected for parent in path.parents):
            selected.add(path)
    if len(selected) > 256:
        raise BackendUnavailable("ordinary-protected-paths-invalid")
    return tuple(sorted(selected, key=str))


def require_separate_project(path, protected):
    """Reject equal/ancestor/descendant targets, including changed path aliases.

    Callers also enforce no-follow traversal and project identity. Resolving here
    catches a protected directory replaced by an alias, not permission to follow
    a project symlink. Nonexistent creation targets are checked before mkdir.
    """
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise BackendUnavailable("ordinary-project-path-invalid")
    targets = (path, path.resolve())
    for item in protected:
        selected = Path(item)
        for boundary in (selected, selected.resolve()):
            if any(target == boundary or target in boundary.parents or boundary in target.parents
                   for target in targets):
                raise BackendUnavailable("ordinary-project-overlaps-protected-paths")
    return path
