"""Read trusted-user installation registrations without execution or writes.

This is dashboard configuration, not project-supplied configuration. Resolution
only selects a local executable; it does not establish its identity, capability,
source revision, Docker endpoint, or permission to run it. SSH entries are data
for a future adapter and can never resolve to a local execution context.
"""

from __future__ import annotations

import json
import importlib.util
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping, Optional, Union

from .context import ContextError, ExecutionContext, Launcher


MAX_CONFIG_BYTES = 256 * 1024
MAX_YAML_DEPTH = 32
MAX_YAML_NODES = 8192


class ConfigError(ValueError):
    """A dashboard configuration is invalid or cannot be resolved safely."""


@dataclass(frozen=True)
class LauncherConfig:
    mode: str = "executable"
    path: Optional[str] = None


@dataclass(frozen=True)
class InstallationConfig:
    launcher: LauncherConfig
    state_root: str


@dataclass(frozen=True)
class MachineConfig:
    transport: str
    installations: Mapping[str, InstallationConfig]
    default_installation: str
    ssh_alias: Optional[str] = None


@dataclass(frozen=True)
class DashboardConfig:
    machines: Mapping[str, MachineConfig]
    default_machine: str
    source_path: Optional[Path] = None


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or any(
            ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError(f"{label} must be a nonempty string without control characters")
    return value


def _identifier(value: object, label: str) -> str:
    value = _text(value, label)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", value):
        raise ConfigError(f"{label} must use 1–48 letters, digits, underscores or hyphens")
    return value


def _object(value: object, allowed: set, label: str) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ConfigError(f"{label} must be an object containing only {sorted(allowed)}")
    return value


def _path(value: object, label: str, *, remote: bool = False) -> str:
    value = _text(value, label)
    if "$" in value or "`" in value:
        raise ConfigError(f"{label} must not contain shell or environment interpolation")
    if value.startswith("~/") and not remote:
        parts = PurePosixPath(value[2:]).parts
    elif value.startswith("/"):
        parts = PurePosixPath(value).parts
    else:
        raise ConfigError(f"{label} must be absolute" + ("" if remote else " or start with ~/"))
    if ".." in parts or "~" in parts:
        raise ConfigError(f"{label} must not contain parent traversal or embedded ~")
    return value


def _local_path(value: str, home: Path) -> Path:
    return home / value[2:] if value.startswith("~/") else Path(value)


def _home_path(value: Optional[Union[str, Path]]) -> Path:
    home = Path.home() if value is None else Path(value)
    _path(str(home), "local home")
    if not home.is_absolute():
        raise ConfigError("local home must be absolute")
    return home


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("duplicate JSON object key")
        result[key] = value
    return result


def config_format(path: Optional[Union[str, Path]]) -> str:
    """The filename selects syntax; existing JSON files are never reinterpreted."""
    return "yaml" if path is not None and Path(path).suffix.lower() in {".yaml", ".yml"} else "json"


def yaml_support_available() -> bool:
    """Inspect availability without loading a parser or another environment."""
    try:
        return importlib.util.find_spec("yaml") is not None
    except (ImportError, ValueError):
        return False


def _yaml_document(text: str) -> object:
    # Optional until this dependency is explicitly installed in the dashboard
    # runtime. Never borrow another application's interpreter or site-packages.
    try:
        import yaml
    except ImportError:
        raise ConfigError("YAML settings require PyYAML in the dashboard environment; JSON settings remain supported") from None

    class SettingsLoader(yaml.SafeLoader):
        """Data-only YAML with no aliases/tags/merge keys or duplicate mappings."""
        def __init__(self, stream):
            self.settings_depth = 0
            self.settings_nodes = 0
            super().__init__(stream)

        def compose_node(self, parent, index):
            event = self.peek_event()
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise ConfigError("YAML anchors and aliases are not supported in dashboard settings")
            if getattr(event, "tag", None) is not None:
                raise ConfigError("Explicit YAML tags are not supported in dashboard settings")
            self.settings_nodes += 1
            self.settings_depth += 1
            try:
                if self.settings_nodes > MAX_YAML_NODES or self.settings_depth > MAX_YAML_DEPTH:
                    raise ConfigError("YAML settings exceed the node or nesting limit")
                return super().compose_node(parent, index)
            finally:
                self.settings_depth -= 1

        def construct_mapping(self, node, deep=False):
            if not isinstance(node, yaml.nodes.MappingNode):
                raise ConfigError("YAML settings mappings must be objects")
            result = {}
            for key_node, value_node in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    raise ConfigError("YAML merge keys are not supported in dashboard settings")
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str):
                    raise ConfigError("YAML settings mapping keys must be strings; quote numeric or boolean names")
                if key in result:
                    raise ConfigError("duplicate YAML object key")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    loader = None
    try:
        loader = SettingsLoader(text)
        return loader.get_single_data()
    except ConfigError:
        raise
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        raise ConfigError("config must contain one valid YAML document" + location) from None
    finally:
        if loader is not None:
            loader.dispose()


