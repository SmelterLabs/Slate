"""Docs: nested pages, autosave folding into page history, embeds, agent edits, files."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from slate.models import DocVersion, Event
from slate.service import docs

API = "/api/v1"


def _doc(client, h, **body):
    r = client.post(f"{API}/docs", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _patch(client, h, doc_id, status=200, **body):
    r = client.patch(f"{API}/docs/{doc_id}", json=body, headers=h)
    assert r.status_code == status, r.text
    return r.json()


def _types(db, doc_id):
    db.expire_all()
    return [
        e.type
        for e in db.scalars(select(Event).where(Event.subject_id == doc_id).order_by(Event.id))
    ]


def test_create_nests_and_lists_in_tree_order(client, alex, todo):
    top = _doc(client, alex, title="Plan", list_id=todo["id"])
    assert top["section_id"] == todo["section_id"] and top["list_id"] == todo["id"]
    a = _doc(client, alex, title="A", parent_id=top["id"])
    b = _doc(client, alex, title="B", parent_id=top["id"])
    a1 = _doc(client, alex, title="A1", parent_id=a["id"])
    first = _doc(client, alex, title="First", parent_id=top["id"], before_id=a["id"])
    ws = _doc(client, alex, title="Workspace note")
    assert a["list_id"] is None  # subpages inherit their top page's place

    listed = client.get(f"{API}/docs", headers=alex).json()
    assert [d["title"] for d in listed] == ["Plan", "First", "A", "A1", "B", "Workspace note"]
    in_list = client.get(f"{API}/docs", params={"list_id": todo["id"]}, headers=alex).json()
    assert {d["id"] for d in in_list} == {top["id"], a["id"], b["id"], a1["id"], first["id"]}
    only_ws = client.get(f"{API}/docs", params={"workspace": True}, headers=alex).json()
    assert [d["id"] for d in only_ws] == [ws["id"]]

    got = client.get(f"{API}/docs/{a1['id']}", headers=alex).json()
    assert [p["title"] for p in got["path"]] == ["Plan", "A"]
    assert [
        c["title"] for c in client.get(f"{API}/docs/{top['id']}", headers=alex).json()["children"]
    ] == ["First", "A", "B"]


def test_template_fills_what_the_caller_left_out(client, alex):
    d = _doc(client, alex, template="meeting")
    assert d["title"] == "Meeting notes" and d["icon"] == "🗓️"
    assert [h["text"] for h in d["outline"]][:2] == ["Attendees", "Agenda"]
    names = [t["key"] for t in client.get(f"{API}/docs/templates", headers=alex).json()]
    assert "plan" in names and "agent_log" in names
    r = client.post(f"{API}/docs", json={"template": "nope"}, headers=alex)
    assert r.status_code == 422


def test_autosaves_fold_into_one_version_until_a_pause_or_another_author(
    client, alex, make_account, db
):
    d = _doc(client, alex, title="Notes", body_md="one")
    for text in ("one two", "one two three"):
        d = _patch(client, alex, d["id"], body_md=text, base_revision=d["revision"])
    assert d["revision"] == 3 and d["version_no"] == 1
    versions = client.get(f"{API}/docs/{d['id']}/versions", headers=alex).json()
    assert len(versions) == 1 and versions[0]["chars"] == len("one two three")

    _, agent = make_account("agent")
    d = _patch(client, agent, d["id"], append_md="From Agent.")
    assert d["version_no"] == 2 and d["body_md"] == "one two three\n\nFrom Agent.\n"

    # A pause longer than FOLD_MINUTES starts a new version even for the same author.
    v2 = db.scalar(select(DocVersion).where(DocVersion.version_no == 2))
    from slate.models import Source
    from slate.service.core import Actor, apply_change

    with apply_change(db, Actor(account_id=v2.author_id, source=Source.api)) as cx:
        cx.set(v2, "updated_at", v2.updated_at - timedelta(minutes=30), "doc.updated")
    d = _patch(client, agent, d["id"], append_md="Later.")
    assert d["version_no"] == 3

    # Every save is still an event.
    assert _types(db, d["id"]).count("doc.updated") == 4


def test_stale_base_revision_is_a_conflict_and_changes_nothing(client, alex):
    d = _doc(client, alex, title="Plan", body_md="v1")
    _patch(client, alex, d["id"], body_md="v2", base_revision=d["revision"])
    r = client.patch(
        f"{API}/docs/{d['id']}",
        json={"body_md": "mine", "base_revision": d["revision"]},
        headers=alex,
    )
    assert r.status_code == 409 and "revision" in r.json()["detail"]
    assert client.get(f"{API}/docs/{d['id']}", headers=alex).json()["body_md"] == "v2"


def test_replace_section_rewrites_one_heading(client, alex):
    body = "# Plan\n\n## Status\n\nold\n\n### Detail\n\nkeep?\n\n## Next\n\nstays\n"
    d = _doc(client, alex, title="Plan", body_md=body)
    d = _patch(client, alex, d["id"], replace_section={"heading": "status", "body_md": "new"})
    assert d["body_md"] == "# Plan\n\n## Status\n\nnew\n\n## Next\n\nstays\n"
    d = _patch(client, alex, d["id"], replace_section={"heading": "Risks", "body_md": "none"})
    assert d["body_md"].endswith("## Next\n\nstays\n\n## Risks\n\nnone\n")


def test_named_version_history_diff_and_restore(client, alex):
    d = _doc(client, alex, title="Spec", body_md="alpha\nbeta\n")
    d = _patch(client, alex, d["id"], note="First draft")
    assert d["version_no"] == 1
    d = _patch(client, alex, d["id"], body_md="alpha\ngamma\n")
    assert d["version_no"] == 2  # the named version is closed
    diff = client.get(f"{API}/docs/{d['id']}/diff", params={"from": 1}, headers=alex).json()
    assert diff["added"] == 1 and diff["removed"] == 1 and "+gamma" in diff["diff"]

    v1 = client.get(f"{API}/docs/{d['id']}/versions/1", headers=alex).json()
    assert v1["note"] == "First draft" and v1["body_md"] == "alpha\nbeta\n"
    r = client.post(f"{API}/docs/{d['id']}/versions/1/restore", headers=alex)
    assert r.status_code == 200, r.text
    assert r.json()["body_md"] == "alpha\nbeta\n" and r.json()["version_no"] == 3
    notes = [v["note"] for v in client.get(f"{API}/docs/{d['id']}/versions", headers=alex).json()]
    assert notes[0] == "Restored version 1"


def test_embeds_resolve_to_live_task_views_and_backlinks(client, alex, todo):
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Buy paint"}, headers=alex
    ).json()
    other = _doc(client, alex, title="Other")
    d = _doc(
        client,
        alex,
        title="Room",
        body_md=f"Steps\n\n[[task:{item['id']}]]\n\nSee [[doc:{other['id']}]] and "
        "[[task:ZZZZZZZZZZZZ]]",
    )
    assert [t["title"] for t in d["tasks"]] == ["Buy paint"]
    assert d["missing_tasks"] == ["ZZZZZZZZZZZZ"]
    assert d["docs"] == [{"id": other["id"], "title": "Other", "icon": None, "kind": "page"}]
    got = client.get(f"{API}/docs/{other['id']}", headers=alex).json()
    assert [b["id"] for b in got["backlinks"]] == [d["id"]]
    by_task = client.get(f"{API}/docs", params={"embeds": item["id"]}, headers=alex).json()
    assert [x["id"] for x in by_task] == [d["id"]]


def test_create_task_from_selection(client, alex, todo, db):
    d = _doc(
        client, alex, title="Plan", list_id=todo["id"], body_md="- call the bank\n- call the bank\n"
    )
    r = client.post(
        f"{API}/docs/{d['id']}/tasks",
        json={
            "title": "Call the bank",
            "replace": "call the bank",
            "occurrence": 1,
            "priority": "high",
        },
        headers=alex,
    )
    assert r.status_code == 201, r.text
    item, doc = r.json()["item"], r.json()["doc"]
    assert item["list_id"] == todo["id"] and item["priority"] == "high"
    assert f"[[doc:{d['id']}]]" in item["body_md"]
    assert doc["body_md"] == f"- call the bank\n- [[task:{item['id']}]]\n"
    assert [t["id"] for t in doc["tasks"]] == [item["id"]]
    # Exactly one item was filed (rule 1).
    assert len(client.get(f"{API}/items", headers=alex).json()) == 1

    ws = _doc(client, alex, title="Loose")
    r = client.post(f"{API}/docs/{ws['id']}/tasks", json={"title": "X"}, headers=alex)
    assert r.status_code == 422 and "list_id" in r.json()["detail"]
    r = client.post(
        f"{API}/docs/{d['id']}/tasks", json={"title": "Y", "replace": "absent"}, headers=alex
    )
    assert r.status_code == 422


def test_move_reorders_and_refuses_cycles_and_depth(client, alex, todo):
    a = _doc(client, alex, title="A")
    b = _doc(client, alex, title="B")
    c = _doc(client, alex, title="C", parent_id=a["id"])
    r = client.post(f"{API}/docs/{a['id']}/move", json={"parent_id": c["id"]}, headers=alex)
    assert r.status_code == 422
    r = client.post(f"{API}/docs/{b['id']}/move", json={"before_id": a["id"]}, headers=alex)
    assert r.status_code == 200
    assert [d["title"] for d in client.get(f"{API}/docs", headers=alex).json()] == [
        "B",
        "A",
        "C",
    ]
    moved = client.post(
        f"{API}/docs/{a['id']}/move", json={"list_id": todo["id"]}, headers=alex
    ).json()
    assert moved["list_id"] == todo["id"] and moved["section_id"] == todo["section_id"]
    under = client.post(f"{API}/docs/{b['id']}/move", json={"parent_id": c["id"]}, headers=alex)
    assert under.json()["parent_id"] == c["id"] and under.json()["list_id"] is None

    chain = [_doc(client, alex, title="L1")]
    for n in range(2, docs.MAX_DEPTH + 1):
        chain.append(_doc(client, alex, title=f"L{n}", parent_id=chain[-1]["id"]))
    r = client.post(
        f"{API}/docs", json={"title": "too deep", "parent_id": chain[-1]["id"]}, headers=alex
    )
    assert r.status_code == 422


def test_archive_hides_subtree_and_delete_needs_archive(client, alex, db):
    a = _doc(client, alex, title="A")
    c = _doc(client, alex, title="C", parent_id=a["id"])
    r = client.delete(f"{API}/docs/{a['id']}", headers=alex)
    assert r.status_code == 422
    client.post(f"{API}/docs/{a['id']}/archive", headers=alex)
    assert client.get(f"{API}/docs", headers=alex).json() == []
    archived = client.get(f"{API}/docs", params={"archived": True}, headers=alex).json()
    assert [d["id"] for d in archived] == [a["id"]]
    r = client.delete(f"{API}/docs/{a['id']}", headers=alex)
    assert r.status_code == 200 and r.json()["deleted"] == [c["id"], a["id"]]
    assert client.get(f"{API}/docs/{a['id']}", headers=alex).status_code == 404
    assert "doc.deleted" in _types(db, c["id"])
    assert db.scalars(select(DocVersion)).all() == []


def test_duplicate_copies_subpages(client, alex):
    a = _doc(client, alex, title="A", body_md="hello")
    _doc(client, alex, title="C", parent_id=a["id"])
    _doc(client, alex, title="Z")
    r = client.post(f"{API}/docs/{a['id']}/duplicate", json={}, headers=alex)
    assert r.status_code == 201, r.text
    copy = r.json()
    assert copy["title"] == "A (copy)" and copy["body_md"] == "hello"
    assert [c["title"] for c in copy["children"]] == ["C"]
    titles = [d["title"] for d in client.get(f"{API}/docs", headers=alex).json()]
    assert titles == ["A", "C", "A (copy)", "C", "Z"]


def test_search_returns_snippets(client, alex):
    _doc(client, alex, title="Garden", body_md="Plant the tomatoes in May after the frost.")
    _doc(client, alex, title="Tomato soup", body_md="")
    hits = client.get(f"{API}/docs", params={"q": "tomato"}, headers=alex).json()
    assert {h["title"] for h in hits} == {"Garden", "Tomato soup"}
    garden = next(h for h in hits if h["title"] == "Garden")
    assert "tomatoes" in garden["snippet"]


def test_files_upload_serve_and_remove(client, alex, db):
    d = _doc(client, alex, title="Pics")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    r = client.post(
        f"{API}/docs/{d['id']}/files",
        params={"filename": "shot.png"},
        content=png,
        headers={**alex, "Content-Type": "image/png"},
    )
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["markdown"] == f"![shot.png](/api/v1/doc-files/{f['id']})"
    got = client.get(f"{API}/doc-files/{f['id']}", headers=alex)
    assert got.content == png and got.headers["content-type"] == "image/png"
    assert client.get(f"{API}/docs/{d['id']}", headers=alex).json()["files"][0]["id"] == f["id"]
    ev = db.scalar(select(Event).where(Event.type == "doc.file_added"))
    assert "data" not in ev.after_json
    assert client.delete(f"{API}/doc-files/{f['id']}", headers=alex).status_code == 204
    assert client.get(f"{API}/doc-files/{f['id']}", headers=alex).status_code == 404


def test_docs_in_an_archived_list_are_hidden(client, alex, todo):
    _doc(client, alex, title="In list", list_id=todo["id"])
    client.patch(f"{API}/lists/{todo['id']}", json={"archived": True}, headers=alex)
    assert client.get(f"{API}/docs", headers=alex).json() == []


def test_favorite_a_doc(client, alex):
    d = _doc(client, alex, title="Pinned", icon="📌")
    r = client.post(f"{API}/favorites", json={"kind": "doc", "target_id": d["id"]}, headers=alex)
    assert r.status_code in (200, 201), r.text
    fav = r.json()
    assert fav["name"] == "📌 Pinned" and fav["scope_kind"] == "doc" and fav["available"]


def test_export_carries_the_docs_in_scope(client, alex, todo):
    mine = _doc(client, alex, title="List doc", list_id=todo["id"], body_md="hi")
    _doc(client, alex, title="Sub", parent_id=mine["id"])
    _doc(client, alex, title="Elsewhere")
    out = client.get(f"{API}/export", params={"list_id": todo["id"]}, headers=alex).json()
    assert [d["title"] for d in out["docs"]] == ["List doc", "Sub"]
    assert out["docs"][0]["body_md"] == "hi"
    everything = client.get(f"{API}/export", headers=alex).json()
    assert len(everything["docs"]) == 3


def test_mentions_in_a_doc_reach_inbox_checkin_and_push(client, alex, todo, make_account, db):
    from slate.service import push

    _, agent = make_account("agent")
    page = _doc(client, alex, title="Roof plan", body_md="Intro")
    # Autosave sends the body again and again: only a newly added handle is news.
    _patch(client, alex, page["id"], body_md="Intro\n\n@agent please price the shingles")
    _patch(client, alex, page["id"], body_md="Intro\n\n@agent please price the shingles!")
    assert _types(db, page["id"]).count("doc.mentioned") == 1

    news = client.get(f"{API}/checkin", headers=agent).json()["news"]
    assert [(n["type"], n["why"]) for n in news] == [("doc.mentioned", "mentioned")]
    entries = client.get(f"{API}/inbox", headers=agent).json()
    assert [(e["reason"], e["doc_id"], e["item_title"]) for e in entries] == [
        ("mention", page["id"], "Roof plan")
    ]
    # The author hears nothing about their own page.
    assert client.get(f"{API}/inbox", headers=alex).json() == []

    ev = db.scalars(select(Event).where(Event.type == "doc.mentioned")).one()
    note = push.notification_for(db, ev, "mention")
    assert note["title"] == "Alex mentioned you in Roof plan"
    assert note["body"] == "@agent please price the shingles"
    assert note["url"] == f"/#/docs/{page['id']}"


def test_a_new_page_with_a_mention_notifies(client, alex, make_account, db):
    _, agent = make_account("agent")
    page = _doc(client, alex, title="Notes", body_md="Hi @agent and @nobody")
    ev = db.scalars(select(Event).where(Event.type == "doc.mentioned")).one()
    assert ev.after_json["mentions"] == ["agent"] and ev.subject_id == page["id"]
