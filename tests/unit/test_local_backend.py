"""Configuration editor and local-shell authority boundaries; no runtime launch."""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from botainer_dashboard.local_backend import DashboardConfigEditor
from botainer_dashboard.sandbox_runtime import validate_shell_config
from botainer_dashboard.workspace import BotainerValidator, WorkspaceError


class DashboardConfigEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-config-editor-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "config.json"
        self.path.write_text('{"version": 1}\n')
        self.editor = DashboardConfigEditor(self.path)

    def test_validates_own_loader_and_saves_exact_text_for_restart(self):
        current = self.editor.read()
        self.assertEqual(current["format"], "json")
        revision = current["revision"]
        text = '{\n  "version": 1,\n  "machines": {"local": {}}\n}\n'
        self.assertTrue(self.editor.validate(text, revision)["valid"])
        result = self.editor.save(text, revision)
        self.assertTrue(result["saved"])
        self.assertTrue(result["restartRequired"])
        self.assertEqual(result["format"], "json")
        self.assertIn("Separately selected runtime profiles", result["warnings"][0])
        self.assertEqual(self.path.read_bytes(), text.encode())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_invalid_installation_or_duplicate_json_never_replaces_file(self):
        current = self.editor.read()
        for text in ('{"version":1,"version":1}', '{"version":1,"machines":{"local":{"installations":{"default":{"launcher":{"path":"relative"}}}}}}'):
            with self.subTest(text=text):
                result = self.editor.save(text, current["revision"])
                self.assertFalse(result["valid"])
                self.assertFalse(result["saved"])
                self.assertEqual(self.path.read_text(), current["text"])

    def test_external_edit_rejects_stale_dashboard_revision(self):
        revision = self.editor.read()["revision"]
        self.path.write_text('{"version": 1, "machines": {"local": {}}}')
        with self.assertRaisesRegex(WorkspaceError, "revision-conflict"):
            self.editor.save('{"version": 1}\n', revision)

    def test_symlink_and_missing_explicit_config_are_not_editable(self):
        target = self.root / "target.json"
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaises(WorkspaceError):
            self.editor.read()
        with self.assertRaises(WorkspaceError):
            DashboardConfigEditor(None).read()

    def test_yaml_without_parser_is_readable_but_cannot_replace_settings(self):
        path = self.root / "config.yaml"
        path.write_text("version: 1\n")
        editor = DashboardConfigEditor(path)
        current = editor.read()
        self.assertEqual(current["format"], "yaml")
        with patch.dict("sys.modules", {"yaml": None}):
            self.assertFalse(editor.read()["writable"])
            self.assertIn("require PyYAML", editor.read()["readOnlyReason"])
            result = editor.save("version: 1\nmachines: {local: {}}\n", current["revision"])
        self.assertFalse(result["saved"])
        self.assertEqual(result["format"], "yaml")
        self.assertIn("require PyYAML", result["errors"][0])
        self.assertEqual(path.read_text(), current["text"])

    @unittest.skipUnless(importlib.util.find_spec("yaml") is not None, "YAML dependency not installed in this interpreter")
    def test_yaml_save_preserves_comments_exact_text_and_rejects_stale_or_invalid_draft(self):
        path = self.root / "config.yml"
        path.write_text("version: 1\n")
        editor = DashboardConfigEditor(path)
        original = editor.read()
        text = "# My connections\nversion: 1\nmachines:\n  local: {} # machine\n"
        self.assertTrue(editor.validate(text, original["revision"])["valid"])
        result = editor.save(text, original["revision"])
        self.assertTrue(result["saved"])
        self.assertEqual(result["format"], "yaml")
        self.assertNotIn("readOnlyReason", result)
        self.assertEqual(path.read_bytes(), text.encode())
        self.assertEqual(result["revision"], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(editor.save("version: 1\nversion: 1\n", result["revision"])["saved"])
        self.assertEqual(path.read_text(), text)
        with self.assertRaisesRegex(WorkspaceError, "revision-conflict"):
            editor.save("version: 1\n", original["revision"])


class ShellInputPolicyTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"docker": {"image": "sha256:" + "a" * 64}, "fixture_readonly_source": "/approved/fixture"}
        self.raw = {"version": "config-v1", "agent": "dashboard-test", "agent_permissions": "prompt",
                    "runtime": "docker", "image": self.plan["docker"]["image"], "network": {"mode": "none"},
                    "plugins_enabled": ["agent-dashboard-test"], "inject_credentials": [], "job_profiles": {},
                    "mounts": {"extra": [{"source": "/approved/fixture", "target": "/mnt/dashboard-fixture",
                                "mode": "ro", "reason": "Local prototype shell"}]}}

    def test_only_bounded_resources_and_nonsecret_app_values_can_vary(self):
        raw = self.raw | {"resources": {"cpu": 2, "memory_mb": 768}, "env": {"APP_MESSAGE": "hello; literal text"}}
        validate_shell_config(raw, self.plan)
        for update in ({"resources": {"cpu": True}}, {"resources": {"memory_mb": 8193}},
                       {"env": {"LD_PRELOAD": "value"}}, {"env": {"APP_API_KEY": "secret"}},
                       {"resources": {"gpus": 1}}, {"env": {"APP_X": "x" * 1025}}):
            with self.subTest(update=update), self.assertRaises((ValueError, RuntimeError)):
                validate_shell_config(self.raw | update, self.plan)

    def test_config_cannot_broaden_cached_runtime_authority(self):
        variants = [{"network": {"mode": "internet"}}, {"image": "unapproved:latest"},
                    {"inject_credentials": ["personal"]}, {"plugins_enabled": ["agent-claude"]},
                    {"mounts": {"extra": [{"source": "/", "target": "/host", "mode": "rw"}]}},
                    {"runtime": "apptainer"}, {"agent_permissions": "bypass"},
                    {"unexpected": "field"}]
        for update in variants:
            with self.subTest(update=update), self.assertRaises((ValueError, RuntimeError)):
                validate_shell_config(self.raw | update, self.plan)


class ValidatorBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-validator-boundary-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.verified = []

    def validator(self, **options):
        return BotainerValidator(interpreter=self.root / "python", source_root=self.root / "source",
            helper=self.root / "helper.py", private_root=self.root / "private", state_root=self.root / "state",
            home=self.root / "home", verify=lambda: self.verified.append(True), **options)

    def test_raw_policy_is_applied_and_private_model_removed(self):
        seen = []
        validator = self.validator(input_policy=lambda raw: seen.append(raw))
        response = {"valid": True, "errors": [], "warnings": [], "raw": {"resources": {"cpu": 1}}, "model": {"normal": "model"}}
        with patch("botainer_dashboard.workspace.bounded_run", return_value=json.dumps(response).encode()) as runner:
            result = validator("resources: {cpu: 1}", None)
        self.assertEqual(seen, [response["raw"]])
        self.assertNotIn("raw", result)
        self.assertNotIn("model", result)
        self.assertEqual(len(self.verified), 2)
        argv = runner.call_args.args[0]
        self.assertEqual(argv[1:3], ("-I", "-B"))
        self.assertEqual(list(self.root.joinpath("private").iterdir()), [self.root / "private/empty-tools"])

    def test_policy_refusal_returns_invalid_config_without_ui_model(self):
        def refuse(_raw):
            raise ValueError("The selected image is outside this local prototype.")
        validator = self.validator(input_policy=refuse)
        response = {"valid": True, "errors": [], "raw": {"image": "unapproved"}, "model": {}}
        with patch("botainer_dashboard.workspace.bounded_run", return_value=json.dumps(response).encode()):
            result = validator("image: unapproved", None)
        self.assertFalse(result["valid"])
        self.assertIn("outside", result["errors"][0])
        self.assertNotIn("raw", result)

    def test_validator_subprocess_ignores_installed_bytecode_cache(self):
        import importlib.util
        import py_compile
        import sys
        package = self.root / "source/botainer"
        package.mkdir(parents=True)
        source = package / "__init__.py"
        source.write_text("IS_REVIEWED_SOURCE = True\n")
        alternate = self.root / "alternate.py"
        alternate.write_text("IS_REVIEWED_SOURCE = False\n")
        cache = Path(importlib.util.cache_from_source(str(source)))
        py_compile.compile(str(alternate), cfile=str(cache), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        previous_cache = cache.read_bytes()
        helper = self.root / "helper.py"
        helper.write_text("import json,sys\nfrom pathlib import Path\n"
            "request=json.loads(Path(sys.argv[1]).read_text())\n"
            "sys.path.insert(0,request['sourceRoot'])\nimport botainer\n"
            "assert sys.pycache_prefix and sys.dont_write_bytecode\n"
            "assert Path(sys.pycache_prefix).is_dir()\n"
            "print(json.dumps({'valid':botainer.IS_REVIEWED_SOURCE,'errors':[],'warnings':[]}))\n")
        validator = BotainerValidator(interpreter=Path(sys.executable), source_root=self.root / "source",
            helper=helper, private_root=self.root / "private", state_root=self.root / "state",
            home=self.root / "home", verify=lambda: self.verified.append(True))
        result = validator("agent: claude", None)
        self.assertTrue(result["valid"])
        self.assertEqual(cache.read_bytes(), previous_cache)
        self.assertEqual(list((self.root / "private").iterdir()), [self.root / "private/empty-tools"])


if __name__ == "__main__":
    unittest.main()
