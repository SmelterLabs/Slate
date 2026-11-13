from __future__ import annotations

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from slate import connection_models, models  # noqa: F401 (register tables)
from slate.db import Base, engine

target_metadata = Base.metadata


def refuse_unknown_revision(connection) -> None:
    """The history starts at a baseline; a database stamped with a revision from before it
    cannot be upgraded by this version. Say so plainly instead of Alembic's "Can't locate
    revision"."""
    if not inspect(connection).has_table("alembic_version"):
        return
    script = ScriptDirectory.from_config(context.config)
    known = {rev.revision for rev in script.walk_revisions()}
    for (version,) in connection.exec_driver_sql("SELECT version_num FROM alembic_version"):
        if version not in known:
            base = ", ".join(script.get_bases())
            raise RuntimeError(
                f"this database is at revision {version}, which is older than this version's "
                f"first migration ({base}): upgrade it with Slate 0.1.0 first, then run this "
                "version's upgrade"
            )


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
        refuse_unknown_revision(connection)
        connection.commit()
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
