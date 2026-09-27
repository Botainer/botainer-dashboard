"""Framed attachment boundaries using local pipes/fakes; no SSH or real PTY."""
import importlib.util
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import select
import signal
import stat
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

from botainer_dashboard import cluster_attach as client

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("cluster_attach_supervisor", ROOT / "tools/cluster_attach_supervisor.py")
remote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remote)

FAKE_SERVER = r'''
import json,os,struct,sys
header=struct.Struct('!cI'); pending=bytearray(); nonce=None
mode=sys.argv[1]
def send(kind,data=b''):
    message=header.pack(kind,len(data))+data
    while message: message=message[os.write(1,message):]
while True:
    chunk=os.read(0,65536)
    if not chunk: break
    pending.extend(chunk)
    while len(pending)>=header.size:
        kind,size=header.unpack_from(pending)
        if len(pending)<header.size+size: break
        data=bytes(pending[header.size:header.size+size]); del pending[:header.size+size]
        if kind==b'H':
            value=json.loads(data); nonce=value['nonce']
            send(b'A',json.dumps({'nonce':nonce if mode!='wrong-ready' else 'wrong','owner':{'test':'owner'},
                'executable_verification':'procfs-executable-and-pinned-screen-sha256'}).encode())
        elif kind==b'I': send(b'O',data)
        elif kind==b'R': send(b'O',('SIZE %s %s'%struct.unpack('!HH',data)).encode())
        elif kind==b'P': send(b'O',b'HEARTBEAT')
        elif kind==b'C':
            send(b'D',json.dumps({'nonce':nonce if mode!='wrong-close' else 'wrong','cleanup':'confirmed'}).encode())
            raise SystemExit(0)
'''


