"""Slate's online backup, run by backup.sh inside the container (`docker exec -i slate python -`).

Standard library only (Python 3.8 or later), so the host's python3 can run `--revision` too:

    python -                        copy the live database to stdout; the last stderr line is
                                    `sha256 <hex> <bytes>` so the caller can check what it got
    python - --revision             print the database's migration revision (empty if none)
    python - --record ok|failed MSG write backup-status.json next to the database (the Health
                                    page reads it)

The live database is `SLATE_DATABASE_PATH` (default /data/slate.db). It is opened without
create: a missing file is an error, never a new empty database that backs up as "ok".
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATUS = "backup-status.json"


def live_path() -> Path:
    return Path(os.environ.get("SLATE_DATABASE_PATH") or "/data/slate.db")


def _open_live() -> sqlite3.Connection:
    path = live_path()
    if not path.is_file():
        sys.exit(f"no database at {path} (SLATE_DATABASE_PATH)")
    # mode=rw opens an existing file and never creates one.
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True)


def revision(con: sqlite3.Connection) -> str | None:
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.DatabaseError:
        return None
    return row[0] if row else None


def backup() -> None:
    src = _open_live()
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rev = revision(dst)
        dst.close()
        src.close()
        if ok != "ok":
            sys.exit("integrity_check failed: " + ok)
        if rev is None or "items" not in tables:
            sys.exit(f"{live_path()} is not a migrated Slate database (no revision or no items)")
        digest = hashlib.sha256()
        size = 0
        with open(tmp, "rb") as fh:
            while chunk := fh.read(1 << 20):
                digest.update(chunk)
                size += len(chunk)
                sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        print(f"sha256 {digest.hexdigest()} {size}", file=sys.stderr)
    finally:
        os.remove(tmp)


def record(outcome: str, detail: str) -> None:
    path = live_path().parent / STATUS
    # timezone.utc, not datetime.UTC: the host's python3 may be older than 3.11.
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017
    try:
        before = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = {}
    status = {
        "ok": outcome == "ok",
        "at": now,
        "detail": detail[:500],
        "last_ok_at": now if outcome == "ok" else before.get("last_ok_at"),
    }
    tmp = path.with_name(STATUS + ".tmp")
    tmp.write_text(json.dumps(status), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str]) -> None:
    if argv[:1] == ["--revision"]:
        con = _open_live()
        print(revision(con) or "")
        con.close()
    elif argv[:1] == ["--record"] and len(argv) >= 2 and argv[1] in ("ok", "failed"):
        record(argv[1], " ".join(argv[2:]))
    elif not argv:
        backup()
    else:
        sys.exit("usage: backup.py [--revision | --record ok|failed MESSAGE]")


if __name__ == "__main__":
    main(sys.argv[1:])
