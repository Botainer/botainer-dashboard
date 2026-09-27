import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.inventory import InventoryError
from botainer_dashboard.ordinary_owner import OrdinaryCliOwner
from botainer_dashboard.pairing import read_private_json


OP = "a" * 32


class FakeClient:
    def __init__(self, argv, **kwargs):
        self.argv, self.kwargs = argv, kwargs
        self.close_calls = 0
        self.fail_close = False
        self.data = None

    def read(self, *_args, **_kwargs):
        result, self.data = self.data, None
        return result

    def write(self, data):
        return len(data)

    def resize(self, *_args):
        pass

    def close(self, **_kwargs):
        self.close_calls += 1
        if self.fail_close:
            raise PermissionError("uncertain client cleanup")
        return 0


class FakeOwner(OrdinaryCliOwner):
    def _socket(self, record):
        self._folder(record)
        return self.fake_socket


class OrdinaryOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oc-", dir="/private/tmp" if Path("/private/tmp").is_dir() else "/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.control = self.root / "o"
        self.control.mkdir(mode=0o700)
        self.binary = self.root / "tool"
        self.binary.write_text("fixture never executed\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.env = {"HOME": str(self.root), "PATH": "/usr/bin:/bin", "TERM": "xterm-256color"}
        self.calls = []
        self.dead = False
        self.exit_code = ""
        self.pid = "1234"
        self.child = "2345"
        self.clients = b""
        self.panes = b"%0\n"
        self.capture_output = b"native warnings\n"
        self.fail_launch = False
        self.owner = self.make_owner()

    def make_owner(self):
        owner = FakeOwner(self.control, self.binary, self.digest, self.env,
                          runner=self.run_tmux, attachment_factory=FakeClient)
        owner.fake_socket = [31, 41]
        return owner

    def run_tmux(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        folder = Path(argv[argv.index("-S") + 1]).parent
        record = read_private_json(folder / "record.json")
        start = argv.index("-f") + 2
        command = argv[start]
        if command == "new-session":
            self.assertNotIn("-N", argv[:start])
            self.assertEqual(record["state"], "unknown")
            self.assertNotIn("owner", record)
            if self.fail_launch:
                raise InventoryError("fixture-reply-lost")
            return b""
        self.assertIn("-N", argv[:start])
        if command == "display-message":
            return (f"{self.pid}\t$0\t@0\t%0\t{self.child}\t1750000000\t{folder / 's'}\t"
                    f"{record['nonce']}\tordinary-{record['id']}\t{int(self.dead)}\t{self.exit_code}\t\t1\n").encode()
        if command == "list-panes":
            return self.panes
        if command == "list-clients":
            return self.clients
        if command == "capture-pane":
            return self.capture_output
        self.fail(f"unexpected command: {command}")

    def launch(self):
        return self.owner.launch(OP, [str(self.binary), "--native-cli"], cwd=self.root, env=self.env)

    def writer(self):
        fd = os.open(self.root / "writer", os.O_WRONLY | os.O_CREAT, 0o600)
        self.addCleanup(lambda: self.close_fd(fd))
        return fd

    @staticmethod
    def close_fd(fd):
        try:
            os.close(fd)
        except OSError:
            pass

    def test_constructing_and_missing_recovery_never_execute(self):
        self.assertEqual(self.calls, [])
        with self.assertRaises((OSError, BackendUnavailable)):
            self.owner.recover(OP)
        self.assertEqual(self.calls, [])

    def test_launch_is_direct_persisted_once_and_recoverable(self):
        record = self.launch()
        self.assertEqual(record["state"], "running")
        self.assertEqual(record["owner"]["socketIdentity"], [31, 41])
        self.assertEqual(record, read_private_json(self.control / OP / "record.json"))
        argv, call = self.calls[0]
        self.assertEqual(argv[-2:], (str(self.binary), "--native-cli"))
        self.assertEqual(call["env"], self.env)
        self.assertEqual(self.make_owner().recover(OP)["owner"], record["owner"])
        with self.assertRaisesRegex(BackendUnavailable, "already-dispatched"):
            self.launch()
        self.assertEqual(sum("new-session" in args for args, _ in self.calls), 1)
        config = (self.control / OP / "tmux.conf").read_text()
        for expected in ("set-clipboard off", "set-titles off", "remain-on-exit on", "automatic-rename off", "history-limit 2000"):
            self.assertIn(expected, config)

    def test_ambiguous_dispatch_stays_unknown_and_is_never_repeated(self):
        self.fail_launch = True
        record = self.launch()
        self.assertEqual(record["state"], "unknown")
        self.assertNotIn("owner", record)
        self.assertEqual(self.make_owner().recover(OP)["code"], "ordinary-owner-unverified")
        with self.assertRaises(BackendUnavailable):
            self.launch()
        self.assertEqual(len(self.calls), 1)

    def test_owner_pid_child_socket_and_extra_panes_are_not_adopted(self):
        record = self.launch()
        for attr, value in (("pid", "9999"), ("child", "8888"), ("panes", b"%0\n%1\n")):
            old = getattr(self, attr)
            setattr(self, attr, value)
            self.assertEqual(self.owner.observe(record)["state"], "unknown")
            with self.assertRaises(BackendUnavailable):
                self.owner.attach(record, self.writer(), 80, 24)
            setattr(self, attr, old)
        self.owner.fake_socket = [31, 42]
        self.assertEqual(self.owner.observe(record)["state"], "unknown")
        self.assertFalse(any("kill-pane" in args or "new-server" in args for args, _ in self.calls))

    def test_changed_config_directory_and_tool_fail_closed(self):
        record = self.launch()
        config = self.control / OP / "tmux.conf"
        text = config.read_text()
        config.write_text(text + "run-shell danger\n")
        self.assertEqual(self.owner.observe(record)["state"], "unknown")
        config.write_text(text)
        folder = self.control / OP
        folder.rename(self.control / "old")
        folder.mkdir(mode=0o700)
        self.assertEqual(self.owner.observe(record)["state"], "unknown")
        folder.rmdir(); (self.control / "old").rename(folder)
        self.binary.write_text("changed executable\n")
        self.assertEqual(self.owner.observe(record)["state"], "unknown")

    def test_root_and_file_symlinks_or_public_permissions_are_refused(self):
        record = self.launch()
        folder = self.control / OP
        config = folder / "tmux.conf"
        config.rename(folder / "original")
        config.symlink_to(folder / "original")
        self.assertEqual(self.owner.observe(record)["state"], "unknown")
        config.unlink(); (folder / "original").rename(config)
        self.control.chmod(0o755)
        self.assertEqual(self.owner.observe(record)["state"], "unknown")
        self.control.chmod(0o700)
        alias = self.root / "alias"
        alias.symlink_to(self.control, target_is_directory=True)
        with self.assertRaises(BackendUnavailable):
            FakeOwner(alias, self.binary, self.digest, self.env)

    def test_attach_has_no_server_creation_and_close_releases_only_client(self):
        record = self.launch()
        fd = self.writer()
        attached = self.owner.attach(record, fd, 90, 28)
        self.assertIn("-N", attached.client.argv)
        self.assertIn("-f", attached.client.argv)
        self.assertEqual(attached.client.argv[-4:], ["attach-session", "-E", "-t", "$0"])
        self.assertTrue(attached.client.kwargs["cooked"])
        self.assertEqual(attached.write(b"y\n"), 2)
        self.assertEqual(attached.close(), 0)
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertEqual(self.owner.observe(record)["state"], "running")
        self.assertFalse(any("kill-pane" in args or "kill-server" in args for args, _ in self.calls))

    def test_uncertain_client_cleanup_retains_writer_lock(self):
        attached = self.owner.attach(self.launch(), self.writer(), 80, 24)
        attached.client.fail_close = True
        with self.assertRaises(PermissionError):
            attached.close()
        self.assertIsNotNone(attached.writer_fd)
        os.fstat(attached.writer_fd)
        attached.client.fail_close = False
        attached.close()

    def test_existing_client_refuses_attach_without_detaching_it(self):
        record = self.launch()
        self.clients = b"3456\n"
        with self.assertRaisesRegex(BackendUnavailable, "already-attached"):
            self.owner.attach(record, self.writer(), 80, 24)
        self.assertFalse(any("detach-client" in args for args, _ in self.calls))

    def test_dead_pane_is_retained_captured_and_not_writable(self):
        record = self.launch()
        attached = self.owner.attach(record, self.writer(), 80, 24)
        self.dead = True; self.exit_code = "2"
        attached.client.data = b"last screen bytes"
        self.assertEqual(attached.read(100), b"last screen bytes")
        self.assertEqual(attached.read(100), b"")
        with self.assertRaises(BrokenPipeError):
            attached.write(b"ignored")
        status = self.owner.observe(record)
        self.assertEqual(status, {"state": "ended", "exitCode": 2, "exitSignal": None})
        self.assertEqual(self.owner.capture(record), b"native warnings\n")
        with self.assertRaisesRegex(BackendUnavailable, "not-running"):
            self.owner.attach(record, self.writer(), 80, 24)
        attached.close()

    def test_running_capture_requires_explicit_handoff_and_is_bounded(self):
        record = self.launch()
        with self.assertRaisesRegex(BackendUnavailable, "not-ended"):
            self.owner.capture(record)
        self.assertEqual(self.owner.capture(record, allow_running=True), self.capture_output)
        self.capture_output = b"a" * 300000
        captured = self.owner.capture(record, max_bytes=100, allow_running=True)
        self.assertEqual(len(captured), 100)
        self.assertTrue(captured.startswith(b"[Earlier terminal output omitted.]"))
        self.assertTrue(captured.endswith(b"a" * 20))

    def test_invalid_launch_arguments_and_inherited_tmux_are_refused_before_dispatch(self):
        for args in ([str(self.binary)], "shell text", [str(self.binary), ";", "bad"], ["relative", "arg"]):
            with self.assertRaises(BackendUnavailable):
                self.owner.launch(OP, args, cwd=self.root, env=self.env)
        for key in ("TMUX", "TMUX_PANE", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"):
            with self.assertRaises(BackendUnavailable):
                self.owner.launch(OP, [str(self.binary), "arg"], cwd=self.root, env={**self.env, key: "bad"})
        self.assertEqual(self.calls, [])
        self.assertFalse((self.control / OP).exists())

    def test_owner_uncertainty_ends_view_without_sending_or_stopping(self):
        attached = self.owner.attach(self.launch(), self.writer(), 80, 24)
        self.pid = "9999"
        with self.assertRaisesRegex(BackendUnavailable, "ordinary-owner-unverified"):
            attached.read(100)
        with self.assertRaises(BackendUnavailable):
            attached.write(b"must not send")
        attached.close()

    def test_socket_must_be_owned_socket_not_symlink_file_or_world_accessible(self):
        record = self.launch()
        real_lstat = Path.lstat
        owner = OrdinaryCliOwner(self.control, self.binary, self.digest, self.env, runner=self.run_tmux)
        socket_path = self.control / OP / "s"
        for mode, uid, accepted in ((stat.S_IFSOCK | 0o660, os.getuid(), True),
                                    (stat.S_IFSOCK | 0o666, os.getuid(), False),
                                    (stat.S_IFSOCK | 0o600, os.getuid() + 1, False),
                                    (stat.S_IFLNK | 0o777, os.getuid(), False),
                                    (stat.S_IFREG | 0o600, os.getuid(), False)):
            info = os.stat_result((mode, 41, 31, 1, uid, os.getgid(), 0, 0, 0, 0))
            def lstat(path):
                return info if path == socket_path else real_lstat(path)
            with patch.object(Path, "lstat", lstat):
                if accepted:
                    self.assertEqual(owner._socket(record), [31, 41])
                else:
                    with self.assertRaises(BackendUnavailable):
                        owner._socket(record)

    def test_recovery_refuses_a_linked_or_wrong_operation_record_without_commands(self):
        self.launch()
        record_path = self.control / OP / "record.json"
        record_path.rename(self.control / OP / "record-original.json")
        record_path.symlink_to(self.control / OP / "record-original.json")
        calls = len(self.calls)
        with self.assertRaisesRegex(BackendUnavailable, "ordinary-owner-unverified"):
            self.owner.recover(OP)
        self.assertEqual(len(self.calls), calls)

    def test_selected_venv_interpreter_link_keeps_its_original_argv_path(self):
        launcher = self.root / "venv-python"
        launcher.symlink_to(self.binary)
        record = self.owner.launch(OP, [str(launcher), "--native-cli"], cwd=self.root, env=self.env)
        self.assertEqual(record["state"], "running")
        self.assertEqual(self.calls[0][0][-2:], (str(launcher), "--native-cli"))


if __name__ == "__main__":
    unittest.main()