def parse_config_text(text: str, source: Optional[Path] = None) -> DashboardConfig:
    """Parse bounded JSON or YAML into the same strict registration schema."""
    try:
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ConfigError("config must be UTF-8 text within the 256 KiB limit")
        raw = (_yaml_document(text) if config_format(source) == "yaml"
               else json.loads(text, object_pairs_hook=_unique_object))
        return _parse(raw, source)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ConfigError("config must contain valid UTF-8 " + config_format(source).upper() + " with bounded nesting") from exc


def _parse(raw: object, source: Optional[Path]) -> DashboardConfig:
    data = _object(raw, {"version", "machines", "default_machine"}, "config")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise ConfigError("config version must be the integer 1")
    machines = data.get("machines", {"local": {}})
    if not isinstance(machines, dict) or not machines:
        raise ConfigError("machines must be a nonempty object")
    parsed = {}
    for name, raw_machine in machines.items():
        _identifier(name, "machine ID")
        machine = _object(raw_machine, {
            "transport", "ssh_alias", "installations", "default_installation"}, "machine")
        transport = machine.get("transport", "local")
        if transport not in ("local", "ssh"):
            raise ConfigError("transport must be local or ssh")
        remote = transport == "ssh"
        alias = machine.get("ssh_alias")
        if remote:
            if not isinstance(alias, str) or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", alias):
                raise ConfigError("ssh machines require a simple ssh_alias")
        elif "ssh_alias" in machine:
            raise ConfigError("local machines must not have ssh_alias")
        installs = machine.get("installations", {"default": {}})
        if not isinstance(installs, dict) or not installs:
            raise ConfigError("installations must be a nonempty object")
        registered = {}
        for install_id, raw_install in installs.items():
            _identifier(install_id, "installation ID")
            install = _object(raw_install, {"launcher", "state_root"}, "installation")
            launcher = _object(install.get("launcher", {}), {"mode", "path"}, "launcher")
            mode = launcher.get("mode", "executable")
            if mode not in ("executable", "python_module"):
                raise ConfigError("launcher mode must be executable or python_module")
            path = launcher.get("path")
            if "path" in launcher:
                path = _path(path, "launcher path", remote=remote)
            elif remote or mode == "python_module":
                raise ConfigError("remote and python_module launchers require an explicit path")
            state = install.get("state_root", "~/.botainer")
            state = _path(state, "state_root", remote=remote)
            registered[install_id] = InstallationConfig(LauncherConfig(mode, path), state)
        default_install = _identifier(machine.get("default_installation", "default"),
                                      "default installation")
        if default_install not in registered:
            raise ConfigError("default_installation must name a registered installation")
        parsed[name] = MachineConfig(transport, MappingProxyType(registered), default_install, alias)
    default_machine = _identifier(data.get("default_machine", "local"), "default machine")
    if default_machine not in parsed:
        raise ConfigError("default_machine must name a registered machine")
    return DashboardConfig(MappingProxyType(parsed), default_machine, source)


