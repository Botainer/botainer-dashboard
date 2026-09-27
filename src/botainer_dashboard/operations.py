"""Durable launch intent and ambiguity tracking; never executes a command.

The dispatcher must claim an operation before spawning. A restarted dispatcher
must reconcile interrupted dispatches, never claim them a second time. This
journal does not replace Botainer liveness or the live-checkout policy check.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Optional


class OperationConflict(ValueError):
    """An operation ID or checkout already belongs to different pending work."""


class InvalidTransition(ValueError):
    """The requested transition would invent an outcome or repeat dispatch."""


@dataclass(frozen=True)
class LaunchOperation:
    operation_id: str
    scope_key: str
    checkout: str
    context_id: str
    config_revision: str
    intent_fingerprint: str
    state: str
    created_at: float
    updated_at: float
    session_id: Optional[str]
    result_code: Optional[str]


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_STATES = {"prepared", "dispatching", "unknown", "succeeded", "failed", "cancelled"}
_SCHEMA_VERSION = 2
_TABLE_SQL = """
    CREATE TABLE operations (
        operation_id TEXT PRIMARY KEY NOT NULL,
        scope_key TEXT NOT NULL,
        checkout TEXT NOT NULL,
        context_id TEXT NOT NULL,
        config_revision TEXT NOT NULL,
        intent_fingerprint TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN
            ('prepared','dispatching','unknown','succeeded','failed','cancelled')),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        session_id TEXT,
        result_code TEXT
    )
"""
_INDEX_SQL = """
    CREATE UNIQUE INDEX one_pending_launch_per_checkout
    ON operations(scope_key, checkout)
    WHERE state IN ('prepared','dispatching','unknown')
