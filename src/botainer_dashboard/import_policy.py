"""Selected-package import hardening for short-lived native helper processes.

Source pins are not an attestation of Python, dependencies, startup customization,
or same-account writers. This policy avoids consuming installation bytecode for
imports performed inside its scope and refuses extra executable package files.
It does not remove or modify an installed cache, replace import loaders, or
alter native Botainer source. Each helper/daemon child enters its own scope.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory


class ImportPolicyError(ValueError):
    pass


def trusted_path(path, *, allow_macos_docker_app=False):
    """Reject other-account writable executable paths and their ancestors.

    A root/account-owned sticky temporary parent is allowed: its sticky bit
    prevents another account from replacing this account's private child.
    A caller checking the selected native Docker CLI may opt into the standard
    macOS /Applications admin-group parent. That exception trusts platform
    administrators, applies only to the exact Docker.app CLI, and leaves its
    executable and all inner directories strict. Python inputs must not opt in.
    Same-account mutation and ACLs remain outside this mode-bit check.
    """
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ImportPolicyError("import-path-not-canonical")
    for candidate in (path, *path.parents):
        info = candidate.stat()
        if info.st_uid not in {0, os.getuid()}:
            raise ImportPolicyError("import-path-owner-untrusted")
        sticky_parent = (candidate != path and stat.S_ISDIR(info.st_mode)
                         and info.st_mode & stat.S_ISVTX)
        applications_parent = (
            allow_macos_docker_app and sys.platform == "darwin"
            and path == Path("/Applications/Docker.app/Contents/Resources/bin/docker")
            and candidate == Path("/Applications") and info.st_uid == 0
            and stat.S_ISDIR(info.st_mode) and info.st_mode & 0o022 == 0o020)
        if applications_parent:
            import grp
            try:
                applications_parent = info.st_gid == grp.getgrnam("admin").gr_gid
            except KeyError:
                applications_parent = False
        if info.st_mode & 0o022 and not sticky_parent and not applications_parent:
            raise ImportPolicyError("import-path-other-account-writable")


def source_modules(source):
    """Return .py modules after checking the selected Botainer package tree.

    Existing __pycache__ directories remain untouched; source_imports redirects
    cache lookup elsewhere. Legacy sourceless bytecode and extension modules
    can shadow reviewed .py files, so they are unsupported in this package.
    Installed dependency extension modules are not traversed or qualified here.
    """
    source = Path(source)
    trusted_path(source)
    package = source / "botainer"
    if not package.is_dir() or package.is_symlink():
        raise ImportPolicyError("import-package-invalid")
    modules = set()
    count = 0
    for here, directories, files in os.walk(package, followlinks=False):
        trusted_path(Path(here))
        directories[:] = [name for name in directories if name != "__pycache__"]
        if any((Path(here) / name).is_symlink() for name in directories):
            raise ImportPolicyError("import-package-directory-symlink")
        for name in files:
            count += 1
            if count > 8192:
                raise ImportPolicyError("import-package-inspection-limit")
            path = Path(here) / name
            if path.suffix.lower() in {".pyc", ".pyo", ".so", ".pyd"}:
                raise ImportPolicyError("import-package-unreviewed-executable")
            if name.endswith(".py"):
                trusted_path(path)
                modules.add(str(path.relative_to(source)))
    return modules


def cache_arguments():
    """Pass the active empty namespace to a synchronous known Python child.

    Children using -I ignore PYTHONPYCACHEPREFIX. The parent must retain the
    source_imports scope until the child ends; detached fixed helpers instead
    enter their own independent scope. This does not wrap arbitrary plugins.
    """
    if not isinstance(sys.pycache_prefix, str) or not sys.dont_write_bytecode:
        raise ImportPolicyError("import-cache-scope-required")
    path = Path(sys.pycache_prefix)
    trusted_path(path)
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or next(path.iterdir(), None) is not None):
        raise ImportPolicyError("import-cache-scope-not-private-empty")
    return ("-X", "pycache_prefix=" + str(path))


def remove_empty_cache_before_exec():
    """Remove only this helper's validated empty namespace before process exec.

    exec bypasses context-manager cleanup. The supported allocation attach
    replaces this process with srun, and its compute helper creates a new scope.
    Keep the now-missing prefix selected until exit: if exec fails, subsequent
    imports must not fall back to the installation's caches. Never recurse here.
    """
    cache_arguments()
    os.rmdir(sys.pycache_prefix)


@contextmanager
def source_imports():
    """Use a new empty private cache namespace without writing bytecode.

    -B alone still reads existing bytecode. An empty pycache_prefix redirects
    normal source-backed cache reads too. This changes only the current helper
    process; it must start before importing Botainer and be repeated in children.
    Modules already loaded (including interpreter startup) remain trusted.
    """
    previous_prefix, previous_write = sys.pycache_prefix, sys.dont_write_bytecode
    with TemporaryDirectory(prefix="dashboard-import-") as folder:
        sys.pycache_prefix = str(Path(folder).resolve(strict=True))
        sys.dont_write_bytecode = True
        try:
            yield
        finally:
            sys.pycache_prefix, sys.dont_write_bytecode = previous_prefix, previous_write
