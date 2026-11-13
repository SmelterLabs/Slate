"""Event ids never go backwards, also across a restart that follows a burst."""

from __future__ import annotations

import time

from sqlalchemy.orm import sessionmaker

from slate import ids
from slate.db import Base, make_engine
from slate.models import Source
from slate.service import accounts
from slate.service.core import Actor, apply_change


def test_event_id_prefix_decodes_to_its_millisecond(monkeypatch):
    # An earlier test's burst or future clock may have run the logical clock ahead.
    monkeypatch.setattr(ids, "_last_ms", 0)
    before = int(time.time() * 1000)
    ms = ids.event_id_ms(ids.new_event_id())
    assert before <= ms <= int(time.time() * 1000) + 1
    assert ids.event_id_ms("") == 0 and ids.event_id_ms("not*base62") == 0


def test_a_new_process_continues_after_the_newest_stored_id(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{(tmp_path / 'ids.db').as_posix()}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    # A burst runs the logical clock ahead of the wall clock (one ms per id): here an hour.
    ahead = time.time() + 3600
    monkeypatch.setattr(ids.time, "time", lambda: ahead)
    with Session() as s:
        account, _, _ = accounts.bootstrap(s, handle="alex", display_name="Alex")
        with apply_change(s, Actor(account.id, Source.api)) as cx:
            ev = cx.record("test.pinged")
        stored = ev.id
    engine.dispose()

    # Restart: the wall clock is back to now, and the process starts from zero.
    monkeypatch.undo()
    monkeypatch.setattr(ids, "_last_ms", 0)
    engine = make_engine(url)
    try:
        with engine.connect():
            pass
        assert ids.new_event_id() > stored
    finally:
        engine.dispose()