"""


def _fingerprint(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("intent fingerprint must be sha256:<64 lowercase hexadecimal digits>")
    return value


def _schema_sql(sql: str) -> str:
    # This private database has one supported DDL shape per version. Exact
    # normalized DDL also checks CHECK constraints and the partial-index predicate,
    # which checking column names/index existence alone would miss.
    return " ".join(sql.split())


def _token(value: str, field: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError(f"{field} must be a bounded identifier, not free text")
    return value


def _checkout(value: str) -> str:
    # Canonicalize remote paths on their owning host, not the dashboard host.
    if (not isinstance(value, str) or not value.startswith("/")
            or value.startswith("//") or len(value) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(part in (".", "..", "") for part in value.split("/")[1:])):
        raise ValueError("checkout must be a canonical absolute POSIX path")
    return value


class OperationStore:
    """One connection per store; use from its owning thread and close explicitly.

    The application supplies a database path in its private state directory.
    Stored values are IDs/revisions/reason codes, never config bodies or input.
    SQLite serializes competing journal writers, including separate processes.
    """

    def __init__(self, database: Path):
        database = Path(database)
        if not database.is_absolute() or not database.parent.is_dir():
            raise ValueError("database requires an absolute path and existing parent")
        if database.is_symlink():
            raise ValueError("database must not be a symlink")
        try:
            fd = os.open(str(database), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not database.is_file():
                raise ValueError("database must be a regular file")
        else:
            os.close(fd)
        self._db = sqlite3.connect(str(database), timeout=5, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("BEGIN IMMEDIATE")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, _SCHEMA_VERSION):
                raise ValueError("unsupported operation database version")
            if version == 0:
                existing = self._db.execute(
                    "SELECT name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
                ).fetchall()
                if existing:
                    raise ValueError("refusing to initialize an unrelated database")
                self._db.execute(_TABLE_SQL)
                self._db.execute(_INDEX_SQL)
                self._db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            self._validate_schema()
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            self._db.close()
            raise

    def _validate_schema(self) -> None:
        rows = self._db.execute("""
            SELECT type, name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'
        """).fetchall()
        expected = {
            ("table", "operations"): _schema_sql(_TABLE_SQL),
            ("index", "one_pending_launch_per_checkout"): _schema_sql(_INDEX_SQL),
        }
        actual = {(row["type"], row["name"]): _schema_sql(row["sql"] or "") for row in rows}
        if actual != expected:
            raise ValueError("operation database schema does not match its supported version")

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "OperationStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, operation_id: str) -> LaunchOperation:
        _token(operation_id, "operation_id")
        row = self._db.execute(
            "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return LaunchOperation(**dict(row))

    def prepare(self, *, operation_id: str, scope_key: str, checkout: str,
                context_id: str, config_revision: str,
                intent_fingerprint: str) -> LaunchOperation:
        """Persist intent once. Same ID/content is idempotent; changed content is not.

        scope_key identifies logical host/account, NOT installation or state root,
        so parallel installations cannot bypass the pending-checkout guard.
        Callers must resolve and verify the checkout identity on the owning host.
        The fingerprint must cover the canonical immutable launch snapshot:
        context revision/source/state root, project UUID, runtime and effective
        launch inputs. Its authoritative producer and durable snapshot storage
        are later integration gates; this journal only validates and compares
        the digest, and cannot reconstruct or authenticate its contents.
        """
        values = (
            _token(operation_id, "operation_id"), _token(scope_key, "scope_key"),
            _checkout(checkout), _token(context_id, "context_id"),
            _token(config_revision, "config_revision"), _fingerprint(intent_fingerprint),
        )
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is not None:
                current = tuple(row[key] for key in (
                    "operation_id", "scope_key", "checkout", "context_id", "config_revision",
                    "intent_fingerprint"
                ))
                if current != values:
                    raise OperationConflict("operation ID reused with different launch intent")
            else:
                now = time.time()
                self._db.execute("""
                    INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?, ?, NULL, NULL)
                """, (*values, now, now))
            self._db.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self._db.execute("ROLLBACK")
            raise OperationConflict("checkout has an unresolved launch") from exc
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        return self.get(operation_id)

    def claim(self, operation_id: str, *, expected_fingerprint: str) -> bool:
        """True once, only for the expected immutable intent; not authorization.

        Recompute/verify the expected snapshot before calling. Merely copying the
        stored digest would not detect a changed installation or launch target.
        """
        _token(operation_id, "operation_id")
        _fingerprint(expected_fingerprint)
        changed = self._db.execute("""
            UPDATE operations SET state='dispatching', updated_at=?
            WHERE operation_id=? AND state='prepared' AND intent_fingerprint=?
        """, (time.time(), operation_id, expected_fingerprint)).rowcount
        if not changed:
            current = self.get(operation_id)  # An absent ID is not an already-claimed one.
            if current.intent_fingerprint != expected_fingerprint:
                raise OperationConflict("launch snapshot differs from prepared intent")
        return bool(changed)

    def record_unknown(self, operation_id: str, *, result_code: str) -> LaunchOperation:
        return self._finish(operation_id, "unknown", result_code, None)

    def record_success(self, operation_id: str, *, session_id: str) -> LaunchOperation:
        """Record an externally confirmed Botainer session ID, not a container/job ID.

        Full 16-character lowercase hexadecimal IDs match audited Botainer 0.1.0a5.
        Recording a result does not itself prove runtime or terminal readiness.
        """
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{16}", session_id):
            raise ValueError("session_id must be a full Botainer 16-character lowercase hex ID")
        return self._finish(operation_id, "succeeded", "session-confirmed", session_id)

    def record_failure(self, operation_id: str, *, result_code: str) -> LaunchOperation:
        """Use only with evidence no runtime was created; timeout is unknown."""
        return self._finish(operation_id, "failed", result_code, None)

    def cancel_prepared(self, operation_id: str, *,
                        result_code: str = "cancelled-before-dispatch") -> LaunchOperation:
        """Release only unclaimed intent; cannot cancel a possibly running launch."""
        return self._finish(operation_id, "cancelled", result_code, None,
                            allowed_from=("prepared",))

    def _finish(self, operation_id: str, state: str, code: str,
                session_id: Optional[str], *,
                allowed_from: tuple[str, ...] = ("dispatching", "unknown")) -> LaunchOperation:
        _token(operation_id, "operation_id")
        _token(code, "result_code")
        if state not in _STATES:
            raise ValueError("unsupported operation state")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            current = self.get(operation_id)
            if current.state == state and current.result_code == code and current.session_id == session_id:
                self._db.execute("COMMIT")
                return current
            if current.state not in allowed_from:
                raise InvalidTransition(f"cannot record {state} from {current.state}")
            self._db.execute("""
                UPDATE operations SET state=?, updated_at=?, session_id=?, result_code=?
                WHERE operation_id=?
            """, (state, time.time(), session_id, code, operation_id))
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        return self.get(operation_id)

    def mark_interrupted_dispatches(self) -> int:
        """On exclusive dispatcher restart, quarantine dispatches for reconciliation.

        Do not call while another live dispatcher may own these operations.
        Even a crash before the actual spawn is ambiguous without owner evidence.
        """
        return self._db.execute("""
            UPDATE operations SET state='unknown', result_code='dispatcher-interrupted',
                                  updated_at=? WHERE state='dispatching'
        """, (time.time(),)).rowcount

    def unresolved(self) -> list[LaunchOperation]:
        rows = self._db.execute("""
            SELECT * FROM operations WHERE state IN ('prepared','dispatching','unknown')
            ORDER BY created_at, operation_id
        """).fetchall()
        return [LaunchOperation(**dict(row)) for row in rows]
