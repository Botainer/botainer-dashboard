"""Offline remote-workspace boundaries; no SSH, scheduler, or Botainer run."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import uuid

PATH = Path(__file__).resolve().parents[2] / "tools/cluster_workspace_helper.py"
SPEC = importlib.util.spec_from_file_location("cluster_workspace_helper_test", PATH)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)

# Representative nonforking Screen owner branch. The executable payload is
# synthetic: no upstream import, scheduler, container, or SSH runs.
NATIVE_OWNER = '    exec screen -D -m -S "$SCREEN_SID" '
SITE_OWNER = '    exec /usr/bin/screen -c "$HOME/.screenrc" -D -m -S "$SCREEN_SID" '
LEGACY_LOOP = ('    while screen -ls 2>/dev/null | grep -qE "[0-9]+\\.${SCREEN_SID}\\b"; do\n'
               '        sleep 30\n    done\n')


def rendered_batch(body='/bin/false'):
    return ('#!/bin/bash\n#SBATCH --job-name=botainer-12345678\n'
            'set -euo pipefail\n'
            'if command -v screen >/dev/null 2>&1; then\n'
            '    SCREEN_SID="botainer-${SLURM_JOB_ID:?SLURM_JOB_ID unset; '
            'sbatch script must run under Slurm}"\n'
            '    # -D -m: create detached WITHOUT forking, so this process\n'
            '    # is the session owner and the job lives exactly as long\n'
            '    # as the session does.\n'
            + NATIVE_OWNER + body + '\n'
            'else\n'
            '    echo "screen unavailable: attachment unavailable" >&2\n'
            '    exec ' + body + '\nfi\n')


class ClusterBatchQualificationTests(unittest.TestCase):
    def qualify(self, script):
        return helper.qualify_batch_script(script, '12345678-1234-1234-1234-123456789abc',
                                           '0123456789abcdef')

    def test_native_owner_is_retained_once_and_only_site_restrictions_are_added(self):
        body = ('apptainer exec --containall --cleanenv --no-home --net --network none '
                '--bind /private/state:/state:ro --bind \'/project with spaces:/workspace\' '
                '--env "SLURM_TMPDIR=${SLURM_TMPDIR:-/tmp}" /cache/shell.sif '
                '/bin/sh -c \'printf "%s\\n" "literal $(command)"\'')
        original = rendered_batch(body)
        name, script = self.qualify(original)
        self.assertEqual(name, 'botainer-dashboard-test-0123456789abcdef')
        self.assertEqual(script.count(SITE_OWNER), 1)
        self.assertNotIn(NATIVE_OWNER, script)
        self.assertNotIn('while screen -ls', script)
        self.assertIn('test -x /usr/bin/screen', script)
        self.assertIn('--check-policy || exit 78', script)
        self.assertIn('#SBATCH --job-name='+name+'\n', script)
        # Both native branches keep their complete caged command unchanged.
        self.assertIn(SITE_OWNER+body+'\n', script)
        self.assertIn('    exec '+body+'\nfi\n', script)
        self.assertEqual(script.count(body), original.count(body))
        # No lifecycle code was inserted or deleted; strip only the named site
        # qualification changes and the exact upstream render is restored.
        restored = script.replace(SITE_OWNER, NATIVE_OWNER).replace(
            '#SBATCH --job-name='+name+'\n', '#SBATCH --job-name=botainer-12345678\n')
        restored = ''.join(line for line in restored.splitlines(keepends=True)
                           if not line.startswith(('test -x /usr/bin/screen',
                               'test "$(command -v screen)"',
                               '/usr/bin/python3 -I -S "$HOME/cluster_attach_supervisor.py"')))
        self.assertEqual(restored, original)

    def test_legacy_mixed_duplicate_or_absent_owner_is_refused(self):
        native = rendered_batch()
        cases = {
            'old-fork-and-poll': native.replace(NATIVE_OWNER, '    screen -dmS "$SCREEN_SID" ').replace('else\n', LEGACY_LOOP+'else\n'),
            'old-fork-only': native.replace(NATIVE_OWNER, '    screen -dmS "$SCREEN_SID" '),
            'native-plus-old-poll': native.replace('else\n', LEGACY_LOOP+'else\n'),
            'native-plus-old-owner': native.replace('else\n', '    screen -dmS other /bin/false\nelse\n'),
            'duplicate-owner': native.replace('else\n', NATIVE_OWNER+'/bin/false\nelse\n'),
            'duplicate-owner-different-indent': native.replace('else\n', NATIVE_OWNER.lstrip()+'/bin/false\nelse\n'),
            'no-owner': native.replace(NATIVE_OWNER, '    exec '),
            'wrong-identity': native.replace(' -S "$SCREEN_SID" ', ' -S "different" '),
        }
        for name, script in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Screen|screen'):
                self.qualify(script)


class ClusterWorkspaceHelperTests(unittest.TestCase):
    def test_same_request_returns_original_result_without_composition_or_dispatch(self):
        request = str(uuid.uuid4())
        for stage in ("preparing", "dispatch-claimed", "submitted"):
            record = {"request_id": request, "stage": stage}
            registry = {"requests": {request: record}}
            h = Mock()
            with patch.object(helper, "bounded") as run, patch.object(helper, "atomic") as atomic:
                result = helper.start(Path("/unused"), h, {}, registry, request)
            self.assertIs(result, record)
            h.compose.assert_not_called()
            run.assert_not_called()
            atomic.assert_not_called()

    def test_unresolved_attempt_fences_a_different_request_without_retry(self):
        registry = {"requests": {str(uuid.uuid4()): {"stage": "dispatch-claimed"}}}
        h = Mock()
        with patch.object(helper, "bounded") as run, patch.object(helper, "atomic") as atomic:
            with self.assertRaisesRegex(RuntimeError, "active or unresolved"):
                helper.start(Path("/unused"), h, {}, registry, str(uuid.uuid4()))
        h.compose.assert_not_called()
        run.assert_not_called()
        atomic.assert_not_called()

    def test_unobservable_job_fences_next_start(self):
        registry = {"requests": {str(uuid.uuid4()): {"stage": "submitted", "job_id": "123"}}}
        h = Mock()
        with patch.object(helper, "observe", side_effect=RuntimeError("site unavailable")), \
                patch.object(helper, "bounded") as run:
            with self.assertRaisesRegex(RuntimeError, "site unavailable"):
                helper.start(Path("/unused"), h, {}, registry, str(uuid.uuid4()))
        h.compose.assert_not_called()
        run.assert_not_called()

    def test_composition_failure_leaves_durable_intent_and_never_repeats(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = str(uuid.uuid4())
            registry = {"requests": {}}
            h = types.SimpleNamespace(compose=Mock(side_effect=RuntimeError("invalid config")))
            with patch.object(helper, "module", return_value=types.SimpleNamespace(tool_paths=lambda: {})):
                with self.assertRaisesRegex(RuntimeError, "invalid config"):
                    helper.start(root, h, {}, registry, request)
            saved = helper.read_json(root / "gui-registry.json")
            self.assertEqual(saved["requests"][request]["stage"], "preparing")
            helper.start(root, h, {}, saved, request)
            self.assertEqual(h.compose.call_count, 1)
            with self.assertRaisesRegex(RuntimeError, "active or unresolved"):
                helper.start(root, h, {}, saved, str(uuid.uuid4()))

    def test_start_request_must_be_canonical_uuid(self):
        for request in ("anything", "../file", "00000000000000000000000000000000", None):
            with self.subTest(request=request), self.assertRaises((RuntimeError, ValueError)):
                helper.start(Path("/unused"), Mock(), {}, {"requests": {}}, request)

    def preflight(self, directory):
        root = Path(directory).resolve()
        sid = "0123456789abcdef"
        project = "12345678-1234-1234-1234-123456789abc"
        evidence = root / "preflights" / sid
        evidence.mkdir(parents=True)
        session = root / "state/state" / project / "sessions" / sid
        session.mkdir(parents=True)
        (root / "gui-sessions").mkdir()
        files = {"spec.json": b"{}", "plan.json": b"{}", "argv.json": b"[]",
                 "unsubmitted.sbatch": rendered_batch().encode()}
        for name, content in files.items():
            (evidence / name).write_bytes(content)
        value = {"session_id": sid, "project_uuid": project, "session_directory": str(session),
                 "evidence_directory": str(evidence),
                 "artifacts_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
        h = types.SimpleNamespace(compose=Mock(return_value=value),
                                  new_file=Mock(side_effect=AssertionError("must refuse before writing launch script")))
        return root, value, h, {"project_uuid": project}

    def test_legacy_source_is_refused_before_launch_script_or_submission(self):
        with tempfile.TemporaryDirectory() as folder:
            root, value, h, descriptor = self.preflight(folder)
            script = Path(value['evidence_directory']) / 'unsubmitted.sbatch'
            script.write_text(rendered_batch().replace(NATIVE_OWNER, '    screen -dmS "$SCREEN_SID" ')
                              .replace('else\n', LEGACY_LOOP+'else\n'))
            value['artifacts_sha256']['unsubmitted.sbatch'] = helper.digest(script)
            registry = {'requests': {}}
            request = str(uuid.uuid4())
            with patch.object(helper, 'module'), patch.object(helper, 'bounded') as run:
                with self.assertRaisesRegex(RuntimeError, 'nonforking Screen owner'):
                    helper.start(root, h, descriptor, registry, request)
            h.new_file.assert_not_called()
            run.assert_not_called()
            saved = helper.read_json(root / 'gui-registry.json')
            self.assertEqual(saved['requests'][request]['stage'], 'preparing')
            self.assertEqual(list((root / 'gui-sessions').iterdir()), [])

    def test_invalid_preflight_identity_or_artifact_set_refuses_before_launch_write(self):
        for variant in ("missing-script", "extra-file", "empty-set", "wrong-project", "outside-session", "wrong-evidence"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as folder:
                root, value, h, descriptor = self.preflight(folder)
                if variant == "missing-script":
                    del value["artifacts_sha256"]["unsubmitted.sbatch"]
                elif variant == "extra-file":
                    (Path(value["evidence_directory"]) / "extra.sh").write_bytes(b"extra")
                    value["artifacts_sha256"]["extra.sh"] = hashlib.sha256(b"extra").hexdigest()
                elif variant == "empty-set":
                    value["artifacts_sha256"] = {}
                elif variant == "wrong-project":
                    value["project_uuid"] = "other-project"
                elif variant == "outside-session":
                    value["session_directory"] = str(root / "outside")
                else:
                    value["evidence_directory"] = str(root / "elsewhere")
                with patch.object(helper, "module") as module, patch.object(helper, "bounded") as run:
                    with self.assertRaises(RuntimeError):
                        helper.start(root, h, descriptor, {"requests": {}}, str(uuid.uuid4()))
                h.new_file.assert_not_called()
                run.assert_not_called()

    def test_ambiguous_submission_is_claimed_once_and_disables_automatic_requeue(self):
        for failure in (RuntimeError("lost submission acknowledgement"), "not an exact job ID"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root, value, h, descriptor = self.preflight(folder)
                fake_tool = root / "scheduler-tool"
                fake_tool.write_bytes(b"offline placeholder; never executed")
                tools = {name: str(fake_tool) for name in ("sbatch", "srun", "scontrol", "scancel")}
                h.new_file = lambda path, content, _mode: path.write_text(content)
                h.safe_environment = lambda *_args: {}
                request = str(uuid.uuid4())
                registry = {"requests": {}}

                def submit(argv, _env):
                    saved = helper.read_json(root / "gui-registry.json")
                    self.assertEqual(saved["requests"][request]["stage"], "dispatch-claimed")
                    self.assertIn("--no-requeue", argv)
                    script = Path(argv[-1]).read_text()
                    self.assertIn('exec /usr/bin/screen -c "$HOME/.screenrc" -D -m -S "$SCREEN_SID" /bin/false', script)
                    self.assertNotIn('while screen -ls', script)
                    self.assertIn('--check-policy || exit 78', script)
                    if isinstance(failure, Exception):
                        raise failure
                    return failure

                with patch.object(helper, "module", return_value=types.SimpleNamespace(tool_paths=lambda: tools)), \
                        patch.object(helper, "bounded", side_effect=submit) as run:
                    expected = 'lost submission acknowledgement' if isinstance(failure, Exception) else 'ambiguous submission reply'
                    with self.assertRaisesRegex(RuntimeError, expected):
                        helper.start(root, h, descriptor, registry, request)
                    saved = helper.read_json(root / "gui-registry.json")
                    self.assertNotIn("job_id", saved["requests"][request])
                    self.assertEqual(helper.start(root, h, descriptor, saved, request)["stage"], "dispatch-claimed")
                    with self.assertRaisesRegex(RuntimeError, "active or unresolved"):
                        helper.start(root, h, descriptor, saved, str(uuid.uuid4()))
                    self.assertEqual(run.call_count, 1)
                self.assertEqual(h.compose.call_count, 1)

    def test_job_identity_change_is_refused_before_owner_or_cancellation(self):
        root = Path("/home/example/botainer-dashboard-test-example")
        record = {"job_id": "123", "job_name": "trial", "tools": {"scontrol": "/usr/bin/scontrol"},
                  "tool_sha256": {"scontrol": "fixed"}}
        probe = types.SimpleNamespace(parse_job=lambda _: self.fields,
            job_identity=lambda fields: {k:fields[k] for k in ("JobId", "UserId", "JobName", "WorkDir", "SubmitTime")})
        base = {"JobId": "123", "UserId": "test(100)", "JobName": "trial", "WorkDir": str(root / "project"),
                "SubmitTime": "2026-09-21T00:00:00", "JobState": "RUNNING"}
        for key, value in (("JobId", "999"), ("UserId", "test(999)"), ("JobName", "other"),
                           ("WorkDir", "/other/project")):
            self.fields = {**base, key: value}
            with self.subTest(key=key), patch.object(helper.os, "getuid", return_value=100), \
                    patch.object(helper, "digest", return_value="fixed"), \
                    patch.object(helper, "environment", return_value={}), \
                    patch.object(helper, "module", return_value=probe), \
                    patch.object(helper, "bounded", return_value="ignored"):
                with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                    helper.observe(root, Mock(), {}, record)
            self.assertNotIn("status", record)

    def test_changed_submit_time_refuses_reused_scheduler_id(self):
        fields = {"JobId": "123", "UserId": f"test({os.getuid()})", "JobName": "trial",
                  "WorkDir": "/home/example/trial/project", "SubmitTime": "new"}
        identity = lambda f: {k:f[k] for k in ("JobId", "UserId", "JobName", "WorkDir", "SubmitTime")}
        record = {"job_id": "123", "job_name": "trial", "tools": {"scontrol": "/usr/bin/scontrol"},
                  "tool_sha256": {"scontrol": "fixed"}, "job_identity": identity({**fields, "SubmitTime": "old"})}
        probe = types.SimpleNamespace(parse_job=lambda _: fields, job_identity=identity)
        with patch.object(helper, "digest", return_value="fixed"), \
                patch.object(helper, "environment", return_value={}), \
                patch.object(helper, "module", return_value=probe), patch.object(helper, "bounded", return_value="ignored"):
            with self.assertRaisesRegex(RuntimeError, "allocation identity changed"):
                helper.observe(Path("/home/example/trial"), Mock(), {}, record)

    def test_file_scope_rejects_traversal_secret_names_symlinks_and_hardlinks(self):
        for value in ("../outside", "/absolute", "a//b", ".botainer/config.yaml", "tokens.json", "a/key.pem", "a\\b"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                helper.safe_parts(value)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "project").mkdir()
            outside = root / "outside.txt"
            outside.write_text("not project content")
            (root / "project/link.txt").symlink_to(outside)
            with self.assertRaises(OSError):
                helper.files(root, "link.txt", read=True)
            os.link(outside, root / "project/hard.txt")
            with self.assertRaises(RuntimeError):
                helper.files(root, "hard.txt", read=True)

    def test_text_preview_enforces_size_and_binary_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "project").mkdir()
            path = root / "project/notes.txt"
            for content in (b"x" * 65537, b"binary\0content"):
                path.write_bytes(content)
                with self.assertRaisesRegex(RuntimeError, "preview limit"):
                    helper.files(root, "notes.txt", read=True)
            path.write_text("hello\n")
            self.assertEqual(helper.files(root, "notes.txt", read=True)["text"], "hello\n")

    def test_directory_scan_is_bounded_even_when_every_name_is_excluded(self):
        class Entries:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __iter__(self):
                for index in range(10001):
                    yield types.SimpleNamespace(name=f".excluded-{index}")
                raise AssertionError("unbounded directory scan")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "project").mkdir()
            with patch.object(helper.os, "scandir", return_value=Entries()):
                with self.assertRaisesRegex(RuntimeError, "limit"):
                    helper.files(root, "")

    def test_config_route_refuses_parent_symlink_and_hardlinked_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "project").mkdir()
            outside = root / "elsewhere"
            outside.mkdir()
            (outside / "config.yaml").write_text("private host file")
            (root / "project/.botainer").symlink_to(outside, target_is_directory=True)
            args = ["helper", "config", "--root", str(root), "--data", '{"fingerprint":"fixed"}']
            with patch.object(helper, "load", return_value=(Mock(), {}, {"profile_fingerprint":"fixed"})), \
                    patch.object(sys, "argv", args):
                with self.assertRaises((OSError, RuntimeError)):
                    helper.main()
            (root / "project/.botainer").unlink()
            (root / "project/.botainer").mkdir()
            os.link(outside / "config.yaml", root / "project/.botainer/config.yaml")
            with patch.object(helper, "load", return_value=(Mock(), {}, {"profile_fingerprint":"fixed"})), \
                    patch.object(sys, "argv", args):
                with self.assertRaises((OSError, RuntimeError)):
                    helper.main()

    def test_config_preview_refuses_oversized_text_and_retains_exact_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "project/.botainer").mkdir(parents=True)
            path = root / "project/.botainer/config.yaml"
            path.write_bytes(b"x" * 65537)
            with self.assertRaisesRegex(RuntimeError, "preview limit"):
                helper.read_config(root)
            raw = b'{"runtime":"apptainer"}\n'
            path.write_bytes(raw)
            result = helper.read_config(root)
            self.assertFalse(result["writable"])
            self.assertEqual(result["text"], raw.decode())
            self.assertEqual(result["revision"], hashlib.sha256(raw).hexdigest())


class ClusterWorkspaceBoundedCommandTests(unittest.TestCase):
    def run_python(self, code, timeout=2):
        return helper.bounded([sys.executable, "-I", "-B", "-c", code], {}, timeout=timeout)

    def test_command_returns_stdout_and_surfaces_nonzero_failure(self):
        self.assertEqual(self.run_python("print('hello')"), "hello\n")
        with self.assertRaisesRegex(RuntimeError, "scheduler command failed: expected"):
            self.run_python("import sys; print('expected', file=sys.stderr); sys.exit(3)")

    def test_output_flood_is_refused_without_waiting_for_process_exit(self):
        with self.assertRaisesRegex(RuntimeError, "output limit"):
            self.run_python("import os; os.write(1, b'x' * 300000)")

    def test_timeout_is_unknown_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "timed out; outcome unknown"):
            self.run_python("import time; time.sleep(10)", timeout=0.2)


if __name__ == "__main__":
    unittest.main()
