"""Opt-in POSIX integration: real PTYs and deterministic Python children.

No Botainer, shell, container, network, agent, package or host-service operations.
These checks do not prove that a real attachment preserves a durable runtime.
"""

import errno
import json
import os
from pathlib import Path
import select
import signal
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

if os.name == "posix":
    from botainer_dashboard.pty_bridge import MAX_IO_BYTES, PtyAttachment


@unittest.skipUnless(os.name == "posix", "POSIX PTYs required")
class PtyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="dashboard-pty-")
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name).resolve()

    def child(self, source, *arguments, **kwargs):
        attachment = PtyAttachment(
            [sys.executable, "-I", "-S", "-c", source, *arguments],
            cwd=self.cwd, env={"TERM": "xterm-256color", "DASHBOARD_PTY_TEST": "explicit"},
            **kwargs,
        )
        self.addCleanup(attachment.close)
        return attachment

    def read_until(self, attachment, predicate, *, limit=MAX_IO_BYTES, timeout=3):
        result = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = attachment.read(min(4096, limit - len(result)), timeout=0.05)
            if chunk == b"":
                break
            if chunk:
                result.extend(chunk)
                self.assertLessEqual(len(result), limit)
            if predicate(bytes(result)):
                return bytes(result)
            self.assertLess(len(result), limit, "child exceeded the test's output bound")
        self.fail(f"child did not provide the expected bounded response: {bytes(result)!r}")

    def write_all(self, attachment, data):
        deadline = time.monotonic() + 3
        offset = 0
        while offset < len(data) and time.monotonic() < deadline:
            offset += attachment.write(data[offset:offset + MAX_IO_BYTES])
            if offset < len(data):
                select.select([], [attachment.fileno()], [], 0.05)
        self.assertEqual(offset, len(data), "bounded test input was not accepted")

    def test_raw_bytes_and_controlling_tty_are_real(self):
        attachment = self.child(
            "import os; fd=os.open('/dev/tty',os.O_RDWR); os.close(fd);"
            "os.write(1,b'READY');"
            "data=os.read(0,1024); os.write(1,data);"
            "os.read(0,1)"
        )
        self.assertEqual(self.read_until(attachment, lambda value: value == b"READY"), b"READY")
        payload = b"\x00\x03\x04\x1b[A\r\n\xff\xf0\x9f\x98\x80"
        self.write_all(attachment, payload)
        echoed = self.read_until(attachment, lambda value: len(value) >= len(payload))
        self.assertEqual(echoed, payload)
        self.assertIsNone(attachment.read(timeout=0))

    def test_initial_terminal_newlines_then_child_raw_mode_preserves_bytes(self):
        attachment = self.child(
            "import os,tty;os.write(1,b'BANNER\\n');tty.setraw(0);"
            "os.write(1,b'RAW');data=os.read(0,1024);os.write(1,data);os.read(0,1)",
            terminal_newlines=True,
        )
        self.assertEqual(self.read_until(attachment, lambda value: value.endswith(b"RAW")), b"BANNER\r\nRAW")
        payload = b"\x00\x03\n\r\n\x1b[A\xff"
        self.write_all(attachment, payload)
        self.assertEqual(self.read_until(attachment, lambda value: len(value) >= len(payload)), payload)

    def test_cooked_prompt_edits_echoes_and_waits_for_enter_then_accepts_eof(self):
        attachment = self.child(
            "import os;os.write(1,b'PROMPT> ');line=os.read(0,1024);"
            "os.write(1,b'ANSWER:'+repr(line).encode()+b'\\nEOF> ');"
            "tail=os.read(0,1024);os.write(1,b'END:'+repr(tail).encode()+b'\\n')",
            cooked=True,
        )
        self.read_until(attachment, lambda value: value.endswith(b'PROMPT> '))
        self.write_all(attachment, b'yx\x7f')
        echoed = self.read_until(attachment, lambda value: b'yx' in value)
        self.assertNotIn(b'ANSWER:', echoed)
        self.assertIsNone(attachment.read(timeout=.05))
        self.write_all(attachment, b'\r')
        answer = self.read_until(attachment, lambda value: value.endswith(b'EOF> '))
        self.assertIn(b"ANSWER:b'y\\n'\r\n", answer)
        self.write_all(attachment, b'\x04')
        self.assertIn(b"END:b''\r\n", self.read_until(attachment, lambda value: b'END:' in value))

    def test_cooked_ctrl_c_signals_the_prompt_instead_of_becoming_input(self):
        attachment = self.child(
            "import os,signal;signal.signal(signal.SIGINT,lambda *_:(os.write(1,b'INTERRUPTED\\n'),exit(0)));"
            "os.write(1,b'PROMPT> ');os.read(0,1024);os.write(1,b'UNEXPECTED INPUT')",
            cooked=True,
        )
        self.read_until(attachment, lambda value: value.endswith(b'PROMPT> '))
        self.write_all(attachment, b'\x03')
        result = self.read_until(attachment, lambda value: b'INTERRUPTED\r\n' in value)
        self.assertNotIn(b'UNEXPECTED INPUT', result)

    def test_denied_kill_allows_bounded_natural_exit_and_real_group_absence(self):
        attachment = self.child(
            "import os,time;os.write(1,b'READY');os.read(0,1);time.sleep(0.06)"
        )
        self.read_until(attachment, lambda value: value == b"READY")
        real_killpg = os.killpg
        def exiting_group(pid, sig):
            if sig == signal.SIGKILL:
                raise PermissionError(errno.EPERM, "fixture natural-exit race")
            return real_killpg(pid, sig)
        self.write_all(attachment, b"q")
        started = time.monotonic()
        with patch("botainer_dashboard.pty_bridge.os.killpg", side_effect=exiting_group):
            self.assertEqual(attachment.close(timeout=1), 0)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertTrue(attachment.closed)
        with self.assertRaises(ChildProcessError):
            os.waitpid(attachment.pid, os.WNOHANG)

    def test_live_kill_denial_preserves_master_and_refusal(self):
        attachment = self.child("import os;os.write(1,b'READY');os.read(0,1)")
        self.read_until(attachment, lambda value: value == b"READY")
        with patch("botainer_dashboard.pty_bridge.os.killpg", side_effect=PermissionError(errno.EPERM, "fixture denied")):
            with self.assertRaises(PermissionError):
                attachment.close(timeout=0.05)
        self.assertFalse(attachment.closed)
        self.assertIsNone(attachment._process.returncode)
        # A real subsequent signal is allowed only while the original PID remains
        # unreaped; this reaps the deterministic fixture and releases the master.
        self.assertEqual(attachment.close(timeout=1), -signal.SIGKILL)

    def test_reaped_leader_with_uncertain_descendants_never_reports_clean_or_kills_again(self):
        attachment = self.child("import os;os.write(1,b'READY');os.read(0,1)")
        self.read_until(attachment, lambda value: value == b"READY")
        self.write_all(attachment, b"q")
        signals = []
        def uncertain_group(pid, sig):
            signals.append(sig)
            if sig == signal.SIGKILL:
                raise PermissionError(errno.EPERM, "fixture denied")
            return None  # Probe cannot establish that every descendant is gone.
        with patch("botainer_dashboard.pty_bridge.os.killpg", side_effect=uncertain_group):
            with self.assertRaises(PermissionError):
                attachment.close(timeout=1)
            self.assertEqual(attachment._process.returncode, 0)
            self.assertFalse(attachment.closed)
            with self.assertRaises(PermissionError):
                attachment.close(timeout=1)
        self.assertEqual(signals, [signal.SIGKILL, 0, 0])
        # The real OS now confirms this fixture group is absent; no SIGKILL after
        # reap is needed or permitted to complete cleanup.
        self.assertEqual(attachment.close(timeout=1), 0)

    def test_initial_and_updated_dimensions_reach_child(self):
        attachment = self.child(
            "import fcntl,os,struct,termios;"
            "size=lambda:struct.unpack('HHHH',fcntl.ioctl(0,termios.TIOCGWINSZ,b'\\0'*8))[:2];"
            "os.write(1,('%d,%d\\n'%size()).encode());"
            "os.read(0,1);os.write(1,('%d,%d\\n'%size()).encode());os.read(0,1)",
            cols=91, rows=28,
        )
        self.assertEqual(self.read_until(attachment, lambda value: value.endswith(b"\n")), b"28,91\n")
        attachment.resize(120, 42)
        self.write_all(attachment, b"?")
        self.assertEqual(self.read_until(attachment, lambda value: value.endswith(b"\n")), b"42,120\n")

    def test_explicit_environment_cwd_and_argv_do_not_go_through_a_shell(self):
        literal = "$(touch SHOULD_NOT_EXIST); --flag"
        attachment = self.child(
            "import json,os,sys;"
            "os.write(1,json.dumps([sys.argv[1],os.getcwd(),os.environ.get('DASHBOARD_PTY_TEST'),"
            "os.environ.get('UNREVIEWED_PTY_SECRET'),os.getsid(0),os.getpgrp()]).encode())",
            literal,
        )
        data = self.read_until(attachment, lambda value: value.endswith(b"]"))
        args, cwd, selected, secret, sid, pgid = json.loads(data)
        self.assertEqual(args, literal)
        self.assertEqual(cwd, str(self.cwd))
        self.assertEqual(selected, "explicit")
        self.assertIsNone(secret)
        self.assertEqual((sid, pgid), (attachment.pid, attachment.pid))
        self.assertFalse((self.cwd / "SHOULD_NOT_EXIST").exists())

    def test_timeout_eof_and_bounded_reads(self):
        attachment = self.child("import os;os.write(1,b'x'*9000)")
        output = bytearray()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            chunk = attachment.read(127, timeout=0.05)
            if chunk == b"":
                break
            if chunk is not None:
                self.assertLessEqual(len(chunk), 127)
                output.extend(chunk)
        else:
            self.fail("EOF was not observed within the test deadline")
        self.assertEqual(output, b"x" * 9000)
        self.assertEqual(attachment.read(), b"")
        with self.assertRaises(BrokenPipeError):
            attachment.write(b"not queued after EOF")
        self.assertEqual(attachment.close(), 0)

    def test_close_is_bounded_idempotent_and_reaps_attachment_process(self):
        attachment = self.child(
            "import os,signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "os.write(1,b'READY');os.read(0,1)"
        )
        self.read_until(attachment, lambda value: value == b"READY")
        started = time.monotonic()
        code = attachment.close(timeout=1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(code, -signal.SIGKILL)
        self.assertEqual(attachment.close(), code)
        self.assertTrue(attachment.closed)
        with self.assertRaises(ChildProcessError):
            os.waitpid(attachment.pid, os.WNOHANG)
        for operation in (attachment.fileno, attachment.read,
                          lambda: attachment.write(b"x"), lambda: attachment.resize(80, 24)):
            with self.assertRaises(ValueError):
                operation()

    def test_invalid_limits_refuse_without_blocking_or_allocating_unbounded_buffers(self):
        attachment = self.child("import os;os.write(1,b'READY');os.read(0,1)")
        self.read_until(attachment, lambda value: value == b"READY")
        for size in (0, -1, MAX_IO_BYTES + 1, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                attachment.read(size)
        for timeout in (-1, 6, float('inf'), float('nan'), True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                attachment.read(timeout=timeout)
        for dimensions in ((0, 24), (80, 65536), (True, 24)):
            with self.subTest(dimensions=dimensions), self.assertRaises(ValueError):
                attachment.resize(*dimensions)
        for data in ("text", b"x" * (MAX_IO_BYTES + 1)):
            with self.assertRaises(ValueError):
                attachment.write(data)

    def test_relative_or_missing_executable_is_rejected_without_fallback(self):
        for argv in (("python3", "-c", "pass"), (str(self.cwd / "missing"),), "echo test"):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                PtyAttachment(argv, cwd=self.cwd, env={})


if __name__ == "__main__":
    unittest.main()