def load_config(path: Optional[Union[str, Path]] = None, *,
                environ: Optional[Mapping[str, str]] = None,
                home: Optional[Union[str, Path]] = None) -> DashboardConfig:
    """Load v1 JSON/YAML: explicit argument, environment override, then user default.

    Only an absent default file produces built-in defaults. An explicit missing
    file is an error. Relative explicit *config filenames* are allowed; launcher
    and state paths inside the file must be absolute or local ~/. The default
    remains config.json; YAML requires an explicit .yaml/.yml selection. Never
    writes or migrates another format.
    """
    env = os.environ if environ is None else environ
    local_home = _home_path(home)
    explicit = path is not None or "BOTAINER_DASHBOARD_CONFIG" in env
    selected = path if path is not None else env.get("BOTAINER_DASHBOARD_CONFIG")
    if selected is None:
        xdg = env.get("XDG_CONFIG_HOME", "")
        base = Path(xdg) if xdg.startswith("/") else local_home / ".config"
        selected = base / "botainer-dashboard" / "config.json"
    text_path = _text(str(selected) if isinstance(selected, Path) else selected, "config filename")
    if "$" in text_path or "`" in text_path or ("~" in text_path and not text_path.startswith("~/")):
        raise ConfigError("config filename cannot contain interpolation or named-user expansion")
    config_path = _local_path(text_path, local_home).absolute()
    try:
        # Refuse devices/FIFOs without waiting for a writer, and bound the read.
        descriptor = os.open(config_path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ConfigError("config must be a regular file")
            content = handle.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError as exc:
        if not explicit:
            return _parse({"version": 1}, None)
        raise ConfigError("explicit config file does not exist") from exc
    except OSError as exc:
        raise ConfigError("config file cannot be read") from exc
    if len(content) > MAX_CONFIG_BYTES:
        raise ConfigError("config exceeds the 256 KiB limit")
    try:
        return parse_config_text(content.decode("utf-8"), config_path)
    except UnicodeError as exc:
        raise ConfigError("config must contain valid UTF-8 text") from exc


def _find_botainer(search_path: str) -> Path:
    _text(search_path, "trusted PATH")
    directories = search_path.split(os.pathsep)
    for directory in directories:
        # Validate the complete PATH before selecting any entry; never use cwd.
        _path(directory, "trusted PATH directory", remote=True)
        if Path(directory).resolve() == Path.cwd().resolve():
            raise ConfigError("trusted PATH must not search the current directory")
    for directory in directories:
        selected = Path(directory) / "botainer"
        if selected.is_file() and os.access(selected, os.X_OK):
            return selected
    raise ConfigError("botainer was not found on the supplied trusted PATH; set launcher.path")


def resolve_local_context(config: DashboardConfig, *, site_id: str, account: str,
                          search_path: str,
                          machine_id: Optional[str] = None,
                          installation_id: Optional[str] = None,
                          home: Optional[Union[str, Path]] = None) -> ExecutionContext:
    """Resolve one explicit local snapshot without running or importing Botainer.

    Site/account identity is supplied by a later trusted registration boundary,
    not inferred from a display name. Callers must retain and verify the snapshot
    before dispatch; registration is neither executable identity nor approval.
    """
    selected_machine = config.default_machine if machine_id is None else machine_id
    try:
        machine = config.machines[selected_machine]
    except (KeyError, TypeError) as exc:
        raise ConfigError("unknown machine") from exc
    if machine.transport != "local":
        raise ConfigError("SSH registration is declarative; the remote adapter is not implemented")
    selected_install = machine.default_installation if installation_id is None else installation_id
    try:
        installation = machine.installations[selected_install]
    except (KeyError, TypeError) as exc:
        raise ConfigError("unknown installation") from exc
    local_home = _home_path(home)
    executable = (_find_botainer(search_path) if installation.launcher.path is None
                  else _local_path(installation.launcher.path, local_home))
    try:
        return ExecutionContext(
            f"{selected_machine}.{selected_install}", site_id, account,
            Launcher(executable, installation.launcher.mode),
            _local_path(installation.state_root, local_home))
    except ContextError as exc:
        raise ConfigError(str(exc)) from exc
