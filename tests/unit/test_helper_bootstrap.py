"""Installed helpers expose the dashboard package, never its dependency tree."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class InstalledHelperBootstrapTests(unittest.TestCase):
    def test_selected_botainer_dependency_path_survives_each_installed_bootstrap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            dashboard_site = root / "dashboard-environment/site-packages"
            package = dashboard_site / "botainer_dashboard"
            helpers = package / "_resources/tools"
            helpers.mkdir(parents=True)
            for module in (ROOT / "src/botainer_dashboard").glob("*.py"):
                (package / module.name).write_bytes(module.read_bytes())
            botainer_site = root / "selected-botainer-environment/site-packages"
            botainer_site.mkdir(parents=True)
            name = "dashboard_distribution_dependency_fixture"
            (botainer_site / (name + ".py")).write_text("ORIGIN = 'selected-botainer-environment'\n")
            (dashboard_site / (name + ".py")).write_text("raise AssertionError('dashboard dependency tree leaked into Botainer imports')\n")
            hostile = root / "project"
            hostile.mkdir()
            (hostile / "botainer_dashboard.py").write_text("raise AssertionError('project import')\n")
            (hostile / "sitecustomize.py").write_text("raise AssertionError('project startup')\n")
            probe = (
                "import json,runpy,sys\n"
                "sys.path.insert(0,sys.argv[2])\n"
                "runpy.run_path(sys.argv[1],run_name='helper_definition_test')\n"
                f"import {name} as dependency\n"
                "import botainer_dashboard\n"
                "print(json.dumps({'origin':dependency.ORIGIN,'package':botainer_dashboard.__file__,'path':sys.path}))\n"
            )
            for helper in ("ordinary_local_helper.py", "ordinary_hook_helper.py"):
                with self.subTest(helper=helper):
                    path = helpers / helper
                    path.write_bytes((ROOT / "tools" / helper).read_bytes())
                    env = dict(os.environ, PYTHONPATH=str(hostile))
                    result = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(path), str(botainer_site)],
                                            cwd=hostile, env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    observed = json.loads(result.stdout)
                    self.assertEqual(observed["origin"], "selected-botainer-environment")
                    self.assertEqual(observed["package"], str(package / "__init__.py"))
                    self.assertNotIn(str(dashboard_site), observed["path"])
                    self.assertNotIn(str(hostile), observed["path"])


if __name__ == "__main__":
    unittest.main()
