"""Backlinks follow the page's text, not a scan of every page."""

from tests.conftest import API


def _doc(client, h, **body):
    r = client.post(f"{API}/docs", json={"title": "Page", **body}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_backlinks_follow_edits_restore_duplicate_and_delete(client, alex, todo):
    target = _doc(client, alex, title="Target", list_id=todo["id"])
    link = f"[[doc:{target['id']}]]"
    linker = _doc(client, alex, title="Index", list_id=todo["id"], body_md=f"See {link}")

    def links():
        return [
            b["id"]
            for b in client.get(f"{API}/docs/{target['id']}", headers=alex).json()["backlinks"]
        ]

    assert links() == [linker["id"]]

    # An edit that drops the embed drops the backlink; putting it back brings it.
    r = client.patch(f"{API}/docs/{linker['id']}", json={"body_md": "no link"}, headers=alex)
    assert r.status_code == 200 and links() == []
    r = client.patch(
        f"{API}/docs/{linker['id']}", json={"body_md": f"Back to {link}"}, headers=alex
    )
    assert r.status_code == 200 and links() == [linker["id"]]

    # Restoring an older version moves the backlink with the text.
    client.patch(f"{API}/docs/{linker['id']}", json={"new_version": True}, headers=alex)
    client.patch(f"{API}/docs/{linker['id']}", json={"body_md": "gone again"}, headers=alex)
    assert links() == []
    versions = client.get(f"{API}/docs/{linker['id']}/versions", headers=alex).json()
    linked = next(
        v["version_no"]
        for v in versions
        if link
        in client.get(f"{API}/docs/{linker['id']}/versions/{v['version_no']}", headers=alex).json()[
            "body_md"
        ]
    )
    client.post(f"{API}/docs/{linker['id']}/versions/{linked}/restore", headers=alex)
    assert links() == [linker["id"]]

    # A duplicate carries the link; deleting the copy drops only its own.
    copy = client.post(f"{API}/docs/{linker['id']}/duplicate", json={}, headers=alex).json()
    assert sorted(links()) == sorted([linker["id"], copy["id"]])
    client.post(f"{API}/docs/{copy['id']}/archive", headers=alex)
    client.delete(f"{API}/docs/{copy['id']}", headers=alex)
    assert links() == [linker["id"]]

    # Trashing the linker hides its backlink, the way a listing hides it.
    client.post(f"{API}/docs/{linker['id']}/archive", headers=alex)
    assert links() == []


def test_a_page_read_finds_backlinks_in_a_fixed_number_of_queries(client, alex, todo):
    """Backlinks come from the doc_links index, so a page read costs the same however many
    pages the workspace holds."""
    from tests.test_doc_writes import _Statements

    target = _doc(client, alex, title="Target", list_id=todo["id"])
    link = f"[[doc:{target['id']}]]"
    for n in range(6):
        _doc(client, alex, title=f"Page {n}", list_id=todo["id"], body_md=link if n % 2 else "x")

    def link_queries(sql):
        return [s for s in sql if "doc_links" in s]

    with _Statements() as st:
        r = client.get(f"{API}/docs/{target['id']}", headers=alex)
    assert r.status_code == 200 and len(r.json()["backlinks"]) == 3
    first = link_queries(st.sql)
    assert first and all("LIKE" not in s for s in st.sql)

    for n in range(6, 12):
        _doc(client, alex, title=f"Page {n}", list_id=todo["id"], body_md="more text")
    with _Statements() as st:
        r = client.get(f"{API}/docs/{target['id']}", headers=alex)
    assert len(r.json()["backlinks"]) == 3
    assert len(link_queries(st.sql)) == len(first)


def test_the_embed_filter_reads_the_link_table(client, alex, todo):
    task = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Call"}, headers=alex
    ).json()
    page = _doc(client, alex, title="Plan", list_id=todo["id"], body_md=f"[[task:{task['id']}]]")
    other = _doc(client, alex, title="Other", list_id=todo["id"], body_md="nothing here")
    hits = client.get(f"{API}/docs", params={"embeds": task["id"]}, headers=alex).json()
    assert [d["id"] for d in hits] == [page["id"]] and other["id"] not in [d["id"] for d in hits]

    # Editing the embed out drops the page from the filter.
    client.patch(f"{API}/docs/{page['id']}", json={"body_md": "done"}, headers=alex)
    assert client.get(f"{API}/docs", params={"embeds": task["id"]}, headers=alex).json() == []


def test_a_repeated_embed_is_one_link(client, alex, todo):
    target = _doc(client, alex, title="Target", list_id=todo["id"])
    link = f"[[doc:{target['id']}]]"
    _doc(client, alex, title="Index", list_id=todo["id"], body_md=f"{link} and again {link}")
    got = client.get(f"{API}/docs/{target['id']}", headers=alex).json()["backlinks"]
    assert len(got) == 1
