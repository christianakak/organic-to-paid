"""sqlite helpers.

Everything sqlite-specific in this project lives here plus `schema.sql`.
Moving to Postgres means rewriting `connect()` and swapping AUTOINCREMENT
for SERIAL; no other module touches the driver.

Connections use `sqlite3.Row`, so every call site indexes results by name
(`row["id"]`) rather than by position. Rows still unpack as tuples, which is
what `counts()` callers rely on.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Columns insert_signal will accept. Anything else passed in is a caller
# bug and raises rather than being silently dropped — silent dropping is
# the failure mode this project is trying to design out.
SIGNAL_FIELDS = (
    "external_id", "parent_id", "permalink", "created_at",
    "reach", "impressions", "saves", "shares", "reactions",
    "comment_count", "clicks", "conversions", "is_boosted", "raw",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


class _Connection(sqlite3.Connection):
    """Commits and closes on a clean exit, rolls back and closes on an error.

    Plain `sqlite3.Connection` commits on `__exit__` but leaves the handle
    open, which leaks one file descriptor per `with db.connect()` block.
    Every call site in this project uses the `with` form.
    """

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def connect():
    conn = sqlite3.connect(
        config.ANGLE_DB,
        factory=_Connection,
        detect_types=0,
        isolation_level="",
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init():
    """Create the schema. Idempotent — every statement is IF NOT EXISTS."""
    Path(config.ANGLE_DB).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


# ------------------------------------------------------------------
# Signals
# ------------------------------------------------------------------

def insert_signal(conn, account, source, kind, text, **fields):
    """Insert one signal. Returns its id, or None if it was already there.

    Every adapter counts what it added by checking this return value, so
    the duplicate case has to be a quiet None rather than an exception —
    re-running `pull` is expected and normal.
    """
    unknown = set(fields) - set(SIGNAL_FIELDS)
    if unknown:
        raise TypeError(
            f"insert_signal got unexpected field(s): {sorted(unknown)}"
        )

    row = {
        "account": account,
        "source": source,
        "kind": kind,
        "text": text or "",
        "fetched_at": _now(),
    }
    for key in SIGNAL_FIELDS:
        if key in fields:
            row[key] = fields[key]

    if isinstance(row.get("raw"), (dict, list)):
        row["raw"] = json.dumps(row["raw"], ensure_ascii=False)
    if "is_boosted" in row:
        row["is_boosted"] = int(bool(row["is_boosted"]))

    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    cur = conn.execute(
        f"INSERT OR IGNORE INTO signal ({cols}) VALUES ({marks})",
        list(row.values()),
    )
    return cur.lastrowid if cur.rowcount else None


def counts(conn, account):
    """(source, kind, n) per source, for the CLI tally and the onboarding page."""
    return conn.execute(
        "SELECT source, kind, COUNT(*) AS n FROM signal "
        "WHERE account = ? GROUP BY source, kind ORDER BY source, kind",
        (account,),
    ).fetchall()


# ------------------------------------------------------------------
# Pull log
# ------------------------------------------------------------------

def record(conn, account, source, event, detail=""):
    """Note something a pull did or failed to do.

    `event` is one of ok | dropped | skipped | error. The point of this
    table is that a pull returning zero rows because a property id is wrong
    should not look identical to one returning zero because there is no
    data.
    """
    conn.execute(
        "INSERT INTO pull_log (account, source, ts, event, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (account, source, _now(), event, str(detail)[:2000]),
    )


def pull_log(conn, account, limit=50):
    return conn.execute(
        "SELECT source, ts, event, detail FROM pull_log "
        "WHERE account = ? ORDER BY id DESC LIMIT ?",
        (account, limit),
    ).fetchall()
