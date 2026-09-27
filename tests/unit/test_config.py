"""Configuration selection and trust boundaries; no Botainer or network use."""

from dataclasses import FrozenInstanceError
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from botainer_dashboard.config import (
    ConfigError, MAX_CONFIG_BYTES, load_config, resolve_local_context, parse_config_text, config_format,
)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-config-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.executable = self.bin / "botainer"
        self.executable.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        self.executable.chmod(0o700)
        self.filename = self.root / "dashboard.json"

    def load(self, data):
        self.filename.write_text(json.dumps(data), encoding="utf-8")
        return load_config(self.filename, environ={}, home=self.root)

    def resolve(self, config, **options):
        return resolve_local_context(config, site_id="this-host", account="example-user",
                                     search_path=str(self.bin), home=self.root, **options)

    def explicit(self, path=None, mode="executable"):
        return {"version": 1, "machines": {"local": {"installations": {
            "default": {"launcher": {"mode": mode, "path": str(path or self.executable)},
                        "state_root": "~/.botainer-custom"}}}}}

    def test_missing_default_uses_path_and_home_without_writes(self):
        before = set(self.root.rglob("*"))
        config = load_config(environ={}, home=self.root)
        context = self.resolve(config)
        self.assertEqual(context.launcher.executable, self.executable)
        self.assertEqual(context.state_root, self.root / ".botainer")
        self.assertEqual(context.context_id, "local.default")
        self.assertIsNone(config.source_path)
        self.assertEqual(before, set(self.root.rglob("*")))

    def test_default_and_xdg_locations_and_override_precedence(self):
        default = self.root / ".config" / "botainer-dashboard" / "config.json"
        default.parent.mkdir(parents=True)
        default.write_text('{"version": 1}', encoding="utf-8")
        self.assertEqual(load_config(environ={}, home=self.root).source_path, default)
        self.assertEqual(load_config(environ={"XDG_CONFIG_HOME": "relative"},
                                     home=self.root).source_path, default)
        xdg = self.root / "xdg"
        custom = xdg / "botainer-dashboard" / "config.json"
        custom.parent.mkdir(parents=True)
        custom.write_text('{"version": 1}', encoding="utf-8")
        env = {"XDG_CONFIG_HOME": str(xdg), "BOTAINER_DASHBOARD_CONFIG": str(default)}
        self.assertEqual(load_config(environ={"XDG_CONFIG_HOME": str(xdg)},
                                     home=self.root).source_path, custom)
        self.assertEqual(load_config(environ=env, home=self.root).source_path, default)
        self.assertEqual(load_config(custom, environ=env, home=self.root).source_path, custom)

    def test_explicit_missing_or_empty_override_does_not_fall_back(self):
        for options in ({"path": self.filename},
                        {"environ": {"BOTAINER_DASHBOARD_CONFIG": str(self.filename)}},
                        {"environ": {"BOTAINER_DASHBOARD_CONFIG": ""}}):
            with self.subTest(options=options), self.assertRaises(ConfigError):
                load_config(home=self.root, **options)

    def test_explicit_installation_ignores_ambient_path_and_preserves_venv_python(self):
        venv = self.root / ".venvs" / "botainer-v1" / "bin"
        venv.mkdir(parents=True)
        python = venv / "python"
        python.symlink_to(self.executable)
        data = self.explicit("~/.venvs/botainer-v1/bin/python", "python_module")
        config = self.load(data)
        context = resolve_local_context(config, site_id="this-host", account="example-user",
                                        search_path="", home=self.root)
        self.assertEqual(context.launcher.executable, python)
        self.assertNotEqual(context.launcher.executable, python.resolve())
        self.assertEqual(context.launcher.argv_prefix,
                         (str(python), "-I", "-m", "botainer.cli.main"))
        self.assertEqual(context.state_root, self.root / ".botainer-custom")

    def test_path_snapshot_does_not_follow_later_path_changes(self):
        config = self.load({"version": 1})
        first = self.resolve(config)
        other_bin = self.root / "other-bin"
        other_bin.mkdir()
        other = other_bin / "botainer"
        other.write_text("#!/bin/sh\nexit 98\n", encoding="utf-8")
        other.chmod(0o700)
        second = resolve_local_context(config, site_id="this-host", account="example-user",
                                       search_path=str(other_bin), home=self.root)
        self.assertEqual(first.launcher.executable, self.executable)
        self.assertEqual(second.launcher.executable, other)

    def test_default_search_refuses_empty_relative_and_current_directory_entries(self):
        config = self.load({"version": 1})
        for search in ("", ":" + str(self.bin), str(self.bin) + ":", ".",
                       str(self.bin) + ":relative", str(self.bin) + ":" + str(Path.cwd())):
            with self.subTest(search=search), self.assertRaises(ConfigError):
                resolve_local_context(config, site_id="host", account="user",
                                      search_path=search, home=self.root)

    def test_missing_nonexecutable_or_directory_launcher_is_rejected(self):
        for path in (self.root / "missing", self.root):
            with self.subTest(path=path), self.assertRaises(ConfigError):
                self.resolve(self.load(self.explicit(path)))
        self.executable.chmod(0o600)
        with self.assertRaises(ConfigError):
            self.resolve(self.load({"version": 1}))
        with self.assertRaises(ConfigError):
            self.resolve(self.load(self.explicit()))

    def test_remote_paths_are_retained_and_never_resolved_locally(self):
        data = {"version": 1, "default_machine": "cluster", "machines": {"cluster": {
            "transport": "ssh", "ssh_alias": "research-cluster", "installations": {
                "default": {"launcher": {"path": "/opt/botainer/bin/botainer"},
                            "state_root": "/scratch/example-user/botainer"}}}}}
        config = self.load(data)
        self.assertEqual(config.machines["cluster"].installations["default"].state_root,
                         "/scratch/example-user/botainer")
        with self.assertRaisesRegex(ConfigError, "remote adapter"):
            self.resolve(config)
        install = data["machines"]["cluster"]["installations"]["default"]
        for key, value in (("state_root", "~/.botainer"), ("launcher", {"path": "~/bin/botainer"})):
            changed = json.loads(json.dumps(data))
            changed["machines"]["cluster"]["installations"]["default"][key] = value
            with self.subTest(key=key), self.assertRaises(ConfigError):
                self.load(changed)
        install["launcher"] = {}
        with self.assertRaises(ConfigError):
            self.load(data)

    def test_multiple_installations_select_explicit_defaults_and_shared_state(self):
        data = self.explicit()
        local = data["machines"]["local"]
        local["installations"]["alternate"] = local["installations"]["default"].copy()
        local["default_installation"] = "alternate"
        config = self.load(data)
        selected = self.resolve(config)
        fallback = self.resolve(config, installation_id="default")
        self.assertEqual(selected.context_id, "local.alternate")
        self.assertEqual(selected.namespace_key, fallback.namespace_key)
        for options in ({"machine_id": "absent"}, {"installation_id": "absent"}):
            with self.assertRaises(ConfigError):
                self.resolve(config, **options)

    def test_parsed_nested_configuration_is_immutable(self):
        config = self.load(self.explicit())
        with self.assertRaises(TypeError):
            config.machines["new"] = config.machines["local"]
        with self.assertRaises(TypeError):
            config.machines["local"].installations["new"] = None
        with self.assertRaises(FrozenInstanceError):
            config.machines["local"].installations["default"].launcher.mode = "shell"

    def test_unknown_fields_bad_versions_and_references_fail_closed(self):
        invalid = [None, [], {}, {"version": True}, {"version": 2}, {"version": "1"},
                   {"version": 1, "credentials": "secret"},
                   {"version": 1, "machines": {}},
                   {"version": 1, "default_machine": "missing"},
                   {"version": 1, "machines": {"with.dot": {}}},
                   {"version": 1, "machines": {"local": {"default_installation": "missing"}}},
                   {"version": 1, "machines": {"local": {"ssh_alias": "unexpected"}}},
                   {"version": 1, "machines": {"local": {"transport": "shell"}}},
                   {"version": 1, "machines": {"local": {"transport": "ssh", "ssh_alias": "-oProxyCommand=evil"}}}]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ConfigError):
                self.load(data)
        for field, value in (("launcher", {"mode": "shell"}),
                             ("launcher", {"mode": "python_module"}),
                             ("launcher", {"path": None}),
                             ("launcher", {"arguments": ["--dangerous"]}),
                             ("unknown", "value"), ("state_root", False)):
            data = self.explicit()
            data["machines"]["local"]["installations"]["default"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ConfigError):
                self.load(data)

    def test_interpolation_relative_launchers_and_traversal_are_rejected(self):
        for path in ("alternate", "bin/botainer", "$HOME/bin/botainer", "/tmp/$(touch marker)",
                     "/tmp/`touch marker`", "~someone/bin/botainer", "~/../botainer",
                     "/opt/../bin/botainer", "/tmp/botainer\n"):
            with self.subTest(path=path), self.assertRaises(ConfigError):
                self.load(self.explicit(path))
        self.assertFalse((self.root / "marker").exists())

    def test_duplicate_json_keys_invalid_encoding_size_and_nonfiles_are_rejected(self):
        invalid = [b'{"version": 1, "version": 1}',
                   b'{"version": 1, "machines": {"local": {}, "local": {}}}',
                   b'{"version": 1, "machines": {"local": {"installations": {"default": {}, "default": {}}}}}',
                   b"\xff", b"{", b" " * (MAX_CONFIG_BYTES + 1), b"[" * 2000]
        for content in invalid:
            self.filename.write_bytes(content)
            with self.subTest(content=content[:50]), self.assertRaises(ConfigError):
                load_config(self.filename, environ={}, home=self.root)
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        with self.assertRaises(ConfigError):
            load_config(fifo, environ={}, home=self.root)
        with self.assertRaises(ConfigError):
            load_config(self.root, environ={}, home=self.root)

    def test_public_example_parses_as_default_registration(self):
        example = Path(__file__).resolve().parents[2] / "config.example.json"
        config = load_config(example, environ={}, home=self.root)
        self.assertEqual(self.resolve(config).launcher.executable, self.executable)

    def test_yaml_format_is_explicit_and_missing_parser_never_changes_json_loading(self):
        self.assertEqual(config_format(self.filename), "json")
        self.assertEqual(config_format(self.root / "settings.yaml"), "yaml")
        self.assertEqual(config_format(self.root / "settings.yml"), "yaml")
        with patch.dict("sys.modules", {"yaml": None}):
            self.assertEqual(parse_config_text('{"version":1}', self.filename).default_machine, "local")
            with self.assertRaisesRegex(ConfigError, "require PyYAML"):
                parse_config_text("version: 1", self.root / "settings.yaml")
        with self.assertRaises(ConfigError):
            parse_config_text("version: 1", self.filename)


