"""
db.py — SQLite database layer for SIH Tracker

Handles all reads/writes. One file, no server, zero setup.
Run `python db.py` once to create the database file (sih_tracker.db).
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = "sih_tracker.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL mode lets reads (like the chat endpoint) happen while the
    # scheduler is mid-write, instead of one blocking the other.
    conn.execute("PRAGMA journal_mode = WAL")
    # If a lock is still held for some reason, retry for up to 30s instead
    # of immediately throwing "database is locked".
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS problem_statements (
                ps_id           TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                organization    TEXT,
                category        TEXT,
                theme           TEXT,
                current_count   INTEGER DEFAULT 0,
                last_updated    TEXT
            );

            CREATE TABLE IF NOT EXISTS submission_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ps_id       TEXT NOT NULL,
                count       INTEGER NOT NULL,
                checked_at  TEXT NOT NULL,
                FOREIGN KEY (ps_id) REFERENCES problem_statements(ps_id)
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_ps_time
                ON submission_snapshots(ps_id, checked_at);

            CREATE TABLE IF NOT EXISTS events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ps_id           TEXT NOT NULL,
                previous_count  INTEGER NOT NULL,
                new_count       INTEGER NOT NULL,
                delta           INTEGER NOT NULL,
                detected_at     TEXT NOT NULL,
                notified        INTEGER DEFAULT 0,
                FOREIGN KEY (ps_id) REFERENCES problem_statements(ps_id)
            );

            CREATE TABLE IF NOT EXISTS team_members (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                email           TEXT,
                email_enabled   INTEGER DEFAULT 1,
                active          INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                status          TEXT,
                ps_count        INTEGER,
                error_message   TEXT
            );
            """
        )
        _migrate_team_members_to_email(conn)
    print(f"Database initialized at {DB_PATH}")


def _migrate_team_members_to_email(conn):
    """One-time self-healing migration: if team_members still has the old
    Telegram columns (from before we switched to email notifications),
    rename them over to the new email columns instead of losing data."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(team_members)")}
    if "telegram_chat_id" in existing_cols and "email" not in existing_cols:
        print("[db] Migrating team_members: telegram_chat_id -> email")
        conn.execute("ALTER TABLE team_members RENAME COLUMN telegram_chat_id TO email")
    if "telegram_enabled" in existing_cols and "email_enabled" not in existing_cols:
        print("[db] Migrating team_members: telegram_enabled -> email_enabled")
        conn.execute("ALTER TABLE team_members RENAME COLUMN telegram_enabled TO email_enabled")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def upsert_problem_statement(ps_id, title, organization, category, theme, count):
    """Insert or update a PS row, and record a snapshot if the count changed
    (or if this is the first time we've seen it). Returns an event dict if
    a genuine new-submission event should be raised, else None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT current_count FROM problem_statements WHERE ps_id = ?",
            (ps_id,),
        ).fetchone()

        event = None
        checked_at = now_iso()

        if row is None:
            # First time seeing this PS — establish baseline, no event.
            conn.execute(
                """INSERT INTO problem_statements
                   (ps_id, title, organization, category, theme, current_count, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ps_id, title, organization, category, theme, count, checked_at),
            )
            conn.execute(
                "INSERT INTO submission_snapshots (ps_id, count, checked_at) VALUES (?, ?, ?)",
                (ps_id, count, checked_at),
            )
        else:
            previous_count = row["current_count"]
            conn.execute(
                """UPDATE problem_statements
                   SET title = ?, organization = ?, category = ?, theme = ?,
                       current_count = ?, last_updated = ?
                   WHERE ps_id = ?""",
                (title, organization, category, theme, count, checked_at, ps_id),
            )
            conn.execute(
                "INSERT INTO submission_snapshots (ps_id, count, checked_at) VALUES (?, ?, ?)",
                (ps_id, count, checked_at),
            )

            if count != previous_count:
                delta = count - previous_count
                conn.execute(
                    """INSERT INTO events (ps_id, previous_count, new_count, delta, detected_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (ps_id, previous_count, count, delta, checked_at),
                )
                event = {
                    "ps_id": ps_id,
                    "title": title,
                    "previous_count": previous_count,
                    "new_count": count,
                    "delta": delta,
                    "detected_at": checked_at,
                }
        return event


def get_all_problem_statements():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM problem_statements ORDER BY current_count DESC"
        ).fetchall()]


def get_snapshot_history(ps_id, limit=200):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT count, checked_at FROM submission_snapshots
               WHERE ps_id = ? ORDER BY checked_at DESC LIMIT ?""",
            (ps_id, limit),
        ).fetchall()]


def get_active_team_members():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM team_members WHERE active = 1"
        ).fetchall()]


def add_team_member(name, email):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO team_members (name, email) VALUES (?, ?)",
            (name, email),
        )


def mark_event_notified(event_id):
    with get_conn() as conn:
        conn.execute("UPDATE events SET notified = 1 WHERE id = ?", (event_id,))


def log_sync_start():
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sync_log (started_at, status) VALUES (?, 'running')",
            (now_iso(),),
        )
        return cur.lastrowid


def log_sync_end(sync_id, status, ps_count=0, error_message=None):
    with get_conn() as conn:
        conn.execute(
            """UPDATE sync_log SET finished_at = ?, status = ?, ps_count = ?, error_message = ?
               WHERE id = ?""",
            (now_iso(), status, ps_count, error_message, sync_id),
        )


if __name__ == "__main__":
    init_db()