class ClusterAttachmentTests(unittest.TestCase):
    def test_policy_requires_exact_qualified_binary_and_all_system_configs(self):
        binary, config = b"reviewed Screen binary", b"reviewed administrator config"
        digest, config_digest = (hashlib.sha256(value).hexdigest() for value in (binary, config))
        info = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o2755, st_uid=0, st_size=len(binary))
        files = {"/usr/bin/screen": (binary, info), "/etc/screenrc": (config, info),
                 "/usr/local/etc/screenrc": None}
        policy = {digest: {"/etc/screenrc": config_digest, "/usr/local/etc/screenrc": None}}
        with patch.object(remote, "QUALIFIED_SCREEN_POLICIES", policy), \
                patch.object(remote, "read_administrator_file", side_effect=lambda name, **_kw: files[name]):
            self.assertEqual(remote.verify_screen_policy()["system_screenrc_sha256"], policy[digest])
            for name, changed in (("/usr/bin/screen", (b"different executable", info)),
                                  ("/etc/screenrc", (b"bindkey unsafe command", info)),
                                  ("/etc/screenrc", None),
                                  ("/usr/local/etc/screenrc", (b"", info))):
                previous = files[name]
                files[name] = changed
                with self.subTest(name=name, changed=changed), self.assertRaisesRegex(remote.Refused, "qualification"):
                    remote.verify_screen_policy()
                files[name] = previous

    def test_administrator_reader_bounds_data_and_rejects_mutation(self):
        directory = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        def regular(size=3, *, mtime=1, mode=0o644, uid=0):
            return types.SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=uid, st_size=size,
                                         st_dev=1, st_ino=2, st_mtime_ns=mtime, st_ctime_ns=1)
        for before, after, message in ((regular(4), regular(4), "size limit"),
                                       (regular(), regular(mtime=2), "changed while reading"),
                                       (regular(mode=0o664), regular(), "root-owned"),
                                       (regular(uid=1000), regular(), "root-owned")):
            stream = MagicMock()
            stream.__enter__.return_value = stream
            stream.fileno.return_value = 12
            stream.read.return_value = b"abc"
            with self.subTest(message=message), patch.object(remote.os, "open", side_effect=[10, 11, 12]) as opened, \
                    patch.object(remote.os, "close"), patch.object(remote.os, "fdopen", return_value=stream), \
                    patch.object(remote.os, "fstat", side_effect=[directory, directory, before, after]), \
                    self.assertRaisesRegex(remote.Refused, message):
                remote.read_administrator_file("/etc/screenrc", limit=3)
            for call in opened.call_args_list:
                self.assertTrue(call.args[1] & os.O_NOFOLLOW)

    def test_administrator_reader_absence_is_distinct_from_untrusted_or_symlink_path(self):
        good = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        shared = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o775, st_uid=0)
        with patch.object(remote.os, "open", side_effect=[10, FileNotFoundError()]), \
                patch.object(remote.os, "close"), patch.object(remote.os, "fstat", return_value=good):
            self.assertIsNone(remote.read_administrator_file("/etc/screenrc", limit=3, absent_ok=True))
        for error in (PermissionError(), OSError("symlink refused")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(remote.os, "open", side_effect=[10, error]), \
                    patch.object(remote.os, "close"), patch.object(remote.os, "fstat", return_value=good), \
                    self.assertRaises(type(error)):
                remote.read_administrator_file("/etc/screenrc", limit=3, absent_ok=True)
        with patch.object(remote.os, "open", return_value=10), patch.object(remote.os, "close"), \
                patch.object(remote.os, "fstat", return_value=shared), self.assertRaisesRegex(remote.Refused, "root-owned"):
            remote.read_administrator_file("/etc/screenrc", limit=3, absent_ok=True)

    def test_policy_only_cli_exits_before_any_terminal_setup(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["supervisor", "--root", "/private/botainer-dashboard-test-one", "--check-policy"]), \
                patch.object(remote, "check_trial_root") as root_check, \
                patch.object(remote, "verify_screen_policy", return_value={"qualification": "tested"}), \
                patch.object(remote.signal, "signal", side_effect=AssertionError("terminal signal setup")), \
                patch.object(remote, "writer_lock", side_effect=AssertionError("terminal lock")), \
                patch.object(remote, "supervise", side_effect=AssertionError("terminal spawn")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(remote.main(), 0)
        root_check.assert_called_once_with(Path("/private/botainer-dashboard-test-one"))
        self.assertEqual(json.loads(output.getvalue()), {"qualification": "tested"})
        for args in (("--check-policy", "--owner-json", "{}"), ()):
            with self.subTest(args=args), patch.object(sys, "argv", ["supervisor", "--root", "/root", *args]), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                remote.main()
            self.assertEqual(raised.exception.code, 2)

    def decode_screen_keys(self, data):
        """Minimal escape ^Aa command-state model: other prefix keys are commands."""
        literal = bytearray()
        commands = []
        pending_prefix = False
        for value in data:
            if pending_prefix:
                if value == ord("a"):
                    literal.append(1)
                else:
                    commands.append(value)
                pending_prefix = False
            elif value == 1:
                pending_prefix = True
            else:
                literal.append(value)
        return bytes(literal), commands, pending_prefix

    def test_screen_host_command_prefixes_are_literal_even_across_input_frames(self):
        cases = (b"\x01c", b"\x01:exec /bin/sh\n", b"\x01!", b"\x01\x01c", bytes(range(256)))
        for data in cases:
            for cut in range(len(data) + 1):
                with self.subTest(data=data[:20], cut=cut):
                    encoded = remote.literalize_screen_input(data[:cut]) + remote.literalize_screen_input(data[cut:])
                    self.assertEqual(self.decode_screen_keys(encoded), (data, [], False))

    def test_prefix_expansion_is_bounded_before_queued_bytes_change(self):
        data = b"\x01" * remote.CHUNK
        self.assertEqual(len(remote.literalize_screen_input(data)), 2 * remote.CHUNK)
        pending = bytearray(b"x" * (remote.MAX_QUEUE - 1))
        original = bytes(pending)
        with self.assertRaisesRegex(remote.Refused, "after Screen-prefix quoting"):
            remote.queue_screen_input(pending, b"\x01")
        self.assertEqual(bytes(pending), original)
        remote.queue_screen_input(pending, b"x")
        self.assertEqual(len(pending), remote.MAX_QUEUE)

    def connection(self, mode="normal"):
        attachment = client.FramedSshAttachment(
            [sys.executable, "-I", "-S", "-c", FAKE_SERVER, mode],
            cwd=ROOT, env={"PATH": "/usr/bin:/bin"}, expected_owner={"test": "owner"})
        self.addCleanup(lambda: self.cleanup(attachment))
        return attachment

    def cleanup(self, attachment):
        try:
            attachment.close()
        except OSError:
            pass

    def read_bytes(self, attachment, length):
        result = bytearray()
        deadline = time.monotonic() + 3
        while len(result) < length and time.monotonic() < deadline:
            data = attachment.read(length - len(result), timeout=0.1)
            if data == b"":
                break
            if data:
                result.extend(data)
        return bytes(result)

    def test_opaque_terminal_bytes_resize_and_verified_close(self):
        attachment = self.connection()
        data = b"binary\0\xff" + client._frame(b"C")
        self.assertEqual(attachment.write(data), len(data))
        self.assertEqual(self.read_bytes(attachment, len(data)), data)
        self.assertEqual(attachment.executable_verification, "procfs-executable-and-pinned-screen-sha256")
        attachment.resize(132, 41)
        self.assertEqual(self.read_bytes(attachment, len(b"SIZE 132 41")), b"SIZE 132 41")
        attachment.close()
        self.assertTrue(attachment.closed)
        self.assertTrue(attachment._cleanup)
        with self.assertRaises(BrokenPipeError):
            attachment.write(b"must not replay")

    def test_idle_connection_emits_heartbeat_without_terminal_input(self):
        with patch.object(client, "HEARTBEAT_SECONDS", 0.05):
            attachment = self.connection()
            self.assertEqual(self.read_bytes(attachment, len(b"HEARTBEAT")), b"HEARTBEAT")
            attachment.close()

    def test_wrong_ready_identity_and_wrong_cleanup_nonce_fail_closed(self):
        with self.assertRaisesRegex(OSError, "handshake identity mismatch"):
            self.connection("wrong-ready")
        attachment = self.connection("wrong-close")
        with self.assertRaisesRegex(OSError, "cleanup receipt identity mismatch"):
            attachment.close()
        self.assertFalse(attachment._cleanup)

    def test_pre_supervisor_stderr_after_stdout_eof_is_preserved_privately(self):
        program = "import os,time;os.close(1);time.sleep(.03);os.write(2,b'private dispatcher: operation lock busy\\n');raise SystemExit(23)"
        with self.assertRaises(client.AttachmentStartError) as raised:
            client.FramedSshAttachment([sys.executable, "-I", "-S", "-c", program],
                                      cwd=ROOT, env={"PATH": "/usr/bin:/bin"})
        failure = raised.exception
        self.assertNotIn("private dispatcher", str(failure))
        self.assertEqual(failure.diagnostics["stderr"], b"private dispatcher: operation lock busy\n")
        self.assertEqual(failure.diagnostics["passenger_returncode"], 23)
        self.assertTrue(failure.diagnostics["passenger_finished"])
        self.assertFalse(failure.diagnostics["remote_cleanup_receipt"])

    def test_startup_diagnostic_capture_remains_bounded_on_excessive_stderr(self):
        program = "import os;os.write(2,b'x'*70000);raise SystemExit(2)"
        with self.assertRaises(client.AttachmentStartError) as raised:
            client.FramedSshAttachment([sys.executable, "-I", "-S", "-c", program],
                                      cwd=ROOT, env={"PATH": "/usr/bin:/bin"})
        diagnostics = raised.exception.diagnostics
        self.assertEqual(diagnostics["stderr"], b"x" * client.MAX_DIAGNOSTICS)
        self.assertTrue(diagnostics["stderr_truncated"])
        self.assertFalse(diagnostics["remote_cleanup_receipt"])

    def test_fragmented_frames_are_bounded_and_unknown_controls_are_rejected(self):
        payload = remote.frame(b"I", b"payload")
        pending = bytearray()
        for byte in payload[:-1]:
            pending.append(byte)
            self.assertEqual(remote.take_frames(pending), [])
        pending.append(payload[-1])
        self.assertEqual(remote.take_frames(pending), [(b"I", b"payload")])
        with self.assertRaises(remote.Refused):
            remote.take_frames(bytearray(remote.HEADER.pack(b"I", remote.MAX_FRAME + 1)))
        with self.assertRaises(OSError):
            client._take_frames(bytearray(client.HEADER.pack(b"O", client.MAX_FRAME + 1)))

    def test_nonblocking_writer_lock_does_not_take_over_existing_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = remote.writer_lock(root)
            try:
                with self.assertRaisesRegex(remote.Refused, "another dashboard attachment"):
                    remote.writer_lock(root)
            finally:
                os.close(first)
            second = remote.writer_lock(root)
            os.close(second)

    def test_writer_lock_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "untouched"
            target.write_bytes(b"preserve")
            (root / "attach-writer.lock").symlink_to(target)
            with self.assertRaises(OSError):
                remote.writer_lock(root)
            self.assertEqual(target.read_bytes(), b"preserve")

    def supervised_fake(self, *, tail=b"", keep_input_open=True, attachment_error=None,
                        detached_error=None, fragmented_output=False):
        incoming, sender = os.pipe()
        receiver, outgoing = os.pipe()
        master, slave = os.pipe()
        keep_slave = os.dup(slave)  # Keep the fake PTY alive through lease expiry.
        nonce = "a" * 32
        initial = remote.frame(b"H", json.dumps({"cols": 80, "rows": 24, "nonce": nonce}).encode())
        os.write(sender, initial + tail)
        if not keep_input_open:
            os.close(sender)
            sender = None
        process = types.SimpleNamespace(pid=123456, returncode=None)
        def wait(timeout):
            process.returncode = 0
            return 0
        process.wait = Mock(side_effect=wait)
        detached = Mock(side_effect=detached_error)
        attached = Mock(side_effect=attachment_error)
        real_write = os.write
        def short_write(descriptor, data):
            return real_write(descriptor, data[:3] if fragmented_output and descriptor == outgoing else data)
        started = time.monotonic()
        with patch.object(remote.os, "openpty", return_value=(master, slave)), \
                patch.object(remote.tty, "setraw"), patch.object(remote.fcntl, "ioctl"), \
                patch.object(remote.subprocess, "Popen", return_value=process) as spawn, \
                patch.object(remote.os, "killpg") as kill, patch.object(remote.os, "write", side_effect=short_write):
            result = remote.supervise(["/usr/bin/screen", "-r", "42.botainer-123"], {}, {"pid": 42},
                                      verify_attached=attached, verify_detached=detached,
                                      executable_verification="procfs-executable-and-pinned-screen-sha256",
                                      input_fd=incoming, output_fd=outgoing,
                                      lease_seconds=0.12)
        elapsed = time.monotonic() - started
        os.close(outgoing)
        received = bytearray(os.read(receiver, 65536))
        for descriptor in (incoming, receiver, keep_slave, sender):
            if descriptor is not None:
                os.close(descriptor)
        return result, remote.take_frames(received), spawn, kill, detached, elapsed

    def test_failed_screen_attachment_never_emits_ready(self):
        result, frames, _spawn, kill, detached, _elapsed = self.supervised_fake(
            attachment_error=remote.Refused("Screen already attached"))
        self.assertEqual(result, 2)
        self.assertFalse(any(kind == b"A" for kind, _data in frames))
        self.assertTrue(any(kind == b"E" for kind, _data in frames))
        kill.assert_called_once_with(123456, signal.SIGHUP)
        detached.assert_called_once_with()

    def test_partial_output_writes_keep_frame_boundaries_through_close(self):
        result, frames, _spawn, _kill, _detached, _elapsed = self.supervised_fake(
            tail=remote.frame(b"C"), fragmented_output=True)
        self.assertEqual(result, 0)
        self.assertEqual([kind for kind, _data in frames], [b"A", b"D"])
        self.assertEqual(json.loads(frames[-1][1]), {"nonce": "a" * 32, "cleanup": "confirmed"})

    def test_unconfirmed_detachment_never_emits_cleanup_success(self):
        result, frames, _spawn, _kill, _detached, _elapsed = self.supervised_fake(
            tail=remote.frame(b"C"), detached_error=remote.Refused("owner still Attached"))
        self.assertEqual(result, 2)
        self.assertFalse(any(kind == b"D" for kind, _data in frames))
        self.assertTrue(any(kind == b"E" and b"cleanup unconfirmed" in data for kind, data in frames))

    def test_lost_client_with_open_mux_channel_expires_and_cleans_only_attacher(self):
        result, frames, spawn, kill, detached, elapsed = self.supervised_fake()
        self.assertEqual(result, 2)
        self.assertLess(elapsed, 1)
        kill.assert_called_once_with(123456, signal.SIGHUP)
        self.assertNotEqual(kill.call_args.args[0], 42)  # The durable server is untouched.
        detached.assert_called_once_with()
        self.assertTrue(any(kind == b"E" and b"heartbeat expired" in data for kind, data in frames))
        self.assertTrue(any(kind == b"D" for kind, _data in frames))
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])

    def test_explicit_close_and_eof_clean_client_without_waiting_for_lease(self):
        for tail, open_pipe in ((remote.frame(b"C"), True), (b"", False)):
            with self.subTest(explicit=bool(tail)):
                result, frames, _spawn, kill, detached, elapsed = self.supervised_fake(tail=tail, keep_input_open=open_pipe)
                self.assertEqual(result, 0)
                self.assertLess(elapsed, 0.1)
                self.assertTrue(any(kind == b"D" for kind, _data in frames))
                kill.assert_called_once_with(123456, signal.SIGHUP)
                detached.assert_called_once_with()

    def test_malformed_control_cleans_owned_client_and_never_becomes_terminal_input(self):
        result, frames, _spawn, kill, detached, _elapsed = self.supervised_fake(tail=remote.frame(b"P", b"invalid"))
        self.assertEqual(result, 2)
        self.assertTrue(any(kind == b"E" and b"invalid heartbeat" in data for kind, data in frames))
        kill.assert_called_once_with(123456, signal.SIGHUP)
        detached.assert_called_once_with()

    def test_restricted_procfs_fallback_requires_pinned_trusted_setgid_binary(self):
        digest = "b" * 64
        binary = Path("/system/screen")
        process = MagicMock()
        process.__truediv__.return_value.resolve.side_effect = PermissionError("setgid procfs restriction")
        mode = stat.S_IFREG | stat.S_ISGID | 0o755
        with patch.object(remote.Path, "resolve", return_value=binary), \
                patch.object(remote, "screen_binary_identity", return_value=(digest, mode)):
            self.assertEqual(remote.verify_screen_executable(process, {"screen_sha256": digest}),
                             "restricted-procfs-pinned-root-setgid-screen")
            with self.assertRaisesRegex(remote.Refused, "pinned Screen executable changed"):
                remote.verify_screen_executable(process, {"screen_sha256": "a" * 64})
        with patch.object(remote.Path, "resolve", return_value=binary), \
                patch.object(remote, "screen_binary_identity", return_value=(digest, stat.S_IFREG | 0o755)):
            with self.assertRaisesRegex(remote.Refused, "trusted setgid"):
                remote.verify_screen_executable(process, {"screen_sha256": digest})

    def test_procfs_mismatch_or_other_io_error_never_uses_fallback(self):
        digest = "b" * 64
        binary = Path("/system/screen")
        process = MagicMock()
        mode = stat.S_IFREG | stat.S_ISGID | 0o755
        with patch.object(remote.Path, "resolve", return_value=binary), \
                patch.object(remote, "screen_binary_identity", return_value=(digest, mode)):
            process.__truediv__.return_value.resolve.return_value = Path("/different/program")
            with self.assertRaisesRegex(remote.Refused, "not the Screen server"):
                remote.verify_screen_executable(process, {"screen_sha256": digest})
            for error in (FileNotFoundError("gone"), OSError("procfs read failed")):
                process.__truediv__.return_value.resolve.side_effect = error
                with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                    remote.verify_screen_executable(process, {"screen_sha256": digest})

    def test_screen_binary_and_parent_metadata_require_root_and_no_shared_write(self):
        for directory in (False, True):
            kind = stat.S_IFDIR if directory else stat.S_IFREG
            valid = types.SimpleNamespace(st_mode=kind | 0o755, st_uid=0, st_size=4096)
            remote.trusted_screen_metadata(valid, directory=directory)
            for mode, uid in ((kind | 0o775, 0), (kind | 0o757, 0), (kind | 0o755, 1000),
                              (stat.S_IFLNK | 0o755, 0)):
                info = types.SimpleNamespace(st_mode=mode, st_uid=uid, st_size=4096)
                with self.subTest(directory=directory, mode=mode, uid=uid), self.assertRaises(remote.Refused):
                    remote.trusted_screen_metadata(info, directory=directory)


if __name__ == "__main__":
    unittest.main()