@unittest.skipUnless(importlib.util.find_spec("yaml") is not None, "YAML dependency not installed in this interpreter")
class YamlConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-yaml-config-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "settings.yaml"

    def load_text(self, text):
        self.path.write_text(text, encoding="utf-8")
        return load_config(self.path, environ={}, home=self.root)

    def test_yaml_comments_block_mappings_and_explicit_remote_identity_share_json_schema(self):
        config = self.load_text("""# User maintained connections
version: 1
default_machine: cluster
machines:
  cluster:
    transport: ssh
    ssh_alias: research-cluster
    installations:
      default:
        launcher:
          path: /opt/botainer/bin/botainer
        state_root: /scratch/example-user/botainer
""")
        self.assertEqual(config.source_path, self.path)
        self.assertEqual(config.default_machine, "cluster")
        self.assertEqual(config.machines["cluster"].ssh_alias, "research-cluster")
        self.assertEqual(config.machines["cluster"].installations["default"].state_root,
                         "/scratch/example-user/botainer")
        self.assertTrue(self.path.read_text().startswith("# User maintained connections\n"))

    def test_unknown_fields_invalid_paths_and_unsupported_yaml_are_rejected(self):
        samples = ("version: 1\nextra: value\n", "version: true\n", "version: 1\nversion: 1\n",
            "version: 1\nmachines:\n  local: {}\n  local: {}\n",
            "version: 1\nmachines: {local: {installations: {default: {launcher: {path: relative}}}}}\n",
            "version: 1\nmachines: {local: !!python/object/apply:os.system [echo unsafe]}\n",
            "version: !!int 1\n", "version: 1\nmachines: &machines {local: {}}\n",
            "version: 1\nmachines: *missing\n", "version: 1\n<<: {machines: {local: {}}}\n",
            "version: 1\n---\nversion: 1\n", "version: 1\nmachines: {1: {}}\n", "version: 1\n\x00",
            "version: 1\nmachines: [incomplete")
        for text in samples:
            with self.subTest(text=text[:90]), self.assertRaises(ConfigError):
                self.load_text(text)

    def test_yaml_size_depth_and_node_limits_fail_before_schema_processing(self):
        samples = ("#" + "x" * MAX_CONFIG_BYTES, "[" * 40 + "0" + "]" * 40,
                   "[" + ",".join("0" for _ in range(8193)) + "]")
        for text in samples:
            with self.subTest(size=len(text)), self.assertRaisesRegex(ConfigError, "limit"):
                self.load_text(text)

    def test_yaml_file_is_not_implicitly_selected_as_default_or_used_for_invalid_json(self):
        default = self.root / ".config/botainer-dashboard/config.yaml"
        default.parent.mkdir(parents=True)
        default.write_text("not valid dashboard configuration")
        self.assertIsNone(load_config(environ={}, home=self.root).source_path)
        with self.assertRaises(ConfigError):
            parse_config_text("version: 1", self.root / "config.json")


if __name__ == "__main__":
    unittest.main()
