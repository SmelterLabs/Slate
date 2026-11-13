"""Doc writes: tags on docs, duplicate, promote, event size, listings and downloads."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from conftest import API
from slate.models import Event


def _doc(client, alex, **body):
    r = client.post(f"{API}/docs", json=body, headers=alex)
    assert r.status_code == 201, r.text
    return r.json()


def _doc_events(db, doc_id, type_="doc.updated"):
    db.expire_all()
    return [
        (e.field, e.before_json, e.after_json)
        for e in db.scalars(
            select(Event).where(Event.subject_id == doc_id, Event.type == type_).order_by(Event.id)
        )
    ]


def _file_doc(client, alex, title, data, filename="q.csv", ctype="text/csv", **body):
    d = _doc(client, alex, kind="file", title=title, **body)
    r = client.post(
        f"{API}/docs/{d['id']}/revisions",
        params={"filename": filename, "note": "first"},
        content=data,
        headers={**alex, "Content-Type": ctype},
    )
    assert r.status_code == 201, r.text
    return d


class _Statements:
    """SQL statements the engine runs while the block is open."""

    def __enter__(self):
        from sqlalchemy import event

        from slate.db import engine

        self.sql: list[str] = []
        self._engine = engine
        self._listen = lambda *a: self.sql.append(a[2])
        event.listen(engine, "before_cursor_execute", self._listen)
        return self

    def __exit__(self, *exc):
        from sqlalchemy import event

        event.remove(self._engine, "before_cursor_execute", self._listen)


# --- duplicate ---------------------------------------------------------------------------------


def test_duplicate_reads_only_the_copied_docs_and_never_reextracts(client, alex, todo, monkeypatch):
    from slate.service import doc_compare

    for n in range(3):  # unrelated files elsewhere in the workspace
        _file_doc(client, alex, f"Other {n}", b"a,b\n1,2\n")
    page = _doc(client, alex, title="Notes", list_id=todo["id"], body_md="hello")
    sheet = _file_doc(client, alex, "Quote", b"item,price\nbolt,0.25\n", parent_id=page["id"])

    def never(*_a, **_k):
        raise AssertionError("a copy has the same bytes; its text and tables are copied")

    monkeypatch.setattr(doc_compare, "extract", never)

    with _Statements() as st:
        r = client.post(
            f"{API}/docs/{page['id']}/duplicate", json={"subpages": False}, headers=alex
        )
    assert r.status_code == 201, r.text
    assert not [s for s in st.sql if "doc_files.data" in s]

    with _Statements() as st:
        r = client.post(f"{API}/docs/{page['id']}/duplicate", json={}, headers=alex)
    assert r.status_code == 201, r.text
    blob_reads = [s for s in st.sql if "doc_files.data" in s]
    assert len(blob_reads) == 1  # the one file in the copied subtree

    copy_kid = r.json()["children"][0]
    assert copy_kid["title"] == "Quote"
    hits = client.get(f"{API}/docs", params={"q": "bolt"}, headers=alex).json()
    assert {h["id"] for h in hits} >= {sheet["id"], copy_kid["id"]}
    out = client.get(
        f"{API}/docs/{copy_kid['id']}/revisions/compare", params={"from": 1}, headers=alex
    )
    assert out.status_code == 200, out.text


# --- moves ------------------------------------------------------------------------------------


def _actor(client, alex):
    from slate.models import Source
    from slate.service.core import Actor

    me = client.get(f"{API}/me", headers=alex).json()
    return Actor(account_id=me["account"]["id"], source=Source.api)


def test_two_moves_at_once_cannot_make_a_page_its_own_ancestor(client, alex, todo, monkeypatch):
    """A moves under B while B moves under A: the move that writes second sees the first."""
    from contextlib import contextmanager

    from slate.db import SessionLocal
    from slate.service import docs as docs_svc
    from slate.service.errors import Invalid

    a = _doc(client, alex, title="A", list_id=todo["id"])
    b = _doc(client, alex, title="B", list_id=todo["id"])
    actor = _actor(client, alex)
    real = docs_svc.apply_change
    raced = []

    @contextmanager
    def racing(session, who):
        if not raced:  # B moves under A after this move read the tree, before it writes
            raced.append(1)
            with SessionLocal() as other:
                docs_svc.move_doc(other, who, b["id"], {"parent_id": a["id"]})
        with real(session, who) as cx:
            yield cx

    monkeypatch.setattr(docs_svc, "apply_change", racing)
    with SessionLocal() as mine, pytest.raises(Invalid, match="under itself"):
        docs_svc.move_doc(mine, actor, a["id"], {"parent_id": b["id"]})
    got = {d["id"]: d["parent_id"] for d in client.get(f"{API}/docs", headers=alex).json()}
    assert got == {a["id"]: None, b["id"]: a["id"]}


def test_a_move_rechecks_depth_against_the_tree_as_it_stands(client, alex, todo, monkeypatch):
    from contextlib import contextmanager

    from slate.db import SessionLocal
    from slate.service import docs as docs_svc
    from slate.service.errors import Invalid

    monkeypatch.setattr(docs_svc, "MAX_DEPTH", 3)
    top = _doc(client, alex, title="Top", list_id=todo["id"])
    mid = _doc(client, alex, title="Mid", parent_id=top["id"])
    leaf = _doc(client, alex, title="Leaf", list_id=todo["id"])
    kid = _doc(client, alex, title="Kid", list_id=todo["id"])
    actor = _actor(client, alex)
    real = docs_svc.apply_change
    raced = []

    @contextmanager
    def racing(session, who):
        if not raced:  # Kid goes under Leaf in the meantime: Leaf is now two levels tall
            raced.append(1)
            with SessionLocal() as other:
                docs_svc.move_doc(other, who, kid["id"], {"parent_id": leaf["id"]})
        with real(session, who) as cx:
            yield cx

    monkeypatch.setattr(docs_svc, "apply_change", racing)
    with SessionLocal() as mine, pytest.raises(Invalid, match="deep at most"):
        docs_svc.move_doc(mine, actor, leaf["id"], {"parent_id": mid["id"]})


def test_walking_a_broken_tree_ends():
    from types import SimpleNamespace

    from slate.service import docs as docs_svc

    a = SimpleNamespace(id="a", parent_id="b")
    b = SimpleNamespace(id="b", parent_id="a")
    c = SimpleNamespace(id="c", parent_id="a")
    assert sorted(d.id for d in docs_svc._descendants([a, b, c], "a")) == ["b", "c"]


# --- saves ------------------------------------------------------------------------------------


def _race_first_change(monkeypatch, other_write):
    """Run `other_write(session)` in its own session just before the first change block of
    the code under test takes the write lock (after that code has read the doc)."""
    from contextlib import contextmanager

    from slate.db import SessionLocal
    from slate.service import docs as docs_svc

    real = docs_svc.apply_change
    raced = []

    @contextmanager
    def racing(session, who):
        if not raced:
            raced.append(1)
            with SessionLocal() as other:
                other_write(other)
        with real(session, who) as cx:
            yield cx

    monkeypatch.setattr(docs_svc, "apply_change", racing)


def test_a_save_against_a_stale_revision_is_refused_even_mid_flight(
    client, alex, todo, monkeypatch
):
    from slate.db import SessionLocal
    from slate.service import docs as docs_svc
    from slate.service.errors import Conflict

    d = _doc(client, alex, title="Plan", list_id=todo["id"], body_md="v1")
    actor = _actor(client, alex)
    _race_first_change(
        monkeypatch,
        lambda s: docs_svc.update_doc(
            s, actor, d["id"], {"body_md": "A's text", "base_revision": 1}
        ),
    )
    with SessionLocal() as mine, pytest.raises(Conflict):
        docs_svc.update_doc(mine, actor, d["id"], {"body_md": "B's text", "base_revision": 1})
    got = client.get(f"{API}/docs/{d['id']}", headers=alex).json()
    assert (got["body_md"], got["revision"]) == ("A's text", 2)


def test_two_appends_at_once_both_land(client, alex, todo, monkeypatch):
    from slate.db import SessionLocal
    from slate.service import docs as docs_svc

    d = _doc(client, alex, title="Log", list_id=todo["id"], body_md="## Log")
    actor = _actor(client, alex)
    _race_first_change(
        monkeypatch,
        lambda s: docs_svc.update_doc(s, actor, d["id"], {"append_md": "- entry from A"}),
    )
    with SessionLocal() as mine:
        docs_svc.update_doc(mine, actor, d["id"], {"append_md": "- entry from B"})
    body = client.get(f"{API}/docs/{d['id']}", headers=alex).json()["body_md"]
    assert "- entry from A" in body and "- entry from B" in body


def test_create_task_from_a_doc_checks_the_doc_as_it_stands(client, alex, todo, monkeypatch):
    from slate.db import SessionLocal
    from slate.service import docs as docs_svc
    from slate.service.errors import Conflict

    d = _doc(client, alex, title="Plan", list_id=todo["id"], body_md="call the venue")
    actor = _actor(client, alex)
    _race_first_change(
        monkeypatch,
        lambda s: docs_svc.update_doc(s, actor, d["id"], {"append_md": "and the caterer"}),
    )
    with SessionLocal() as mine, pytest.raises(Conflict):
        docs_svc.create_task(
            mine, actor, d["id"], title="Call", replace="call the venue", base_revision=1
        )
    assert client.get(f"{API}/items", params={"list_id": todo["id"]}, headers=alex).json() == []


# --- what a page save records -----------------------------------------------------------------


def test_a_page_save_event_points_at_page_history_instead_of_copying_the_body(
    client, alex, todo, db
):
    long = "word " * 20_000
    d = _doc(client, alex, title="Notes", list_id=todo["id"], body_md=long)
    r = client.patch(
        f"{API}/docs/{d['id']}", json={"append_md": "one more line", "note": "draft"}, headers=alex
    )
    assert r.status_code == 200, r.text
    r = client.patch(f"{API}/docs/{d['id']}", json={"body_md": "short"}, headers=alex)
    assert r.status_code == 200, r.text

    saves = [e for e in _doc_events(db, d["id"]) if e[0] == "body_md"]
    assert len(saves) == 2
    for _field, before, after in saves:
        assert set(before) == {"version_no", "chars"} and set(after) == {"version_no", "chars"}
    assert saves[0][1]["chars"] == len(d["body_md"]) and saves[1][2]["chars"] == len("short")
    assert (saves[1][1]["version_no"], saves[1][2]["version_no"]) == (1, 2)

    act = client.get(f"{API}/docs/{d['id']}/activity", headers=alex)
    assert act.status_code == 200 and len(act.content) < 10_000
    assert [e["field"] for e in act.json()["events"] if e["type"] == "doc.updated"].count(
        "body_md"
    ) == 2
    # The text itself is in page history, and restoring it still works.
    versions = client.get(f"{API}/docs/{d['id']}/versions", headers=alex).json()
    first = min(v["version_no"] for v in versions)
    r = client.post(f"{API}/docs/{d['id']}/versions/{first}/restore", headers=alex)
    assert r.status_code == 200, r.text
    assert r.json()["body_md"].startswith("word word")
    restored = _doc_events(db, d["id"], "doc.version_restored")[-1]
    assert "body_md" not in restored[1] and "body_md" not in restored[2]
    assert restored[2]["restored_from"] == first


def test_activity_leaves_out_bodies_recorded_before_saves_pointed_at_history(
    client, alex, todo, db
):
    from slate.service import doc_threads

    d = _doc(client, alex, title="Old", list_id=todo["id"], body_md="x")
    out = doc_threads._activity_event(
        {"type": "doc.updated", "field": "body_md", "before": "x" * 500, "after": "y" * 500}
    )
    assert out["before"] is None and out["after"] is None
    kept = {"type": "doc.updated", "field": "title", "before": "a", "after": "b"}
    assert doc_threads._activity_event(dict(kept)) == kept
    assert client.get(f"{API}/docs/{d['id']}/activity", headers=alex).status_code == 200


# --- listings ---------------------------------------------------------------------------------


def _reads_text(sql: list[str]) -> list[str]:
    """Statements that fetch the extracted text itself (a NULL test on it is fine)."""
    return [s for s in sql if "doc_files.text" in s.replace("doc_files.text IS NOT NULL", "")]


def test_listings_report_has_text_without_loading_the_text(client, alex, todo):
    folder = _doc(client, alex, title="Quotes", list_id=todo["id"])
    sheet = _file_doc(client, alex, "Quote", b"item,price\nbolt,0.25\n", parent_id=folder["id"])
    blob = _file_doc(client, alex, "Zip", b"PK\x03\x04", "a.zip", "application/zip")
    with _Statements() as st:
        listing = client.get(f"{API}/docs", headers=alex).json()
        parent = client.get(f"{API}/docs/{folder['id']}", headers=alex).json()
        one = client.get(f"{API}/docs/{sheet['id']}", headers=alex).json()
        revs = client.get(f"{API}/docs/{blob['id']}/revisions", headers=alex).json()
    assert _reads_text(st.sql) == []
    files = {d["id"]: d["file"]["has_text"] for d in listing if d["file"]}
    assert files == {sheet["id"]: True, blob["id"]: False}
    assert [c["file"]["has_text"] for c in parent["children"]] == [True]
    assert one["file"]["has_text"] is True and one["revisions"][0]["has_text"] is True
    assert [r["has_text"] for r in revs] == [False]
    # A search still finds and quotes the text of the current revision.
    hits = client.get(f"{API}/docs", params={"q": "bolt"}, headers=alex).json()
    assert [(h["id"], "bolt" in (h["snippet"] or "")) for h in hits] == [(sheet["id"], True)]


def test_reading_or_saving_a_page_walks_the_tree_once(client, alex, todo):
    page = _doc(client, alex, title="Spec", list_id=todo["id"])
    for n in range(4):
        _doc(client, alex, title=f"Part {n}", parent_id=page["id"])
    link = f"[[doc:{page['id']}]]"
    linker = _doc(client, alex, title="Index", list_id=todo["id"], body_md=f"See {link}")
    hidden = _doc(client, alex, title="Old", list_id=todo["id"], body_md=link)
    client.post(f"{API}/docs/{hidden['id']}/archive", headers=alex)

    def tree_reads(sql):  # the whole-table read of docs, and any lazy load of one column
        return [
            s for s in sql if s.startswith("SELECT docs.") and "WHERE" not in s.split("FROM")[1]
        ], [s for s in sql if s.startswith("SELECT docs.top_pinned")]

    for call in (
        lambda: client.get(f"{API}/docs/{page['id']}", headers=alex),
        lambda: client.patch(f"{API}/docs/{page['id']}", json={"body_md": "v2"}, headers=alex),
    ):
        with _Statements() as st:
            r = call()
        assert r.status_code == 200, r.text
        whole, lazy = tree_reads(st.sql)
        assert len(whole) == 1 and lazy == []
        assert [b["id"] for b in r.json()["backlinks"]] == [linker["id"]]
        assert len(r.json()["children"]) == 4


# --- several steps, one change ----------------------------------------------------------------


def _guest(client, alex, handle="alex-guest"):
    acct = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": "Guest", "kind": "person", "role": "guest"},
        headers=alex,
    ).json()
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    return acct, {"Authorization": f"Bearer {tok['token']}"}


def _share(client, alex, account_id, kind, scope_id, level):
    r = client.put(
        f"{API}/shares",
        json={"account_id": account_id, "scope_kind": kind, "scope_id": scope_id, "level": level},
        headers=alex,
    )
    assert r.status_code == 200, r.text


def test_a_refused_promote_leaves_nothing_half_done(client, alex, todo):
    guest, gh = _guest(client, alex)
    _share(client, alex, guest["id"], "list", todo["id"], "comment")
    drawer = client.post(
        f"{API}/lists",
        json={"section_id": todo["section_id"], "name": "Files", "kind": "drawer"},
        headers=alex,
    ).json()
    _share(client, alex, guest["id"], "list", drawer["id"], "edit")
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Quote"}, headers=alex
    ).json()
    att = client.post(
        f"{API}/items/{item['id']}/attachments",
        params={"filename": "q.csv"},
        content=b"a,b\n1,2\n",
        headers={**alex, "Content-Type": "text/csv"},
    ).json()

    r = client.post(
        f"{API}/attachments/{att['id']}/promote", json={"list_id": drawer["id"]}, headers=gh
    )
    assert r.status_code == 403, r.text
    assert client.get(f"{API}/docs", params={"list_id": drawer["id"]}, headers=alex).json() == []
    still = client.get(f"{API}/items/{item['id']}/attachments", headers=alex).json()
    assert [a["filename"] for a in still] == ["q.csv"]

    r = client.post(
        f"{API}/attachments/{att['id']}/promote", json={"list_id": drawer["id"]}, headers=alex
    )
    assert r.status_code in (200, 201), r.text
    assert [
        d["title"]
        for d in client.get(f"{API}/docs", params={"list_id": drawer["id"]}, headers=alex).json()
    ] == ["q.csv"]
    assert client.get(f"{API}/items/{item['id']}/attachments", headers=alex).json() == []


def test_a_refused_step_of_to_drawer_undoes_the_new_drawer(client, alex, todo, monkeypatch):
    from slate.service import docs as docs_svc
    from slate.service.errors import Invalid

    page = _doc(client, alex, title="Contracts", list_id=todo["id"])
    _doc(client, alex, title="Lease", parent_id=page["id"])
    lists_before = client.get(f"{API}/lists", headers=alex).json()
    real = docs_svc.move_doc
    calls = []

    def failing(session, actor, doc_id, to):
        calls.append(doc_id)
        if len(calls) == 2:
            raise Invalid("refused")
        return real(session, actor, doc_id, to)

    monkeypatch.setattr(docs_svc, "move_doc", failing)
    r = client.post(f"{API}/docs/{page['id']}/to-drawer", headers=alex)
    assert r.status_code == 422, r.text
    assert client.get(f"{API}/lists", headers=alex).json() == lists_before
    got = client.get(f"{API}/docs/{page['id']}", headers=alex).json()
    assert got["list_id"] == todo["id"] and len(got["children"]) == 1


# --- uploads off the event loop ---------------------------------------------------------------


@pytest.mark.parametrize("route", ["files", "revisions"])
def test_a_doc_upload_waiting_for_the_write_lock_does_not_stall_other_requests(
    client, alex, todo, route
):
    import threading
    import time

    from slate.service import core

    if route == "files":
        target = _doc(client, alex, title="Notes", list_id=todo["id"])
        params = {"filename": "a.txt"}
    else:
        target = _doc(client, alex, kind="file", title="Quote")
        params = {"filename": "a.txt", "note": "first"}
    release = threading.Event()
    held = threading.Event()

    def hold() -> None:
        with core._write_lock:  # any slow change holding the lock
            held.set()
            release.wait(5)

    with client:  # one shared event loop, as under uvicorn
        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(2)
        result = {}
        up = threading.Thread(
            target=lambda: result.setdefault(
                "r",
                client.post(
                    f"{API}/docs/{target['id']}/{route}",
                    params=params,
                    content=b"hello",
                    headers={**alex, "Content-Type": "text/plain"},
                ),
            )
        )
        up.start()
        time.sleep(0.3)  # the upload is now waiting for the lock
        t0 = time.perf_counter()
        r = client.get("/healthz")
        waited = time.perf_counter() - t0
        release.set()
        holder.join()
        up.join()
    assert r.status_code == 200 and waited < 1.0, waited
    assert result["r"].status_code == 201, result["r"].text


# --- serving stored files ---------------------------------------------------------------------


ACTIVE = [
    ("page.html", "text/html"),
    ("page.xhtml", "application/xhtml+xml"),
    ("pic.svg", "image/svg+xml"),
    ("feed.xml", "text/xml"),
    ("data.xml", "application/xml"),
]
PASSIVE = [("notes.txt", "text/plain"), ("pic.png", "image/png"), ("scan.pdf", "application/pdf")]


def _stored(client, alex, todo):
    """One URL per way Slate serves an upload: a page's file, a file doc's revision, a task
    attachment. Returns a function that uploads (name, type) and gives back its URL."""
    page = _doc(client, alex, title="Notes", list_id=todo["id"])
    item = client.post(f"{API}/items", json={"list_id": todo["id"], "title": "T"}, headers=alex)
    item_id = item.json()["id"]

    def up(kind, name, ctype):
        if kind == "page":
            url, params = f"{API}/docs/{page['id']}/files", {"filename": name}
        elif kind == "revision":
            d = _doc(client, alex, kind="file", title=name)
            url, params = f"{API}/docs/{d['id']}/revisions", {"filename": name, "note": "first"}
        else:
            url, params = f"{API}/items/{item_id}/attachments", {"filename": name}
        r = client.post(
            url, params=params, content=b"<b>x</b>", headers={**alex, "Content-Type": ctype}
        )
        assert r.status_code == 201, r.text
        return r.json()["url"] if kind != "attachment" else f"{API}/attachments/{r.json()['id']}"

    return up


@pytest.mark.parametrize("kind", ["page", "revision", "attachment"])
def test_markup_that_could_render_as_a_page_is_always_a_download(client, alex, todo, kind):
    up = _stored(client, alex, todo)
    for name, ctype in ACTIVE:
        r = client.get(up(kind, name, ctype), headers=alex)
        assert r.status_code == 200, r.text
        assert r.headers["content-disposition"].startswith("attachment;"), (name, r.headers)
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in r.headers["content-security-policy"]
    for name, ctype in PASSIVE:
        r = client.get(up(kind, name, ctype), headers=alex)
        assert r.headers["content-disposition"].startswith("inline;"), (name, r.headers)
        r = client.get(up(kind, name, ctype), params={"download": "true"}, headers=alex)
        assert r.headers["content-disposition"].startswith("attachment;")


def test_every_upload_route_cleans_name_and_type_the_same_way(client, alex, todo):
    up = _stored(client, alex, todo)
    long_type = "image/" + "x" * 200
    for kind in ("page", "revision", "attachment"):
        r = client.get(up(kind, 'a"b\\c.png', long_type), headers=alex)
        assert r.status_code == 200
        assert len(r.headers["content-type"]) <= 127, kind
        assert "a_b_c.png" in r.headers["content-disposition"], kind


# --- revisions --------------------------------------------------------------------------------


def test_an_upload_that_lands_while_another_is_read_takes_the_next_number(
    client, alex, monkeypatch
):
    """Two uploads to one file doc: the second one's number is chosen when it writes, so an
    upload committed after it looked at the history does not make it collide."""
    from slate.db import SessionLocal
    from slate.models import Source
    from slate.service import docs as docs_svc
    from slate.service.core import Actor

    d = _file_doc(client, alex, "Quote", b"a,b\n1,2\n")
    me = client.get(f"{API}/me", headers=alex).json()
    actor = Actor(account_id=me["account"]["id"], source=Source.api)
    real = docs_svc._revision_row
    raced = []

    def racing(*a, **k):
        if not raced:  # the other upload commits between this one's read and its write
            raced.append(1)
            with SessionLocal() as other:
                docs_svc.add_revision(other, actor, d["id"], filename="b.csv", data=b"b", note="b")
        return real(*a, **k)

    monkeypatch.setattr(docs_svc, "_revision_row", racing)
    with SessionLocal() as mine:
        rev = docs_svc.add_revision(mine, actor, d["id"], filename="a.csv", data=b"a", note="a")
    assert rev.revision_no == 3
    revs = client.get(f"{API}/docs/{d['id']}/revisions", headers=alex).json()
    assert [(r["revision_no"], r["note"]) for r in revs] == [(3, "a"), (2, "b"), (1, "first")]
    assert [r["superseded_at"] is None for r in revs] == [True, False, False]


# --- tags on docs: merge and delete ------------------------------------------------------------


def test_merging_a_tag_moves_it_on_docs_and_records_the_change(client, alex, todo, db):
    doc = _doc(client, alex, title="Policy", list_id=todo["id"], tags=["insurance", "home"])
    both = _doc(client, alex, title="Both", list_id=todo["id"], tags=["insurance", "cover"])
    client.post(f"{API}/tags", json={"name": "cover"}, headers=alex)
    r = client.post(f"{API}/tags/insurance/merge", json={"into": "cover"}, headers=alex)
    assert r.status_code == 200, r.text

    got = client.get(f"{API}/docs/{doc['id']}", headers=alex).json()
    assert got["tags"] == ["cover", "home"]
    assert client.get(f"{API}/docs/{both['id']}", headers=alex).json()["tags"] == ["cover"]
    assert _doc_events(db, doc["id"])[-1] == ("tags", ["home", "insurance"], ["cover", "home"])
    assert _doc_events(db, both["id"])[-1] == ("tags", ["cover", "insurance"], ["cover"])


def test_deleting_a_tag_removes_it_from_docs_with_an_event(client, alex, todo, db):
    doc = _doc(client, alex, title="Policy", list_id=todo["id"], tags=["cover", "home"])
    r = client.delete(f"{API}/tags/cover", headers=alex)
    assert r.status_code in (200, 204), r.text
    assert client.get(f"{API}/docs/{doc['id']}", headers=alex).json()["tags"] == ["home"]
    assert _doc_events(db, doc["id"])[-1] == ("tags", ["cover", "home"], ["home"])
