from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest

from botainer_dashboard.operations import (
    InvalidTransition, OperationConflict, OperationStore,
)


FINGERPRINT = "sha256:" + "a" * 64
CHANGED_FINGERPRINT = "sha256:" + "b" * 64
SESSION_ID = "0123456789abcdef"


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "operations.sqlite3"
        self.store = OperationStore(self.database)
        self.addCleanup(self.store.close)
        self.intent = dict(operation_id="request-1", scope_key="site-account-1",
                           checkout="/workspace/project with spaces", context_id="stable",
                           config_revision="sha256:abc123", intent_fingerprint=FINGERPRINT)

    def prepare(self, **changes):
        return self.store.prepare(**dict(self.intent, **changes))

    def test_repeat_request_cannot_dispatch_twice_even_from_another_connection(self):
        first = self.prepare()
        self.assertEqual(first, self.prepare())
        with OperationStore(self.database) as other:
            self.assertTrue(self.store.claim(first.operation_id, expected_fingerprint=FINGERPRINT))
            self.assertFalse(other.claim(first.operation_id, expected_fingerprint=FINGERPRINT))
            self.assertEqual(other.prepare(**self.intent).state, "dispatching")

    def test_changed_request_content_and_parallel_installation_cannot_bypass_pending_guard(self):
        self.prepare()
        with self.assertRaises(OperationConflict):
            self.prepare(config_revision="sha256:changed")
        with self.assertRaises(OperationConflict):
            self.prepare(intent_fingerprint=CHANGED_FINGERPRINT)
        with self.assertRaises(OperationConflict):
            self.prepare(operation_id="request-2", context_id="development")
        self.assertEqual(len(self.store.unresolved()), 1)
        # Different host/account is a different checkout namespace.
        self.prepare(operation_id="request-3", scope_key="another-account")
        self.assertEqual(len(self.store.unresolved()), 2)

    def test_restart_after_dispatch_blocks_retry_until_runtime_reconciled(self):
        self.prepare()
        self.store.claim("request-1", expected_fingerprint=FINGERPRINT)
        with OperationStore(self.database) as restarted:
            self.assertEqual(restarted.mark_interrupted_dispatches(), 1)
            self.assertFalse(restarted.claim("request-1", expected_fingerprint=FINGERPRINT))
            self.assertEqual(restarted.get("request-1").state, "unknown")
            with self.assertRaises(OperationConflict):
                restarted.prepare(**dict(self.intent, operation_id="retry"))
            result = restarted.record_success("request-1", session_id=SESSION_ID)
            self.assertEqual(result.state, "succeeded")
            self.assertEqual(restarted.record_success("request-1", session_id=result.session_id), result)
            self.assertEqual(restarted.unresolved(), [])
            self.assertFalse(restarted.claim("request-1", expected_fingerprint=FINGERPRINT))

    def test_timeout_is_unknown_not_permission_to_start_again(self):
        self.prepare()
        self.store.claim("request-1", expected_fingerprint=FINGERPRINT)
        self.store.record_unknown("request-1", result_code="acknowledgement-timeout")
        with self.assertRaises(OperationConflict):
            self.prepare(operation_id="request-2")
        self.assertFalse(self.store.claim("request-1", expected_fingerprint=FINGERPRINT))

    def test_confirmed_failure_allows_new_intent_without_overwriting_history(self):
        self.prepare()
        self.store.claim("request-1", expected_fingerprint=FINGERPRINT)
        self.store.record_failure("request-1", result_code="validation-refused")
        self.prepare(operation_id="request-2")
        self.assertEqual(self.store.get("request-1").state, "failed")
        self.assertEqual(self.store.get("request-2").state, "prepared")
        with self.assertRaises(InvalidTransition):
            self.store.record_success("request-1", session_id=SESSION_ID)

    def test_undispatched_intent_cannot_claim_success(self):
        self.prepare()
        with self.assertRaises(InvalidTransition):
            self.store.record_success("request-1", session_id=SESSION_ID)
        self.assertEqual(self.store.get("request-1").state, "prepared")

    def test_future_schema_unrelated_database_and_symlink_are_refused(self):
        for version in (0, 1, 999):
            path = Path(self.directory.name) / f"unknown-{version}.sqlite3"
            with sqlite3.connect(str(path)) as db:
                db.execute("CREATE TABLE unrelated (value TEXT)")
                db.execute(f"PRAGMA user_version={version}")
            with self.assertRaises(ValueError):
                OperationStore(path)
        link = Path(self.directory.name) / "link.sqlite3"
        link.symlink_to(self.database)
        with self.assertRaises(ValueError):
            OperationStore(link)

    def test_existing_version_cannot_hide_missing_or_weakened_concurrency_index(self):
        changes = (
            "DROP INDEX one_pending_launch_per_checkout",
            """CREATE INDEX one_pending_launch_per_checkout
               ON operations(scope_key, checkout)
               WHERE state IN ('prepared','dispatching','unknown')""",
            """CREATE UNIQUE INDEX one_pending_launch_per_checkout
               ON operations(scope_key, checkout) WHERE state='prepared'""",
        )
        for number, replacement in enumerate(changes):
            path = Path(self.directory.name) / f"bad-index-{number}.sqlite3"
            with OperationStore(path):
                pass
            with sqlite3.connect(str(path)) as db:
                db.execute("DROP INDEX one_pending_launch_per_checkout")
                if not replacement.startswith("DROP"):
                    db.execute(replacement)
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                OperationStore(path)

    def test_existing_version_cannot_hide_changed_table_or_unexpected_trigger(self):
        changes = (
            "ALTER TABLE operations ADD COLUMN unreviewed TEXT",
            "CREATE TABLE sqliteXnot_internal (unreviewed TEXT)",
            """CREATE TRIGGER unreviewed AFTER UPDATE ON operations
               BEGIN DELETE FROM operations WHERE state='unknown'; END""",
        )
        for number, change in enumerate(changes):
            path = Path(self.directory.name) / f"bad-table-{number}.sqlite3"
            with OperationStore(path):
                pass
            with sqlite3.connect(str(path)) as db:
                db.execute(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                OperationStore(path)

    def test_changed_snapshot_cannot_be_claimed_or_reused(self):
        self.prepare()
        with self.assertRaises(OperationConflict):
            self.store.claim("request-1", expected_fingerprint=CHANGED_FINGERPRINT)
        self.assertEqual(self.store.get("request-1").state, "prepared")
        self.assertTrue(self.store.claim("request-1", expected_fingerprint=FINGERPRINT))
        with self.assertRaises(OperationConflict):
            self.store.claim("request-1", expected_fingerprint=CHANGED_FINGERPRINT)

    def test_only_unclaimed_work_can_be_cancelled_and_replaced(self):
        self.prepare()
        cancelled = self.store.cancel_prepared("request-1")
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(self.store.cancel_prepared("request-1"), cancelled)
        self.assertFalse(self.store.claim("request-1", expected_fingerprint=FINGERPRINT))
        self.prepare(operation_id="replacement")
        self.store.claim("replacement", expected_fingerprint=FINGERPRINT)
        with self.assertRaises(InvalidTransition):
            self.store.cancel_prepared("replacement")
        self.store.record_unknown("replacement", result_code="acknowledgement-lost")
        with self.assertRaises(InvalidTransition):
            self.store.cancel_prepared("replacement")
        with self.assertRaises(InvalidTransition):
            self.store.record_success("request-1", session_id=SESSION_ID)

    def test_competing_claims_on_independent_connections_have_one_winner(self):
        self.prepare()
        barrier = Barrier(2)

        def claim():
            with OperationStore(self.database) as store:
                barrier.wait(timeout=5)
                return store.claim("request-1", expected_fingerprint=FINGERPRINT)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(claim) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.store.get("request-1").state, "dispatching")

    def test_competing_prepares_across_installations_have_one_winner(self):
        barrier = Barrier(2)

        def prepare(number):
            with OperationStore(self.database) as store:
                barrier.wait(timeout=5)
                try:
                    store.prepare(**dict(self.intent, operation_id=f"concurrent-{number}",
                                         context_id=f"installation-{number}"))
                except OperationConflict:
                    return "conflict"
                return "prepared"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(prepare, number) for number in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(results), ["conflict", "prepared"])
        self.assertEqual(len(self.store.unresolved()), 1)

    def test_cancellation_racing_claim_cannot_cancel_an_owned_dispatch(self):
        self.prepare()
        barrier = Barrier(2)

        def claim():
            with OperationStore(self.database) as store:
                barrier.wait(timeout=5)
                return store.claim("request-1", expected_fingerprint=FINGERPRINT)

        def cancel():
            with OperationStore(self.database) as store:
                barrier.wait(timeout=5)
                try:
                    return store.cancel_prepared("request-1").state
                except InvalidTransition:
                    return "refused"

        with ThreadPoolExecutor(max_workers=2) as executor:
            claim_result = executor.submit(claim)
            cancel_result = executor.submit(cancel)
            result = (claim_result.result(timeout=10), cancel_result.result(timeout=10))
        self.assertIn(result, ((True, "refused"), (False, "cancelled")))

    def test_close_reopen_preserves_ambiguity_fingerprint_and_session_result(self):
        self.prepare()
        self.store.claim("request-1", expected_fingerprint=FINGERPRINT)
        self.store.close()
        with OperationStore(self.database) as reopened:
            original = reopened.get("request-1")
            self.assertEqual(original.state, "dispatching")
            self.assertEqual(original.intent_fingerprint, FINGERPRINT)
            self.assertEqual(reopened.mark_interrupted_dispatches(), 1)
        with OperationStore(self.database) as reconciler:
            self.assertEqual(reconciler.get("request-1").state, "unknown")
            self.assertFalse(reconciler.claim("request-1", expected_fingerprint=FINGERPRINT))
            reconciler.record_success("request-1", session_id=SESSION_ID)
        with OperationStore(self.database) as reader:
            self.assertEqual(reader.get("request-1").session_id, SESSION_ID)
            self.assertEqual(reader.get("request-1").state, "succeeded")

    def test_success_requires_a_full_botainer_session_id_not_container_or_job_handle(self):
        self.prepare()
        self.store.claim("request-1", expected_fingerprint=FINGERPRINT)
        for value in ("12345", "a" * 64, "abcd", "--all", "a" * 16 + "\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.record_success("request-1", session_id=value)
        self.assertEqual(self.store.get("request-1").state, "dispatching")

    def test_private_file_and_bounded_metadata_inputs(self):
        self.assertEqual(os.stat(self.database).st_mode & 0o777, 0o600)
        for field, bad in (("operation_id", "bad\nvalue"),
                           ("scope_key", "secret body with spaces"),
                           ("checkout", "/work/../project"),
                           ("checkout", "relative/path"),
                           ("config_revision", "x" * 257),
                           ("intent_fingerprint", "sha256:short"),
                           ("intent_fingerprint", "sha256:" + "A" * 64)):
            with self.subTest(field=field, value=bad):
                with self.assertRaises(ValueError):
                    self.prepare(**{field: bad})
        self.assertEqual(self.store.unresolved(), [])


if __name__ == "__main__":
    unittest.main()
