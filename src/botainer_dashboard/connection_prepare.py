"""Read-only preparation of explicitly selected existing Botainer installations.

The probe never imports Botainer, runs its launcher/hooks, installs dependencies,
or writes remote files. A fingerprint describes observed bytes, not trusted code
or a successful agent launch. Candidates require a separate review before use.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import time

from .remote_profile import parse_remote_profile
from . import control_paths, installation_layout, import_policy
from .ssh_diagnostics import classify_ssh_failure, connection_diagnostic

REQUEST_FIELDS = frozenset({"kind", "id", "label", "ssh_alias", "python", "source_root",
    "state_root", "launcher", "project_roots", "control_root", "docker_path", "docker_host",
    "tmux_path", "home", "read_only_acknowledged"})
MAX_OUTPUT = 1024 * 1024

# Passed as stdin to an isolated interpreter. -S prevents .pth and sitecustomize
# execution, including during discovery; static .pth paths are only read below.
PROBE_SOURCE = r'''
import base64, hashlib, json, os, pathlib, pwd, stat, sys, sysconfig
P = pathlib.Path
request = json.loads(base64.b64decode(sys.argv[1]))
result = {'ok': False, 'code': 'inspection-failed'}
count = 0
total = 0
class InspectionError(ValueError):
    def __init__(self, code, field=None, resolved_path=None):
        self.code, self.field, self.resolved_path = code, field, resolved_path
        super().__init__(code)
def require(test, code):
    if not test: raise InspectionError(code)
def directory(value, field, resolve_discovered=False):
    p = P(value)
    try:
        resolved = p.resolve(strict=True)
        if not p.is_absolute() or not resolved.is_dir():
            raise InspectionError('directory-not-directory', field)
    except (OSError, RuntimeError):
        raise InspectionError('directory-unavailable', field) from None
    if p != resolved and not resolve_discovered:
        raise InspectionError('directory-not-canonical', field, str(resolved))
    return resolved if resolve_discovered else p
def checked_path(path, field, *, allow_macos_docker_app=False):
    try:
        _import_policy.trusted_path(path, allow_macos_docker_app=allow_macos_docker_app)
    except _import_policy.ImportPolicyError as error:
        raise InspectionError(str(error), field) from None
def digest(path, limit=16*1024*1024, *, field='source_files', allow_macos_docker_app=False,
           native_owner=False):
    global count, total
    path = P(path)
    require(path.resolve(strict=True) == path, 'source-symlink')
    if native_owner:
        # tmux follows OrdinaryCliOwner._verify_tool, not Python import policy.
        # Keep its canonical executable, size and stable exact-byte checks.
        limit = min(limit, 64*1024*1024)
        selected = path.lstat()
        require(stat.S_ISREG(selected.st_mode) and os.access(path, os.X_OK), 'selected-tool-unavailable')
    else:
        checked_path(path, field, allow_macos_docker_app=allow_macos_docker_app)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if request['kind'] == 'remote':
            # Match regular() in the remote runtime before approving a profile.
            limit = min(limit, (64 if field == 'python' else 2)*1024*1024)
            if before.st_nlink != 1:
                raise InspectionError('source-hardlink', field)
        if not stat.S_ISREG(before.st_mode):
            raise InspectionError('source-file-invalid', field)
        if before.st_size > limit:
            raise InspectionError('source-file-too-large', field)
        count += 1; total += before.st_size
        require(count <= 4096 and total <= 384*1024*1024, 'source-inspection-limit')
        h = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
        after = os.fstat(stream.fileno())
        require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) ==
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns), 'source-changed-during-inspection')
        if native_owner:
            identity = lambda s: (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode)
            require(identity(selected) == identity(before) == identity(after), 'source-changed-during-inspection')
        return h.hexdigest()
def regular_text(path):
    path = P(path)
    require(path.resolve(strict=True) == path, 'discovery-file-symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'discovery-file-invalid')
        raw = stream.read(65537)
        require(len(raw) <= 65536, 'discovery-file-invalid')
        return raw.decode('utf-8')
def selected_tool(name, supplied=None):
    if supplied:
        p = P(supplied).resolve(strict=True)
        require(p.is_file() and os.access(p, os.X_OK), 'selected-tool-unavailable')
        return p
    for item in os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin').split(':'):
        if not item.startswith('/') or '..' in P(item).parts: continue
        p = P(item) / name
        if p.is_file() and os.access(p, os.X_OK): return p.resolve(strict=True)
    return None
def package_search():
    paths = [P(p) for p in sys.path if p and p.startswith('/')]
    version = 'python%d.%d' % sys.version_info[:2]
    prefix = P(sys.executable).parent.parent
    sites = []
    if (prefix / 'pyvenv.cfg').is_file():
        sites += [prefix / 'lib' / version / 'site-packages', prefix / 'lib64' / version / 'site-packages']
    else:
        sites += [P(sysconfig.get_path('purelib')), P(sysconfig.get_path('platlib'))]
    dynamic = False
    seen = set()
    for site in sites:
        if not site.is_dir(): continue
        site = site.resolve(strict=True)
        if site in seen: continue
        seen.add(site); paths.append(site)
        for pth in sorted(site.glob('*.pth')):
            for line in regular_text(pth).splitlines():
                if not line or line.startswith('#'): continue
                if line.startswith(('import ', 'import\t')):
                    dynamic = True; continue
                p = site / line
                if p.is_dir(): paths.append(p.resolve(strict=True))
    matches = []
    for path in paths:
        candidate = path / 'botainer' / '__init__.py'
        if candidate.is_file():
            root = candidate.resolve(strict=True).parent.parent
            if root not in matches: matches.append(root)
    return matches, dynamic
def collect(base, subtree, hashes, hooks, field):
    root = base / subtree
    if not root.exists(): return
    directory(str(root), field)
    for here, dirs, files in os.walk(root, followlinks=False):
        require(all(not (P(here)/name).is_symlink() for name in dirs), 'source-directory-symlink')
        dirs[:] = sorted(name for name in dirs if name not in {'__pycache__', '.git'})
        for name in sorted(files):
            p = P(here) / name
            if p.suffix == '.pyc': continue
            relative = p.relative_to(root)
            # Match the remote helper's executable/plugin filter, plus package
            # resources and installed.lock, without following file symlinks.
            mode = p.lstat().st_mode
            keep = (subtree != 'plugins' or mode & 0o111 or name == 'installed.lock'
                or set(relative.parts[:-1]) & {'hooks','host_helper','commands'}
                or p.suffix in {'.py','.sh','.yaml','.yml','.json','.js','.mjs','.cjs','.rb','.pl','.so'})
            if keep:
                h = digest(p, field=field); hashes[str(p.relative_to(base))] = h
                if 'hooks' in relative.parts[:-1]: hooks[str(p)] = h
try:
    require(sys.version_info >= (3,11), 'python-too-old')
    # Account databases commonly expose a symlink/automounter home on clusters.
    # It is discovered metadata, not a user-selected pinned runtime root. The
    # remote helper itself uses passwd HOME without a canonical-path gate.
    # Resolve only that discovered default; explicit paths retain strict checks.
    home_value = request.get('home')
    home = directory(home_value or pwd.getpwuid(os.getuid()).pw_dir,
                     'home' if home_value else 'account_home', resolve_discovered=not bool(home_value))
    matches, dynamic = ([], False) if request['kind'] == 'local' and request.get('source_root') else package_search()
    source_value = request.get('source_root') or (str(matches[0]) if len(matches) == 1 else None)
    require(source_value, 'source-not-discovered')
    source = directory(source_value, 'source_root')
    state = directory(request.get('state_root') or str(home / '.botainer'), 'state_root')
    layout = _installation_layout.discover_layout(source)
    if layout:
        matches, dynamic = package_search()
        require(matches == [source] and not dynamic, 'remote-import-selection-unverified')
    required = ['botainer/__init__.py','botainer/plugins/lifecycle.py',
        'botainer/plugins/manifest.py','botainer/cli/main.py','botainer/cli/plugin.py']
    required += list(_installation_layout.metadata_paths(layout)) if layout else ['pyproject.toml']
    if request['kind'] == 'remote':
        required += ['botainer/cli/hpc.py']
        if not layout:
            required += ['plugins/hpc-launcher/host_helper/submit.py','plugins/hpc-launcher/host_helper/_common.py']
        else:
            require(all((state / p).is_file() for p in ('plugins/hpc-launcher/botainer-plugin.yaml',
                'plugins/hpc-launcher/host_helper/submit.py','plugins/hpc-launcher/host_helper/_common.py')),
                'installed-hpc-plugin-unavailable')
    require(all((source/p).is_file() for p in required), 'source-layout-unsupported')
    try:
        _import_policy.source_modules(source)
    except _import_policy.ImportPolicyError as error:
        raise InspectionError(str(error), 'source_files') from None
    source_hashes = {name: digest(source / name) for name in
        (_installation_layout.metadata_paths(layout) if layout else ('pyproject.toml',))}
    state_hashes, hooks = {}, {}
    collect(source, 'botainer', source_hashes, hooks, 'source_files')
    if not layout:
        collect(source, 'plugins', source_hashes, hooks, 'source_files')
        collect(source, 'templates', source_hashes, hooks, 'source_files')
    collect(state, 'plugins', state_hashes, hooks, 'state_plugins')
    require(len(source_hashes) <= 2048 and len(state_hashes) <= 2048, 'source-inspection-limit')
    roots = [str(directory(p, 'project_roots.' + str(i))) for i, p in enumerate(request.get('project_roots', []))]
    require(roots and len(roots) == len(set(roots)), 'project-roots-required')
    python_target = P(sys.executable).resolve(strict=True)
    python_hash = digest(python_target, 256*1024*1024, field='python')
    control = request.get('control_root')
    require(control, 'control-root-required')
    control_path = P(control)
    protected = [source, state, *map(P, roots)]
    require(all(control_path != p and control_path not in p.parents
                for p in protected), 'control-root-overlap')
    if request['kind'] == 'local':
        try:
            _control_paths.validate_local_control_root(control)
        except _control_paths.ControlRootError as error:
            raise InspectionError(error.code, 'control_root', error.resolved_path) from None
    elif control_path.exists():
        directory(control, 'control_root')
        st = control_path.stat()
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise InspectionError('control-root-not-private', 'control_root')
    else:
        raise InspectionError('control-root-missing', 'control_root')
    result = {'ok': True, 'source_root':str(source), 'state_root':str(state), 'home':str(home),
        'python':sys.executable, 'python_target':str(python_target), 'python_sha256':python_hash,
        'source_hashes':source_hashes, 'state_hashes':state_hashes, 'hook_candidates':hooks,
        'project_roots':roots, 'control_root':control, 'control_exists':control_path.exists(),
        'import_source':str(matches[0]) if matches else None, 'dynamic_import_setup':dynamic,
        'directory_identities':{str(p):[p.stat().st_dev,p.stat().st_ino] for p in [home,source,state,*map(P,roots)]}}
    if layout: result['installation_layout'] = layout
    if request['kind'] == 'remote':
        launcher = selected_tool('botainer', request.get('launcher'))
        require(launcher, 'launcher-not-discovered')
        result['launcher'] = {'path':str(launcher), 'sha256':digest(launcher, field='launcher')}
        require(matches and matches[0] == source and not dynamic, 'remote-import-selection-unverified')
    else:
        docker = selected_tool('docker', request.get('docker_path'))
        tmux = selected_tool('tmux', request.get('tmux_path'))
        require(docker and tmux, 'local-tools-unavailable')
        result.update(docker_path=str(docker), docker_sha256=digest(docker,256*1024*1024,
                          field='docker_path', allow_macos_docker_app=True),
                      tmux_path=str(tmux), tmux_sha256=digest(tmux, field='tmux_path',
                                                            limit=64*1024*1024, native_owner=True))
except InspectionError as error:
    result = {'ok':False, 'code':error.code}
    if error.field: result['field'] = error.field
    if error.resolved_path: result['resolved_path'] = error.resolved_path
except _installation_layout.InstallationLayoutError:
    result = {'ok':False, 'code':'installation-layout-unverified'}
except (OSError, ValueError, RuntimeError, UnicodeError) as error:
    # Do not echo paths, file content, credential text, or arbitrary exception
    # descriptions into public diagnostics.
    code = str(error) if type(error) is ValueError else 'inspection-path-unavailable'
    result = {'ok':False, 'code':code}
print(json.dumps(result, sort_keys=True))
'''

# The same reviewed parser runs locally and in the fixed isolated probe. No
# package from the inspected installation is imported, including its metadata.
PROBE_SOURCE = ("import types\n_installation_layout = types.ModuleType('dashboard_installation_layout')\n"
    + "exec(compile(" + repr(Path(installation_layout.__file__).read_text())
    + ", '<dashboard-installation-layout>', 'exec'), _installation_layout.__dict__)\n"
    + "_control_paths = types.ModuleType('dashboard_control_paths')\n"
    + "exec(compile(" + repr(Path(control_paths.__file__).read_text())
    + ", '<dashboard-control-paths>', 'exec'), _control_paths.__dict__)\n"
    + "_import_policy = types.ModuleType('dashboard_import_policy')\n"
    + "exec(compile(" + repr(Path(import_policy.__file__).read_text())
    + ", '<dashboard-import-policy>', 'exec'), _import_policy.__dict__)\n" + PROBE_SOURCE)


def _path(value):
    if (not isinstance(value, str) or len(value) > 4096 or not value.startswith("/")
            or value in {"/", "//"} or value.startswith("//")
            or str(PurePosixPath(value)) != value or ".." in PurePosixPath(value).parts
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Use a normalized absolute path, without ~ or shell variables.")
    return value


def _run(argv, *, input, timeout):
    """Fixed argv runner with bounded stdin, both output pipes, and lifetime."""
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, shell=False, close_fds=True, start_new_session=True)
    selector = selectors.DefaultSelector()
    output, error = bytearray(), bytearray()
    pending = memoryview(input)
    try:
        for stream, name in ((process.stdin, "in"), (process.stdout, "out"), (process.stderr, "err")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_WRITE if name == "in" else selectors.EVENT_READ, name)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            if time.monotonic() >= deadline:
                return {"returncode": None, "stdout": b"", "stderr": b"", "transport_status": "timeout"}
            for key, _events in selector.select(.1):
                if key.data == "in":
                    try:
                        if pending: pending = pending[os.write(key.fileobj.fileno(), pending[:65536]):]
                    except BrokenPipeError:
                        pending = memoryview(b"")
                    if not pending:
                        selector.unregister(key.fileobj); key.fileobj.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj); continue
                target = output if key.data == "out" else error
                if len(target) + len(chunk) > (MAX_OUTPUT if key.data == "out" else 65536):
                    raise ValueError("Inspection output exceeded its bound.")
                target.extend(chunk)
        code = process.wait(timeout=max(.01, deadline - time.monotonic()))
        return {"returncode": code, "stdout": bytes(output), "stderr": bytes(error), "transport_status": "complete"}
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr): stream.close()
        if process.poll() is None:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=2)


_HINTS = {
    "source-not-discovered": "Select the Python interpreter belonging to Botainer. For a wheel or pipx installation it must resolve the installed botainer package; for a source installation you can specify the checkout containing pyproject.toml and botainer/. Shell aliases are not executable paths.",
    "source-layout-unsupported": "The selected Botainer source or installed package is missing required native CLI files. Choose the correct existing installation; do not guess source pins.",
    "installation-layout-unverified": "Botainer installation metadata is missing, ambiguous, changed, or unsupported. Select one normal Botainer installation with its own Python, or an existing source checkout.",
    "installed-hpc-plugin-unavailable": "This installed Botainer has no complete HPC launcher in its state directory. Use Botainer's own setup/plugin installation procedure, then inspect again. Bundled plugin resources are not a substitute for the native installed plugin.",
    "directory-not-canonical": "Select existing folders using their real absolute paths, without symlinks.",
    "directory-unavailable": "This folder is missing or inaccessible on the selected machine. Check its spelling, parent folders and account permissions.",
    "directory-not-directory": "This path is not an existing directory. Select a folder on the chosen machine.",
    "inspection-path-unavailable": "A selected file or folder is missing, inaccessible, or not a supported regular file. Check absolute paths on the selected machine.",
    "project-roots-required": "Choose at least one existing parent folder containing projects that this connection may control.",
    "control-root-required": "Choose a private dashboard control directory outside Botainer source, state, and project folders.",
    "control-root-overlap": "The dashboard control directory must be separate from Botainer source, state, and permitted project folders.",
    "control-parent-unavailable": "Create the parent of the private control directory, or select an existing private location. Preparation does not create folders.",
    "control-root-missing": "The selected private control directory does not exist. Create it with mode 700 for the login account, then inspect again. Preparation does not create folders.",
    "control-root-not-private": "The control directory must belong to the login account and have mode 700.",
    "local-tools-unavailable": "Select existing Docker and tmux executables. Install missing tools only after reviewing and approving their installation separately.",
    "launcher-not-discovered": "Enter the absolute Botainer launcher path on the remote machine. A shell alias or function cannot be selected as an executable.",
    "remote-import-selection-unverified": "The selected isolated Python could not be linked to this exact Botainer source without executing installation hooks. Choose the installation's interpreter, or use a separately reviewed existing runtime profile. No import hooks were executed.",
    "python-too-old": "Select an existing Python 3.11 or later interpreter belonging to this Botainer installation.",
    "source-symlink": "A required source file is a symlink. Select a canonical source checkout or obtain a separately reviewed profile.",
    "source-directory-symlink": "The selected source/plugin tree contains a directory symlink. Preparation will not follow it.",
    "source-hardlink": "A selected remote installation file has multiple hard links. This layout is not supported by the runtime verification. Select a separately installed copy; no files or links were changed.",
    "source-file-invalid": "A selected installation input is not a supported regular file. No profile was approved.",
    "source-file-too-large": "A selected installation file exceeds the supported read limit. Remote source, plugin and launcher files are limited to 2 MiB, and the remote Python binary to 64 MiB. Choose a supported installation; no files were changed.",
    "source-inspection-limit": "The selected source exceeds the bounded preparation size. Inspect it manually rather than broadening the scan.",
    "import-path-other-account-writable": "This installation has executable files or parent folders that another account can change. Select an installation protected from other accounts, or ask its administrator to review the permissions. The dashboard has not changed permissions or approved the installation.",
    "import-path-owner-untrusted": "A selected executable or parent folder belongs to another account. Select an installation owned by the login account or the system administrator. No ownership was changed.",
    "import-path-not-canonical": "A selected executable path changed or uses an unsupported link. Inspect the installation again using its real path.",
    "import-package-invalid": "The selected Botainer package is missing or uses an unsupported directory link. Choose the existing installation's real package folder.",
    "import-package-directory-symlink": "The selected Botainer package contains a directory symlink that the runtime cannot safely verify. Choose a supported installation layout; no links were changed.",
    "import-package-unreviewed-executable": "The Botainer package contains standalone bytecode or extension files outside the supported source layout. Select a supported installation. Existing cache folders are left untouched.",
    "import-package-inspection-limit": "The Botainer package exceeds the bounded runtime inspection limit. This installation needs review before use.",
}

_FIELD_LABELS = {"home": "Local home folder", "account_home": "Login account home",
    "source_root": "Botainer import root", "state_root": "Botainer state folder",
    "control_root": "Private dashboard control folder", "source_files": "Botainer source file tree",
    "state_plugins": "Installed plugin file tree", "python": "Botainer Python interpreter",
    "launcher": "Botainer launcher", "docker_path": "Docker executable", "tmux_path": "Terminal owner executable"}


def _inspection_field(value):
    """Only fixed field names may become diagnostic labels from a remote."""
    if isinstance(value, str) and value in _FIELD_LABELS:
        return _FIELD_LABELS[value] + " (" + value + ")"
    if isinstance(value, str) and re.fullmatch(r"project_roots\.(?:[0-9]|[12][0-9]|3[01])", value):
        index = int(value.split(".")[1])
        return "Project folder " + str(index + 1) + " (project_roots[" + str(index) + "])"
    return "Installation prerequisites"


def _failed(kind, name, message, instructions=()):
    return {"kind": kind, "profile": None, "checks": [{"name": name, "status": "failed", "message": message}],
        "instructions": list(instructions), "qualification": "failed", "requires_review": True}


def _python_from_launcher(launcher):
    """Read only a small regular script header; do not resolve aliases or eval."""
    try:
        path = Path(launcher).resolve(strict=True)
        if not path.is_file(): return None
        with path.open("rb") as stream: line = stream.readline(4097)
        if len(line) > 4096 or not line.startswith(b"#!/"): return None
        candidate = line[2:].decode("utf-8").strip()
        if any(c.isspace() for c in candidate) or "python" not in Path(candidate).name: return None
        _path(candidate)
        return candidate if os.access(candidate, os.X_OK) else None
    except (OSError, UnicodeError, ValueError): return None


def prepare_connection(request, *, runner=None, expected_local_tools=None):
    """Return an unactivated candidate, checks, and manual recovery instructions.

    The caller must present the selected-machine inspection and obtain its
    explicit acknowledgement. A runner can be injected for offline tests; it
    receives immutable argv and keyword input/timeout and returns byte pipes.
    An update caller supplies expected_local_tools from the saved profile, never
    from browser input, to reject changed tools before invoking Docker.
    """
    kind = request.get("kind") if isinstance(request, dict) else None
    try:
        if not isinstance(request, dict) or set(request) - REQUEST_FIELDS:
            raise ValueError("Unsupported connection preparation fields.")
        if kind not in {"local", "remote"}: raise ValueError("Choose this computer or an SSH connection.")
        if expected_local_tools is not None and (kind != "local"
                or not isinstance(expected_local_tools, dict)
                or set(expected_local_tools) != {"docker_path", "docker_sha256", "tmux_path", "tmux_sha256"}
                or any(not isinstance(v, str) or not v for v in expected_local_tools.values())):
            raise ValueError("The saved container and terminal tool selection needs review through connection setup.")
        if request.get("read_only_acknowledged") is not True:
            raise ValueError("Review and acknowledge the read-only inspection before testing the selected connection.")
        ident, label = request.get("id"), request.get("label")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", ident):
            raise ValueError("Use a connection ID containing 1–48 letters, digits, underscores or hyphens.")
        if (kind == "local") != (ident == "local"):
            raise ValueError("The single local container connection must use ID local; remote connections must use a different ID.")
        if (not isinstance(label, str) or not 1 <= len(label) <= 160
                or any(ord(c) < 32 or ord(c) == 127 for c in label)):
            raise ValueError("Provide a short connection name.")
        values = {k: v for k, v in request.items() if v is not None and v != ""}
        for key in ("python", "source_root", "state_root", "launcher", "control_root", "docker_path", "tmux_path", "home"):
            if key in values: _path(values[key])
        roots = values.get("project_roots", [])
        if not isinstance(roots, list) or not 1 <= len(roots) <= 32 or len(set(map(str, roots))) != len(roots):
            raise ValueError("Choose 1–32 distinct existing parent project folders.")
        for root in roots: _path(root)
        if kind == "remote":
            alias = values.get("ssh_alias")
            if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", alias):
                raise ValueError("Enter a simple existing SSH alias; configure its host, user, key and port in SSH first.")
            if any(key in values for key in ("home", "docker_path", "docker_host", "tmux_path")):
                raise ValueError("Docker, tmux and local home fields apply only to this computer.")
            python = values.get("python", "/usr/bin/python3")
        else:
            if "ssh_alias" in values: raise ValueError("An SSH alias applies only to a remote connection.")
            launcher = values.get("launcher") or shutil.which("botainer")
            python = values.get("python") or (_python_from_launcher(launcher) if launcher else None) or sys.executable
            _path(python)
            host = values.get("docker_host") or os.environ.get("DOCKER_HOST")
            if not host: host = "unix:///var/run/docker.sock"
            if not isinstance(host, str) or not host.startswith("unix:///"):
                raise ValueError("Select a local unix:/// Docker socket; network Docker endpoints are not supported.")
            _path(host[len("unix://"):])
            values["docker_host"] = host
        values["python"] = python
        payload = base64.b64encode(json.dumps(values, ensure_ascii=True).encode()).decode("ascii")
        argv = (python, "-I", "-S", "-B", "-", payload)
        if kind == "remote":
            ssh = shutil.which("ssh")
            if not ssh: raise ValueError("The system SSH client is unavailable; install it only with separate approval.")
            command = " ".join(shlex.quote(part) for part in argv)
            argv = (ssh, "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
                "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "ClearAllForwardings=yes",
                "-o", "PermitLocalCommand=no", "-o", "UpdateHostKeys=no", "--", alias, command)
        run = runner or _run
        execution = run(tuple(argv), input=PROBE_SOURCE.encode(), timeout=30)
        if execution.get("returncode") != 0:
            if kind == "remote":
                diagnostic = connection_diagnostic(classify_ssh_failure(
                    {"transport_status": execution.get("transport_status", "complete"), "returncode": execution.get("returncode")},
                    execution.get("stderr", b"")))
                return _failed(kind, "SSH inspection", diagnostic["message"], [diagnostic["recovery"]])
            return _failed(kind, "Installation inspection", "The selected interpreter could not complete the bounded inspection.",
                ["Check the interpreter path and selected folders. No Botainer command, hook or installation was run."])
        raw = execution.get("stdout")
        if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT: raise ValueError("Invalid or oversized inspection response.")
        observed = json.loads(raw)
        if not isinstance(observed, dict) or observed.get("ok") is not True:
            code = observed.get("code") if isinstance(observed, dict) else None
            instructions = []
            if code == "control-root-missing" and values.get("control_root"):
                instructions = ["In a terminal on the selected machine, create this dedicated directory: mkdir -m 700 -- " + shlex.quote(values["control_root"]),
                    "If its parent is missing, create that private parent first. Do not change permissions on an existing shared directory."]
            if code in {"directory-not-canonical", "control-root-not-canonical"} and observed.get("resolved_path"):
                try:
                    resolved = _path(observed["resolved_path"])
                    instructions.append("The inspected folder resolves to " + resolved + ". Review and enter that real path; no selection was changed automatically.")
                except ValueError:
                    pass
            hints = {**_HINTS, **control_paths.LOCAL_CONTROL_ROOT_MESSAGES} if kind == "local" else _HINTS
            return _failed(kind, _inspection_field(observed.get("field") if isinstance(observed, dict) else None),
                hints.get(code, "The read-only installation inspection failed."), instructions)
        checks = [{"name": "Installation files", "status": "passed", "message": "Selected source, plugin files and interpreter were read and fingerprinted; no Botainer code was executed."}]
        instructions = ["Review the selected installation and permitted project folders before saving. Fingerprints detect later changes; they do not certify that software is safe.",
            "A saved candidate remains untested until native preflight, launch, terminal reconnect and stop are exercised on this machine."]
        if kind == "remote":
            profile = {"version": 2 if observed.get("installation_layout") else 1, "id": ident, "label": label, "ssh_alias": alias,
                "remote_python": python, "remote_python_sha256": observed["python_sha256"],
                "source_root": observed["source_root"], "state_root": observed["state_root"],
                "launcher": observed["launcher"], "source_sha256": observed["source_hashes"],
                "state_plugin_sha256": observed["state_hashes"], "control_root": observed["control_root"],
                "project_roots": {"projects-" + str(i+1): path for i, path in enumerate(observed["project_roots"])}}
            if observed.get("installation_layout"): profile["installation_layout"] = observed["installation_layout"]
            parse_remote_profile(profile)
            checks.append({"name": "Cluster operation", "status": "untested", "message": "Scheduler, compute-node access, Screen persistence and real agents have not been tested by this inspection."})
            instructions += ["Follow the cluster's SSH/MFA and scheduler guidance. This inspection neither submits a job nor enables the nudge persistence plugin.",
                "Project persistence and attach prerequisites must be verified before relying on reconnect; preparation does not modify Screen settings."]
        else:
            if expected_local_tools is not None and any(
                    observed.get(key) != expected for key, expected in expected_local_tools.items()):
                return {**_failed(kind, "Saved tool selection",
                    "A container or terminal management tool changed. Review it through connection setup; the Docker executable was not run."),
                    "failure_code": "installation-tools-changed"}
            daemon = run((observed["docker_path"], "--host", values["docker_host"], "info", "--format", "{{json .ID}}"), input=b"", timeout=12)
            if daemon.get("returncode") != 0:
                return _failed(kind, "Docker connection", "The selected Docker socket did not return a daemon identity.",
                    ["Start the existing Docker runtime or choose its correct local socket, then retry. No container was started."])
            daemon_id = json.loads(daemon.get("stdout", b""))
            if not isinstance(daemon_id, str) or not 1 <= len(daemon_id) <= 256 or any(ord(c) < 32 for c in daemon_id):
                raise ValueError("Docker returned an invalid daemon identity.")
            paths = [str(Path(observed["docker_path"]).parent), str(Path(python).parent), str(Path(observed["tmux_path"]).parent), "/usr/local/bin", "/usr/bin", "/bin"]
            paths = list(dict.fromkeys(paths))
            profile = {"version": 2 if observed.get("installation_layout") else 1, "id": ident, "label": label, "python": python,
                "source_root": observed["source_root"], "state_root": observed["state_root"], "home": observed["home"],
                "project_roots": [{"id": "projects-" + str(i+1), "label": Path(path).name, "path": path} for i, path in enumerate(observed["project_roots"])],
                "docker": {"executable": observed["docker_path"], "host": values["docker_host"], "daemon_id": daemon_id},
                "environment": {"PATH": ":".join(paths), "LANG": "en_US.UTF-8"},
                "source_hashes": observed["source_hashes"],
                "support_hashes": {observed["python_target"]: observed["python_sha256"], observed["docker_path"]: observed["docker_sha256"],
                    **{str(Path(observed["state_root"]) / k): v for k, v in observed["state_hashes"].items()}},
                "approved_hooks": {}, "directory_identities": observed["directory_identities"],
                "terminal_owner": {"path": observed["tmux_path"], "sha256": observed["tmux_sha256"], "control_root": observed["control_root"]}}
            if observed.get("installation_layout"): profile["installation_layout"] = observed["installation_layout"]
            checks.append({"name": "Docker connection", "status": "passed", "message": "Read the selected daemon identity without starting a container."})
            if observed.get("hook_candidates"):
                checks.append({"name": "Startup hooks", "status": "review_required", "message": "Installed hook files were fingerprinted but are not approved for execution. An explicit reviewed hook selection is still required for projects that use them."})
                instructions.append("Review the exact hook paths and fingerprints below. If you trust those host-side programs, select the separate hook approval before saving. Hashing alone does not approve a hook; leaving it unchecked prevents projects that need those hooks from launching.")
        return {"kind": kind, "profile": profile, "checks": checks, "instructions": instructions,
            "qualification": "untested", "requires_review": True, "hook_candidates": observed.get("hook_candidates", {}),
            "control_directory_required": False}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # Only our validation text is suitable for the UI. OS/process errors can
        # contain local paths or remote banners; avoid interpolating them.
        message = str(error) if type(error) is ValueError else "Connection preparation could not complete; check the selected paths and installed tools."
        return _failed(kind, "Connection details", message)
