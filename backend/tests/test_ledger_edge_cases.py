"""Independent/adversarial coverage for the ledger substrate (db.post_journal,
db.record_stock_movement, db._seed_ledger_accounts) added in commit 81e3cf9.

This file intentionally does NOT repeat what backend/tests/test_ledger.py
already covers (unbalanced/both-set/neither-set rejection, a balanced
multi-line post, idempotent seeding, record_stock_movement happy path, and
upgrading a pre-ledger legacy DB). It instead probes shapes the coder's own
suite is unlikely to have exercised: empty lines lists, single-line entries,
negative amounts slipping past the Python balance check, FK/CHECK violations
that fire mid-INSERT after journal_entries has already been written, the
UNIQUE constraint on idempotency_keys, the CHECK constraint on
payments.tender_type, and the "caller owns the transaction" contract.
"""

import sqlite3

import pytest


def _counts(conn: sqlite3.Connection) -> tuple[int, int]:
    entries = conn.execute("SELECT COUNT(*) AS n FROM journal_entries").fetchone()["n"]
    lines = conn.execute("SELECT COUNT(*) AS n FROM journal_lines").fetchone()["n"]
    return entries, lines


@pytest.fixture()
def db_module(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "test.db"))
    import db

    db.init_db()
    return db


def test_post_journal_empty_lines_list_writes_memo_only_entry_with_zero_lines(db_module):
    """DEFECT CANDIDATE: an empty lines list trivially balances (0 == 0), so
    post_journal happily writes a journal_entries row with zero journal_lines
    instead of raising ValueError. A memo-only, lineless "journal entry" is
    not a valid double-entry posting and should almost certainly be rejected
    the same way a single unbalanced line is."""
    conn = db_module.connect()
    try:
        journal_entry_id = db_module.post_journal(conn, "txn-empty", [], memo="oops")
        conn.commit()
        assert _counts(conn) == (1, 0)
        entry = conn.execute(
            "SELECT * FROM journal_entries WHERE id = ?", (journal_entry_id,)
        ).fetchone()
        assert entry["memo"] == "oops"
    finally:
        conn.close()


def test_post_journal_single_line_is_always_rejected_as_unbalanced(db_module):
    """A one-legged entry can only "balance" if its amount is 0, and 0 counts
    as "neither set" under the has_debit/has_credit check -- so a single line
    should always raise ValueError, never silently post."""
    conn = db_module.connect()
    try:
        with pytest.raises(ValueError):
            db_module.post_journal(
                conn, "txn-single", [{"account_code": "cash_on_hand", "debit_cents": 500}]
            )
        assert _counts(conn) == (0, 0)
    finally:
        conn.close()


def test_post_journal_explicit_zero_debit_and_credit_is_rejected(db_module):
    """Explicitly passing debit_cents=0, credit_cents=0 (rather than omitting
    the keys) must be treated identically to "neither set" -- confirms the
    check is value-based, not key-presence-based."""
    conn = db_module.connect()
    try:
        with pytest.raises(ValueError):
            db_module.post_journal(
                conn,
                "txn-explicit-zero",
                [
                    {"account_code": "cash_on_hand", "debit_cents": 0, "credit_cents": 0},
                    {"account_code": "sales_revenue", "credit_cents": 500},
                ],
            )
        assert _counts(conn) == (0, 0)
    finally:
        conn.close()


def test_post_journal_negative_amounts_pass_python_balance_check_but_violate_db_check(
    db_module,
):
    """DEFECT CANDIDATE: -500 debit and -500 credit sum to equal totals under
    plain arithmetic (-500 == -500), so the pure-Python balance check in
    post_journal lets this through as a "balanced" entry. The real guard is
    the CHECK(debit_cents >= 0) / CHECK(credit_cents >= 0) constraint on
    journal_lines, which fires mid-INSERT -- after journal_entries has
    already been written. Because post_journal does not wrap itself in a
    savepoint, this leaves an orphaned journal_entries row with fewer
    journal_lines than expected once the caller's surrounding transaction is
    rolled back or if the caller does not roll back at all."""
    conn = db_module.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db_module.post_journal(
                conn,
                "txn-negative",
                [
                    {"account_code": "cash_on_hand", "debit_cents": -500},
                    {"account_code": "sales_revenue", "credit_cents": -500},
                ],
            )
        # The journal_entries row was inserted before the failing INSERT INTO
        # journal_lines raised -- proving the partial-write hazard.
        entries, lines = _counts(conn)
        assert entries == 1
        assert lines == 0
    finally:
        conn.close()


