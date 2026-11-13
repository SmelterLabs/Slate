"""Database engine and session factory.

SQLite in WAL mode with foreign keys on. One engine per process; sessions are short-lived.

Rows are fetched under one process-wide lock (`STATEMENT_LOCK`, in `SerialCursor`). Python's
sqlite3 gives up the GIL around each row it steps; with several threads reading at once each
row then waits for the GIL behind another thread, a convoy that turned a 13 ms read of 20,000
rows into 12 s with ten readers (docs/PERFORMANCE.md). Fetching in turns keeps every read
close to its time alone. The statement itself (up to its first row) runs outside the lock, and
rows come in chunks of `FETCH_CHUNK` with the lock released between them, so a long query or a
big read never holds up a small one for more than a chunk.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import ColumnElement, Engine, create_engine, event, func
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from slate.settings import settings


class Base(DeclarativeBase):
    pass


STATEMENT_LOCK = threading.Lock()
FETCH_CHUNK = 500


class SerialCursor(sqlite3.Cursor):
    """A cursor that fetches its rows only while holding `STATEMENT_LOCK`, and buffers them.

    SQLAlchemy reads results through `fetchone`/`fetchmany`/`fetchall`, which serve the buffer.
    """

    _rows: list | None = None
    _pos = 0

    def execute(self, sql, parameters=(), /):  # noqa: ANN001, ANN201
        # The statement runs (up to its first row) without the lock: a long aggregate or sort
        # steps once, without the GIL, and must not hold up anyone else. Only the row-by-row
        # fetch, where the convoy happens, takes turns.
        super().execute(sql, parameters)
        rows: list = []
        if self.description is not None:
            while True:
                with STATEMENT_LOCK:
                    chunk = super().fetchmany(FETCH_CHUNK)
                rows += chunk
                if len(chunk) < FETCH_CHUNK:
                    break
        self._rows, self._pos = rows, 0
        return self

    def executemany(self, sql, seq_of_parameters, /):  # noqa: ANN001, ANN201
        super().executemany(sql, seq_of_parameters)
        self._rows, self._pos = [], 0
        return self

    def fetchone(self):  # noqa: ANN201
        if self._rows is None:
            return super().fetchone()
        if self._pos >= len(self._rows):
            return None
        self._pos += 1
        return self._rows[self._pos - 1]

    def fetchmany(self, size=None):  # noqa: ANN001, ANN201
        if self._rows is None:
            return super().fetchmany(size or self.arraysize)
        start = self._pos
        self._pos = min(len(self._rows), start + (size or self.arraysize))
        return self._rows[start : self._pos]

    def fetchall(self):  # noqa: ANN201
        if self._rows is None:
            return super().fetchall()
        rest = self._rows[self._pos :] if self._pos else self._rows
        self._pos = len(self._rows)
        return rest

    def __iter__(self):  # noqa: ANN204
        return self

    def __next__(self):  # noqa: ANN204
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class SerialConnection(sqlite3.Connection):
    def cursor(self, factory=SerialCursor):  # noqa: ANN001, ANN201
        return super().cursor(factory)


def _ulower(value: str | None) -> str | None:
    return value.lower() if isinstance(value, str) else value


def _configure_sqlite(dbapi_connection, _record) -> None:  # noqa: ANN001
    # SQLite's own lower() changes only A-Z; text search compares with Python's lower().
    dbapi_connection.create_function("ulower", 1, _ulower, deterministic=True)
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


# Connections kept open between requests. Past this the pool opens more (a SQLite connection
# is a few file handles) and closes them on return; it never makes a caller wait. The token
# check runs on the event loop, so a wait for a connection there would stop every request,
# including the ones that would hand a connection back. How many are open at once is bounded
# by the work running (the threadpool), because a request gives its connection back after the
# token check and while it waits (`api.auth.require`, `api.longpoll`).
POOL_SIZE = 20


def make_engine(url: str | None = None) -> Engine:
    url = url or settings.database_url
    kwargs: dict = {"connect_args": {"check_same_thread": False, "factory": SerialConnection}}
    if url.endswith(":memory:"):
        kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(pool_size=POOL_SIZE, max_overflow=-1)
        Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    event.listen(engine, "connect", _configure_sqlite)
    event.listen(engine, "first_connect", _seed_event_clock)
    return engine


def _seed_event_clock(dbapi_connection, _record) -> None:  # noqa: ANN001
    """Event ids continue after the newest stored one (slate.ids.seed_event_clock)."""
    from slate.ids import seed_event_clock

    cursor = dbapi_connection.cursor()
    try:
        newest = []
        for table in ("events", "messages"):
            try:
                cursor.execute(f"SELECT max(id) FROM {table}")  # noqa: S608 (fixed names)
                newest.append(cursor.fetchone()[0])
            except sqlite3.OperationalError:  # not migrated yet: nothing to continue after
                pass
        seed_event_clock(newest)
    finally:
        cursor.close()


def like_pattern(q: str) -> str:
    """`%q%`, lower-cased, with the caller's `%`, `_` and `\\` escaped (use with `escape="\\"`)."""
    body = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{body}%"


def contains(column: ColumnElement, q: str) -> ColumnElement[bool]:
    """`column` contains `q`, any case (Unicode, not only A-Z), `%` and `_` taken literally.
    Every text search (tasks, docs, chat) matches through this."""
    return func.ulower(column).like(like_pattern(q), escape="\\")


@event.listens_for(Session, "before_flush")
def _guard_flush(session, flush_context, instances) -> None:  # noqa: ANN001
    # Imported lazily: slate.service imports the models, which import this module.
    from slate.service.core import guard_flush

    guard_flush(session, flush_context, instances)


@event.listens_for(Session, "do_orm_execute")
def _guard_bulk(state) -> None:  # noqa: ANN001
    from slate.service.access import read_criteria
    from slate.service.core import guard_bulk

    guard_bulk(state)
    read_criteria(state)


engine: Engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[Session]:
    """FastAPI dependency: one session per request, committed by the service layer.

    Async so FastAPI does not spend two worker-thread round trips opening and closing it; the
    (sync) route still runs in the threadpool and uses the session there, one thread at a time.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def release(session: Session) -> None:
    """End `session`'s read transaction and hand its connection back to the pool.

    Loaded rows stay loaded (`expire_on_commit=False`); the next query takes a connection
    again. Call it before a request waits on anything that is not the database.
    """
    session.commit()
