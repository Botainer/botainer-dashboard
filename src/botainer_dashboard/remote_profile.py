"""Explicit installed remote Botainer registration; loading never contacts SSH."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .pairing import read_private_json
from .installation_layout import InstallationLayoutError, metadata_paths, parse_layout, validate_source_pins


class RemoteProfileError(ValueError):
    pass


def remote_path(value):
    if (not isinstance(value, str) or len(value) > 4096 or not value.startswith('/')
            or value.startswith('//') or value == '/' or str(PurePosixPath(value)) != value
            or '..' in PurePosixPath(value).parts
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise RemoteProfileError('Expected a normalized absolute remote path')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value):
        raise RemoteProfileError('Expected a simple remote identifier')
    return value


def sha256(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise RemoteProfileError('Expected a SHA-256 digest')
    return value


@dataclass(frozen=True)
class RemoteProfile:
    data: dict
    fingerprint: str

    @property
    def id(self): return self.data['id']
    @property
    def label(self): return self.data['label']


def parse_remote_profile(value):
    fields = {'version', 'id', 'label', 'ssh_alias', 'remote_python', 'source_root',
              'state_root', 'launcher', 'source_sha256', 'control_root', 'project_roots',
              'remote_python_sha256', 'state_plugin_sha256'}
    if isinstance(value, dict) and value.get('version') == 2:
        fields.add('installation_layout')
    if not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] not in {1, 2}:
        raise RemoteProfileError('Unsupported installed remote profile')
    layout = None
    if value['version'] == 2:
        try: layout = parse_layout(value['installation_layout'])
        except InstallationLayoutError as error: raise RemoteProfileError(str(error)) from None
    identifier(value['id']); identifier(value['ssh_alias'])
    label = value['label']
    if not isinstance(label, str) or not 1 <= len(label) <= 160 or any(ord(c) < 32 or ord(c) == 127 for c in label):
        raise RemoteProfileError('Invalid remote location label')
    for field in ('remote_python', 'source_root', 'state_root', 'control_root'):
        remote_path(value[field])
    sha256(value['remote_python_sha256'])
    launcher = value['launcher']
    if not isinstance(launcher, dict) or set(launcher) != {'path', 'sha256'}:
        raise RemoteProfileError('Pin the selected launcher path and content')
    remote_path(launcher['path']); sha256(launcher['sha256'])
    hashes = value['source_sha256']
    required = {'botainer/plugins/lifecycle.py', 'botainer/cli/main.py', 'botainer/cli/hpc.py', 'botainer/cli/plugin.py',
                'botainer/plugins/manifest.py'}
    required.update({'botainer/__init__.py', *metadata_paths(layout)} if layout else
        {'pyproject.toml', 'plugins/hpc-launcher/host_helper/submit.py', 'plugins/hpc-launcher/host_helper/_common.py'})
    if not isinstance(hashes, dict) or not required <= hashes.keys() or len(hashes) > 2048:
        raise RemoteProfileError('Native remote CLI source pins are incomplete')
    if layout:
        try: validate_source_pins(layout, hashes)
        except InstallationLayoutError as error: raise RemoteProfileError(str(error)) from None
    state_hashes = value['state_plugin_sha256']
    if not isinstance(state_hashes, dict) or len(state_hashes) > 2048:
        raise RemoteProfileError('Invalid installed plugin source pins')
    if layout and not {'plugins/hpc-launcher/botainer-plugin.yaml', 'plugins/hpc-launcher/host_helper/submit.py',
                       'plugins/hpc-launcher/host_helper/_common.py'} <= state_hashes.keys():
        raise RemoteProfileError('Installed HPC launcher source pins are incomplete')
    for name, digest in [*hashes.items(), *state_hashes.items()]:
        if (not isinstance(name, str) or not name or len(name) > 512 or name.startswith('/')
                or str(PurePosixPath(name)) != name or '..' in PurePosixPath(name).parts
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise RemoteProfileError('Invalid relative source pin')
        sha256(digest)
    roots = value['project_roots']
    if not isinstance(roots, dict) or not 1 <= len(roots) <= 32:
        raise RemoteProfileError('Register bounded project roots explicitly')
    for name, path in roots.items():
        identifier(name); remote_path(path)
    control = PurePosixPath(value['control_root'])
    for root in [value['source_root'], value['state_root'], *roots.values()]:
        candidate = PurePosixPath(root)
        if candidate == control or control in candidate.parents:
            raise RemoteProfileError('Control state cannot contain projects, source or Botainer state')
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    if len(encoded) > 256 * 1024:
        raise RemoteProfileError('Remote profile is too large')
    return RemoteProfile(json.loads(encoded), hashlib.sha256(encoded).hexdigest())


def load_remote_profile(path):
    return parse_remote_profile(read_private_json(Path(path), max_bytes=256 * 1024))
