from __future__ import annotations

from alembic import context

from slate import connection_models, models  # noqa: F401 (register tables)
from slate.db import Base, engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        # SQLite changes a column by building a new table, dropping the old one and renaming the
        # new one. With foreign keys on, dropping a table that other tables point at fails as soon
        # as it holds rows (accounts does), so a migration that works on an empty file fails on a
        # database with data. SQLite's own procedure for this is: keys off, rebuild, check, keys on.
        # The pragma is ignored inside a transaction, so it goes first, before anything begins.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()  # ends the transaction SQLAlchemy opened for the pragma
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite needs batch mode for ALTER
        )
        with context.begin_transaction():
            context.run_migrations()
        broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise RuntimeError(f"migration left {len(broken)} broken foreign key(s): {broken[:5]}")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
