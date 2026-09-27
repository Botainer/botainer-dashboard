"""Offline convenience-command boundaries; never start a service or subprocess."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / "src/botainer_dashboard/cli.py"
SPEC = importlib.util.spec_from_file_location("dashboard_command", SOURCE)
command = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = command
SPEC.loader.exec_module(command)


def profile_data():
    return {"version": 1, "id": "example", "label": "Example site",
        "preset": {"version": 1, "id": "cpu-trial", "label": "CPU trial", "scheduler": "slurm",
            "runtime": "apptainer", "attach_route": "slurm-screen",
            "partitions": [{"name": "short", "max_cpus": 1, "max_memory_mib": 1024,
                "max_time_minutes": 5, "max_gpus": 0}]},
        "connection": {"ssh_alias": "cluster-example", "remote_python": "/opt/botainer/python",
            "source_root": "/work/example/source", "state_root": "/home/example/.botainer",
            "image": "/images/existing.sif", "trial_root": "/home/example/botainer-dashboard-test-example",
            "launcher": {"mode": "python_module", "path": "/opt/botainer/python"},
            "file_roots": {"home": "/home/example"}},
        "defaults": {"partition": "short", "cpus": 1, "memory_mib": 1024, "time_minutes": 5, "gpus": 0}}


class DashboardCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o700)
        self.write(".local/envs/botainer_dashboard/bin/python", "never execute this fixture", 0o700)
        self.write("tools/run_dashboard.py", "# fixture\n")
        for asset in command.VENDOR_ASSETS:
            self.write(".local/frontend/vendor/" + asset, "fixture")
        self.write(".local/config.json", '{"version": 1}')
        self.write(".local/reviews/disposable-runtime-plan.json", "{}")

    def write(self, relative, text, mode=0o600):
        path = self.root / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
        return path

    def profile(self, relative=None, value=None):
        return self.write(relative or Path(".local/cluster-workspace/example.json"), json.dumps(value or profile_data()))

    def plan(self, *args):
        return command.plan_for(command.parser().parse_args(args), root=self.root)

    def test_start_verb_preserves_existing_arguments(self):
        self.assertEqual(self.plan("start", "--open", "--port", "8765").argv(),
                         self.plan("--open", "--port", "8765").argv())

    def test_check_verb_never_executes(self):
        with contextlib.redirect_stdout(io.StringIO()), patch.object(command.os, "execv") as execute:
            self.assertEqual(command.main(["check"], root=self.root), 0)
        execute.assert_not_called()

    def test_lifecycle_validation_precedes_any_control(self):
        invalid = [
            ["status", "--port", "0"], ["stop", "--port", "65536"],
            ["stop", "--instance", "../bad"], ["status", "--instance", "A" * 32],
            ["stop", "--instance", "a" * 32, "--port", "8765"], ["stop", "--json"],
            ["status", "--open"], ["stop", "--config", "config.json"],
            ["status", "--check"], ["start", "--instance", "a" * 32],
            ["start", "--json"], ["check", "--pairing-code", "access.json"],
        ]
        for argv in invalid:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(command, "manage_service") as manage, self.assertRaises(SystemExit):
                command.main(argv, root=self.root)
            manage.assert_not_called()

    def service_record(self, name="a", status="running", port=8765):
        return {"runId": name * 32, "url": f"http://127.0.0.1:{port}",
                "pid": 1234, "mode": "setup",
                "serviceFile": str(self.root / ".local/run" / (name * 32) / "service.json"),
                "status": status, "responsive": status in {"running", "starting", "stopping"}}

    def run_control(self, argv, records, *, confirmed=True):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                patch("botainer_dashboard.service_control.discover_services", return_value=records) as discover, \
                patch("botainer_dashboard.service_control.inspect_service", return_value=records[0] if records else {}) as inspect, \
                patch("botainer_dashboard.service_control.request_service_action", return_value=records[0] if records else {}) as request, \
                patch("botainer_dashboard.service_control.wait_for_stopped", return_value=confirmed) as wait, \
                patch.object(command, "plan_for", side_effect=AssertionError("no profiles")), \
                patch.object(command, "inspect_prerequisites", side_effect=AssertionError("no prerequisite probes")), \
                patch.object(command.os, "execv", side_effect=AssertionError("no launch")), \
                patch.object(command.os, "kill", side_effect=AssertionError("no PID signals")):
            code = command.main(argv, root=self.root)
        return code, output.getvalue(), discover, inspect, request, wait

    def test_status_is_independent_of_runtime_and_returns_no_pairing_secret(self):
        record = self.service_record()
        code, output, _discover, inspect, request, wait = self.run_control(["status", "--json"], [record])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {"services": [record]})
        inspect.assert_not_called()
        request.assert_not_called()
        wait.assert_not_called()

    def test_legacy_history_does_not_make_responsive_managed_status_fail(self):
        records = [self.service_record(), self.service_record("b", "unmanaged")]
        code, output, *_calls = self.run_control(["status"], records)
        self.assertEqual(code, 0)
        self.assertIn("liveness is unknown", output)
        self.assertNotIn(records[1]["runId"], output)

    def test_exact_instance_bypasses_history_scan(self):
        record = self.service_record()
        code, _output, discover, inspect, request, wait = self.run_control(
            ["stop", "--instance", record["runId"]], [record])
        self.assertEqual(code, 0)
        discover.assert_not_called()
        inspect.assert_called_once_with(Path(record["serviceFile"]))
        request.assert_called_once_with(record["serviceFile"], "stop")
        wait.assert_called_once_with(record["serviceFile"], timeout=10.0)

    def test_stop_refuses_ambiguous_unknown_and_unresponsive_owners(self):
        for records in ([self.service_record(), self.service_record("b")],
                        [self.service_record(), self.service_record("b", "unknown")],
                        [self.service_record(status="unresponsive")],
                        [self.service_record(status="control-unavailable")]):
            with self.subTest(records=records):
                code, _output, _discover, _inspect, request, wait = self.run_control(["stop"], records)
                self.assertEqual(code, 2)
                request.assert_not_called()
                wait.assert_not_called()

    def test_port_reuse_ignores_stopped_record_but_never_stops_other_port(self):
        records = [self.service_record(status="stopped"), self.service_record("b"),
                   self.service_record("c", port=8766)]
        code, _output, _discover, _inspect, request, _wait = self.run_control(["stop", "--port", "8765"], records)
        self.assertEqual(code, 0)
        request.assert_called_once_with(records[1]["serviceFile"], "stop")

    def test_stop_timeout_is_not_success_and_no_force_fallback(self):
        code, output, _discover, _inspect, request, wait = self.run_control(["stop"], [self.service_record()], confirmed=False)
        self.assertEqual(code, 2)
        self.assertIn("not yet confirmed", output)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(wait.call_count, 1)

    def test_no_managed_service_does_not_stop_legacy_records(self):
        for records in ([], [self.service_record(status="stopped")], [self.service_record(status="unmanaged")]):
            with self.subTest(records=records):
                code, _output, _discover, _inspect, request, wait = self.run_control(["stop"], records)
                self.assertEqual(code, 1)
                request.assert_not_called()
                wait.assert_not_called()

    def run_pair(self, argv, records, *, interactive=True, pairing_result=0):
        class Output(io.StringIO):
            def isatty(self):
                return interactive

        output, errors = Output(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                patch("botainer_dashboard.service_control.discover_services", return_value=records) as discover, \
                patch("botainer_dashboard.service_control.inspect_service", return_value=records[0] if records else {}) as inspect, \
                patch("botainer_dashboard.service_control.request_service_action", side_effect=AssertionError("no stop")), \
                patch("botainer_dashboard.service_control.wait_for_stopped", side_effect=AssertionError("no stop wait")), \
                patch.object(command, "show_pairing_code", return_value=pairing_result) as show, \
                patch.object(command, "plan_for", side_effect=AssertionError("no profiles")), \
                patch.object(command, "inspect_prerequisites", side_effect=AssertionError("no prerequisite probes")), \
                patch.object(command.os, "execv", side_effect=AssertionError("no launch")), \
                patch.object(command.os, "kill", side_effect=AssertionError("no PID signals")):
            code = command.main(argv, root=self.root)
        return code, output.getvalue() + errors.getvalue(), discover, inspect, show

    def test_pair_rejects_invalid_selectors_and_unrelated_flags_before_control(self):
        invalid = [
            ["--port", "0"], ["--port", "65536"], ["--port", "-1"],
            ["--instance", "../bad"], ["--instance", "A" * 32],
            ["--instance", "a" * 31], ["--instance", "g" * 32],
            ["--port", "8765", "--instance", "a" * 32],
            ["--json"], ["--open"], ["--check"], ["--setup-only"],
            ["--local-only"], ["--cluster-only"], ["--native-cli"],
            ["--config", "config.json"], ["--connections", "selection.json"],
            ["--local-profile", "local.json"], ["--remote-profile", "remote.json"],
            ["--host-profile", "host.json"], ["--cluster-profile", "cluster.json"],
            ["--pairing-code", "access.json"],
        ]
        for flags in invalid:
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(command, "manage_service") as manage, \
                    patch.object(command, "show_pairing_code") as show, self.assertRaises(SystemExit):
                command.main(["pair", *flags], root=self.root)
            manage.assert_not_called()
            show.assert_not_called()

    def test_pair_refuses_redirected_output_before_service_discovery_or_inspection(self):
        record = self.service_record()
        for selector in ([], ["--port", "8765"], ["--instance", record["runId"]]):
            with self.subTest(selector=selector):
                code, _output, discover, inspect, show = self.run_pair(
                    ["pair", *selector], [record], interactive=False)
                self.assertEqual(code, 2)
                discover.assert_not_called()
                inspect.assert_not_called()
                show.assert_not_called()

    def test_pair_single_running_service_uses_canonical_path_and_exact_owner(self):
        record = self.service_record()
        code, _output, discover, inspect, show = self.run_pair(["pair"], [record])
        self.assertEqual(code, 0)
        discover.assert_called_once_with(self.root / ".local/run")
        inspect.assert_not_called()
        show.assert_called_once_with(
            self.root / ".local/run" / record["runId"] / "access.json", root=self.root,
            expected_service={name: record[name] for name in ("runId", "url", "pid", "mode")})

    def test_pair_refuses_foreign_service_file_or_invalid_instance_identity(self):
        records = [dict(self.service_record(), serviceFile=str(self.root / "unrelated" / "service.json")),
                   dict(self.service_record(), runId="../other"),
                   dict(self.service_record(), runId=None)]
        for record in records:
            with self.subTest(record=record):
                code, _output, _discover, _inspect, show = self.run_pair(["pair"], [record])
                self.assertEqual(code, 2)
                show.assert_not_called()

    def test_pair_exact_instance_inspects_only_that_service(self):
        record = self.service_record()
        code, _output, discover, inspect, show = self.run_pair(
            ["pair", "--instance", record["runId"]], [record])
        self.assertEqual(code, 0)
        discover.assert_not_called()
        inspect.assert_called_once_with(self.root / ".local/run" / record["runId"] / "service.json")
        self.assertEqual(show.call_count, 1)
        self.assertEqual(show.call_args.kwargs["expected_service"]["runId"], record["runId"])

    def test_pair_port_selects_live_owner_and_ignores_stopped_history(self):
        records = [self.service_record(status="stopped"), self.service_record("b"),
                   self.service_record("c", port=8766), self.service_record("d", "unmanaged")]
        code, _output, _discover, inspect, show = self.run_pair(["pair", "--port", "8765"], records)
        self.assertEqual(code, 0)
        inspect.assert_not_called()
        show.assert_called_once_with(
            self.root / ".local/run" / records[1]["runId"] / "access.json", root=self.root,
            expected_service={name: records[1][name] for name in ("runId", "url", "pid", "mode")})

    def test_pair_port_endpoints_are_valid_and_do_not_start_missing_services(self):
        for port in (1, 65535):
            with self.subTest(port=port):
                code, _output, discover, _inspect, show = self.run_pair(["pair", "--port", str(port)], [])
                self.assertEqual(code, 1)
                discover.assert_called_once()
                show.assert_not_called()

    def test_pair_refuses_ambiguity_even_when_only_one_service_is_responsive(self):
        for extra_status in ("running", "starting", "stopping", "unknown", "unresponsive", "control-unavailable"):
            with self.subTest(extra_status=extra_status):
                records = [self.service_record(), self.service_record("b", extra_status)]
                code, _output, _discover, _inspect, show = self.run_pair(["pair"], records)
                self.assertEqual(code, 2)
                show.assert_not_called()

    def test_pair_requires_one_responsive_running_owner(self):
        records = [self.service_record(status=status) for status in (
            "starting", "stopping", "unknown", "unresponsive", "control-unavailable")]
        records.append(dict(self.service_record(), responsive=False))
        for record in records:
            with self.subTest(record=record):
                code, _output, _discover, _inspect, show = self.run_pair(["pair"], [record])
                self.assertEqual(code, 2)
                show.assert_not_called()

    def test_pair_without_live_managed_service_returns_no_code(self):
        for records in ([], [self.service_record(status="stopped")],
                        [self.service_record(status="unmanaged")]):
            with self.subTest(records=records):
                code, _output, _discover, _inspect, show = self.run_pair(["pair"], records)
                self.assertEqual(code, 1)
                show.assert_not_called()

    def test_pair_request_failure_is_not_success_or_an_automatic_retry(self):
        code, _output, _discover, _inspect, show = self.run_pair(
            ["pair"], [self.service_record()], pairing_result=2)
        self.assertEqual(code, 2)
        self.assertEqual(show.call_count, 1)

    def test_pairing_request_is_exclusive_including_port_zero(self):
        for flag in (("--check",), ("--open",), ("--port", "0"),
                     ("--connections", "selection.json"), ("--local-only",)):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(command, "show_pairing_code") as show, self.assertRaises(SystemExit):
                command.main(["--pairing-code", "access.json", *flag], root=self.root)
            show.assert_not_called()

    def test_pairing_request_never_enters_startup_or_profile_checks(self):
        with patch.object(command, "show_pairing_code", return_value=0) as show, \
                patch.object(command, "plan_for", side_effect=AssertionError("no launch plan")), \
                patch.object(command, "inspect_prerequisites", side_effect=AssertionError("no profile check")), \
                patch.object(command.os, "execv", side_effect=AssertionError("no subprocess")):
            self.assertEqual(command.main(["--pairing-code", "private run/access.json"], root=self.root), 0)
        show.assert_called_once_with(Path("private run/access.json"), root=self.root)

    def test_pairing_code_refuses_logs_before_reading_or_requesting(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                patch("botainer_dashboard.pairing_control.request_pairing_code") as request:
            self.assertEqual(command.show_pairing_code(self.root / "absent.json"), 2)
        request.assert_not_called()

    def test_explicit_terminal_request_displays_only_confirmed_code_and_origin(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        output = Terminal()
        result = {"url": "http://127.0.0.1:50432", "token": "S" * 43, "expiresAt": 2000000000.0}
        with contextlib.redirect_stdout(output), \
                patch("botainer_dashboard.pairing_control.request_pairing_code", return_value=result) as request, \
                patch.object(command.os, "execv", side_effect=AssertionError("no service")):
            self.assertEqual(command.show_pairing_code(Path("specific/access.json")), 0)
        request.assert_called_once_with(Path("specific/access.json"))
        self.assertIn("One-time pairing code: " + result["token"], output.getvalue())
        self.assertIn("Pair this browser at: " + result["url"], output.getvalue())
        self.assertNotIn("?", output.getvalue())
        output = Terminal()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()), \
                patch("botainer_dashboard.pairing_control.request_pairing_code", side_effect=TimeoutError("no reply")):
            self.assertEqual(command.show_pairing_code(Path("specific/access.json")), 2)
        self.assertEqual(output.getvalue(), "")

    def test_default_opens_saved_selection_without_discovering_trial_profiles(self):
        self.profile(".local/cluster-workspace/research-historical.json")
        self.profile()
        plan = self.plan()
        self.assertFalse(plan.local)
        self.assertEqual(plan.profiles, ())
        self.assertEqual(plan.connections, self.root / command.DEFAULT_CONNECTIONS)
        self.assertEqual(plan.config, self.root / ".local/config.json")
        self.assertNotIn("--workspace", plan.argv())
        self.assertNotIn("--cluster-profile", plan.argv())
        self.assertIn("--connections", plan.argv())
        self.assertFalse(plan.connections.exists())

    def test_explicit_repeatable_profiles_replace_default_and_keep_arguments_literal(self):
        self.profile()
        first = self.profile("private files/first profile.json")
        other = dict(profile_data(), id="other")
        second = self.profile("private files/second;literal.json", other)
        plan = self.plan("--cluster-profile", str(first), "--cluster-profile", str(second), "--port", "0", "--open")
        self.assertEqual(plan.profiles, (first, second))
        self.assertEqual(plan.argv(), [str(plan.interpreter), "-I", "-B", str(self.root / "tools/run_dashboard.py"),
            "--workspace", "--cluster-profile", str(first), "--cluster-profile", str(second),
            "--config", str(self.root / ".local/config.json"), "--port", "0", "--open"])

    def test_local_and_cluster_only_exclude_unselected_backend(self):
        selected = self.profile()
        self.assertEqual(self.plan("--local-only").profiles, ())
        self.assertFalse(self.plan("--cluster-only", "--cluster-profile", str(selected)).local)
        self.assertEqual(self.plan("--cluster-only", "--cluster-profile", str(selected)).profiles, (selected,))
        selected.unlink()
        with self.assertRaisesRegex(ValueError, "no cluster profile"):
            self.plan("--cluster-only")

    def test_installed_profiles_exclude_implicit_trial_and_preserve_literal_paths(self):
        self.profile()
        local = self.root / 'private/local profile.json'
        remote = self.root / 'private/remote profile.json'
        plan = self.plan('--local-profile', str(local), '--remote-profile', str(remote))
        self.assertEqual(plan.profiles, ())
        self.assertEqual(plan.local_profile, local)
        self.assertEqual(plan.remote_profiles, (remote,))
        self.assertNotIn('--workspace', plan.argv())
        self.assertNotIn('--cluster-profile', plan.argv())
        self.assertIn(str(local), plan.argv())
        self.assertIn(str(remote), plan.argv())
        for flag in ('--local-only', '--cluster-only', '--native-cli'):
            with self.assertRaisesRegex(ValueError, 'cannot be mixed'):
                self.plan('--local-profile', str(local), flag)

    def test_native_cli_is_explicit_local_selection_without_installation(self):
        plan = self.plan("--local-only", "--native-cli")
        self.assertTrue(plan.native_cli)
        self.assertEqual(plan.profiles, ())
        self.assertIn("--native-cli", plan.argv())
        self.assertIn("--workspace", plan.argv())

    def test_host_only_never_selects_implicit_container_or_cluster_trial(self):
        self.profile()
        host = self.root / 'private/host agents.json'
        plan = self.plan('--host-profile', str(host))
        self.assertFalse(plan.local)
        self.assertEqual(plan.profiles, ())
        self.assertEqual(plan.host_profiles, (host,))
        self.assertNotIn('--workspace', plan.argv())
        self.assertNotIn('--cluster-profile', plan.argv())
        self.assertIn('--host-profile', plan.argv())
        for flag in ('--local-only', '--cluster-only', '--native-cli'):
            with self.assertRaisesRegex(ValueError, 'cannot be mixed'):
                self.plan('--host-profile', str(host), flag)
        with self.assertRaisesRegex(ValueError, 'distinct host-agent'):
            self.plan('--host-profile', str(host), '--host-profile', str(host))
        self.assertNotIn("--native-cli", self.plan("--local-only").argv())
        self.profile()
        with self.assertRaisesRegex(ValueError, "requires a local workspace"):
            self.plan("--native-cli", "--cluster-only")

    def test_contradictory_duplicate_and_invalid_selection_refuse(self):
        selected = self.profile()
        cases = (("--local-only", "--cluster-profile", str(selected)),
                 ("--cluster-profile", str(selected), "--cluster-profile", str(selected)),
                 ("--port", "-1"), ("--port", "65536"))
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.plan(*args)

    def test_check_is_read_only_and_never_opens_browser_or_executes_transport(self):
        self.profile()
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(command.os, "execv") as execute, \
                patch("subprocess.Popen", side_effect=AssertionError("no subprocess")), \
                patch("socket.socket", side_effect=AssertionError("no socket")):
            self.assertEqual(command.main(["--check", "--open"], root=self.root), 0)
        execute.assert_not_called()
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertIn("Not checked:", output.getvalue())
        self.assertIn("No service, browser, Docker or SSH connection was started", output.getvalue())

    def test_missing_runtime_refuses_without_install_or_exec(self):
        self.plan().interpreter.unlink()
        with contextlib.redirect_stdout(io.StringIO()), patch.object(command.os, "execv") as execute:
            self.assertEqual(command.main([], root=self.root), 2)
        execute.assert_not_called()

    def test_distributed_vendor_assets_work_without_private_asset_tree(self):
        for name in command.VENDOR_ASSETS:
            self.write("frontend/vendor/" + name, "distributed fixture")
            (self.root / ".local/frontend/vendor" / name).unlink()
        self.assertTrue(all(ok for ok, _ in command.inspect_prerequisites(self.plan())))

    def test_incomplete_public_and_private_asset_trees_are_not_combined(self):
        for name in command.VENDOR_ASSETS[:2]:
            self.write("frontend/vendor/" + name, "distributed fixture")
            (self.root / ".local/frontend/vendor" / name).unlink()
        checks = command.inspect_prerequisites(self.plan())
        self.assertTrue(any(not ok and "terminal assets" in message for ok, message in checks))

    def test_runtime_cannot_redirect_outside_approved_prefix(self):
        interpreter = self.plan().interpreter
        interpreter.unlink()
        interpreter.symlink_to(sys.executable)
        self.assertFalse(command.inspect_prerequisites(self.plan())[0][0])

    def test_broken_explicit_profile_is_not_silently_omitted(self):
        path = self.root / ".local/cluster-workspace/example.json"
        path.parent.mkdir(mode=0o700, parents=True)
        path.symlink_to(self.root / "missing.json")
        plan = self.plan("--cluster-profile", str(path))
        self.assertEqual(plan.profiles, (path,))
        self.assertFalse(all(ok for ok, _ in command.inspect_prerequisites(plan)))

    def test_broken_default_saved_selection_fails_without_trial_fallback(self):
        self.profile()
        path = self.root / command.DEFAULT_CONNECTIONS
        path.parent.mkdir(mode=0o700, parents=True)
        path.symlink_to(self.root / "missing.json")
        plan = self.plan()
        self.assertEqual(plan.connections, path)
        self.assertEqual(plan.profiles, ())
        self.assertFalse(plan.local)
        self.assertFalse(all(ok for ok, _ in command.inspect_prerequisites(plan)))

    def test_profile_permissions_and_duplicate_identity_are_checked(self):
        first = self.profile()
        first.chmod(0o644)
        self.assertFalse(all(ok for ok, _ in command.inspect_prerequisites(self.plan("--cluster-profile", str(first)))))
        first.chmod(0o600)
        second = self.profile(".local/cluster-workspace/duplicate.json")
        checks = command.inspect_prerequisites(self.plan("--cluster-profile", str(first), "--cluster-profile", str(second)))
        self.assertTrue(any(not ok and "duplicated" in message for ok, message in checks))

    def test_start_replaces_process_with_exact_prepared_runtime_argv(self):
        plan = self.plan("--local-only", "--open")
        with contextlib.redirect_stdout(io.StringIO()), patch.object(command.os, "execv") as execute:
            command.main(["--local-only", "--open"], root=self.root)
        execute.assert_called_once_with(str(plan.interpreter), plan.argv())

    def test_local_identity_is_reserved_even_in_cluster_only_mode(self):
        selected = self.profile(value=dict(profile_data(), id="local"))
        checks = command.inspect_prerequisites(self.plan("--cluster-only", "--cluster-profile", str(selected)))
        self.assertTrue(any(not ok and "reserved" in message for ok, message in checks))

    def test_cluster_only_does_not_require_local_preparation_record(self):
        selected = self.profile()
        (self.root / ".local/reviews/disposable-runtime-plan.json").unlink()
        self.assertTrue(all(ok for ok, _ in command.inspect_prerequisites(self.plan("--cluster-only", "--cluster-profile", str(selected)))))
        self.assertTrue(all(ok for ok, _ in command.inspect_prerequisites(self.plan())))
        self.assertFalse(all(ok for ok, _ in command.inspect_prerequisites(self.plan("--local-only"))))


if __name__ == "__main__":
    unittest.main()
