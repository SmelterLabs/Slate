"""The in-memory database (tests) is one SQLite connection that every session and thread
shares (`StaticPool`). Many threads reading and writing through it at once must neither raise,
return another thread's rows, nor crash the process (`db.SharedConnection`).

Transactions still share the connection: one thread's rollback (a session returning its
connection, say) also undoes another thread's uncommitted write. That is what one shared
connection means, so the writes here are checked for not failing, not for all landing."""

from __future__ import annotations

import threading

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from slate.db import Base, SharedConnection, make_engine
from slate.ids import new_id
from slate.models import Account, AccountKind

THREADS = 12
ROUNDS = 80
ROWS = 2000


def test_many_threads_read_and_write_one_shared_connection():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as conn:
        assert isinstance(conn.connection.dbapi_connection, SharedConnection)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        conn.execute(
            text("INSERT INTO t (id, v) VALUES (:id, :v)"),
            [{"id": i, "v": f"row{i}"} for i in range(ROWS)],
        )
        ids = [new_id() for _ in range(20)]
        conn.execute(
            Account.__table__.insert(),
            [
                {"id": i, "handle": f"a{n}", "display_name": f"A{n}", "kind": AccountKind.agent}
                for n, i in enumerate(ids)
            ],
        )
    errors: list[BaseException] = []
    start = threading.Barrier(THREADS)

    def work(n: int) -> None:
        try:
            start.wait()
            for r in range(ROUNDS):
                kind = (n + r) % 4
                if kind == 0:  # a big read, fetched in chunks
                    with engine.connect() as conn:
                        rows = conn.execute(text("SELECT id, v FROM t WHERE id < :n"), {"n": ROWS})
                        rows = rows.all()
                    assert len(rows) == ROWS and rows[-1] == (ROWS - 1, f"row{ROWS - 1}")
                elif kind == 1:  # an aggregate, like the dispatcher's first query
                    with engine.connect() as conn:
                        top = conn.execute(text("SELECT max(id) FROM t WHERE id < :n"), {"n": ROWS})
                        assert top.scalar() == ROWS - 1
                elif kind == 2:  # an ORM lookup by key, like the token check
                    with Session(engine) as session:
                        account = session.get(Account, ids[(n + r) % len(ids)])
                        assert account is not None and account.handle.startswith("a")
                        assert session.scalar(select(func.count()).select_from(Account)) == 20
                else:  # a write and its commit
                    with engine.begin() as conn:
                        conn.execute(
                            text("INSERT INTO t (id, v) VALUES (:id, 'w')"),
                            {"id": ROWS + n * ROUNDS + r},
                        )
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    engine.dispose()
    assert errors == []