def test_post_journal_invalid_account_code_raises_integrity_error_with_partial_write(
    db_module,
):
    """A second line referencing an account_code absent from ledger_accounts
    must fail the FK check (PRAGMA foreign_keys=ON is set by db.connect()).
    Because the first line's INSERT already succeeded before the second
    fails, this demonstrates a real partial write: 1 journal_entries row and
    1 journal_lines row survive an operation the caller believes raised
    before writing anything, unless the caller explicitly rolls back."""
    conn = db_module.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db_module.post_journal(
                conn,
                "txn-bad-account",
                [
                    {"account_code": "cash_on_hand", "debit_cents": 500},
                    {"account_code": "does_not_exist", "credit_cents": 500},
                ],
            )
        entries, lines = _counts(conn)
        assert entries == 1
        assert lines == 1
    finally:
        conn.close()


def test_post_journal_without_commit_then_close_persists_nothing(db_module):
    """Confirms the "caller owns the transaction" contract: post_journal
    itself never commits, so if the caller closes the connection without
    committing, nothing is persisted."""
    conn = db_module.connect()
    db_module.post_journal(
        conn,
        "txn-uncommitted",
        [
            {"account_code": "cash_on_hand", "debit_cents": 100},
            {"account_code": "sales_revenue", "credit_cents": 100},
        ],
    )
    conn.close()  # no commit()

    conn2 = db_module.connect()
    try:
        assert _counts(conn2) == (0, 0)
    finally:
        conn2.close()


def test_record_stock_movement_invalid_product_id_raises_integrity_error(db_module):
    """product_id REFERENCES products(id); a nonexistent product_id should
    raise sqlite3.IntegrityError under FK enforcement rather than silently
    inserting an orphaned stock_movements row."""
    conn = db_module.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db_module.record_stock_movement(conn, 999999, "txn-6", -1, "sale")
        conn.rollback()
        rows = conn.execute(
            "SELECT * FROM stock_movements WHERE product_id = ?", (999999,)
        ).fetchall()
        assert rows == []
    finally:
        conn.close()


def test_idempotency_keys_duplicate_store_and_key_raises_integrity_error(db_module):
    conn = db_module.connect()
    try:
        now = db_module.local_now_iso()
        conn.execute(
            """INSERT INTO idempotency_keys (store_id, key, transaction_id, created_at)
               VALUES ('store-01', 'req-1', 'txn-a', ?)""",
            (now,),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO idempotency_keys (store_id, key, transaction_id, created_at)
                   VALUES ('store-01', 'req-1', 'txn-b', ?)""",
                (now,),
            )
    finally:
        conn.rollback()
        conn.close()


def test_idempotency_keys_same_key_different_store_is_allowed(db_module):
    """The UNIQUE constraint is on the (store_id, key) pair, not key alone --
    confirms two stores can independently reuse the same idempotency key."""
    conn = db_module.connect()
    try:
        now = db_module.local_now_iso()
        conn.execute(
            """INSERT INTO idempotency_keys (store_id, key, transaction_id, created_at)
               VALUES ('store-01', 'req-1', 'txn-a', ?)""",
            (now,),
        )
        conn.execute(
            """INSERT INTO idempotency_keys (store_id, key, transaction_id, created_at)
               VALUES ('store-02', 'req-1', 'txn-b', ?)""",
            (now,),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT store_id FROM idempotency_keys WHERE key = 'req-1' ORDER BY store_id"
        ).fetchall()
        assert [r["store_id"] for r in rows] == ["store-01", "store-02"]
    finally:
        conn.close()


def test_payments_invalid_tender_type_raises_integrity_error(db_module):
    conn = db_module.connect()
    try:
        now = db_module.local_now_iso()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO payments
                   (transaction_id, tender_type, amount_cents, status, created_at)
                   VALUES ('txn-7', 'bitcoin', 500, 'captured', ?)""",
                (now,),
            )
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("tender_type", ["cash", "card"])
def test_payments_valid_tender_types_are_accepted(db_module, tender_type):
    conn = db_module.connect()
    try:
        now = db_module.local_now_iso()
        conn.execute(
            """INSERT INTO payments
               (transaction_id, tender_type, amount_cents, status, created_at)
               VALUES ('txn-8', ?, 500, 'captured', ?)""",
            (tender_type, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT tender_type FROM payments WHERE transaction_id = 'txn-8'"
        ).fetchone()
        assert row["tender_type"] == tender_type
    finally:
        conn.close()
