"""Export (JSON and CSV, per list, section or everything) and the admin backup download."""

from __future__ import annotations

import csv
import io
import sqlite3

import pytest

API = "/api/v1"


def new_item(client, headers, todo, title="Thing", **extra):
    r = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": title, **extra}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def other_list(client, alex, name="Other"):
    section = client.post(
        f"{API}/sections", json={"name": "Work", "audience": "work"}, headers=alex
    ).json()
    return client.post(
        f"{API}/lists", json={"section_id": section["id"], "name": name}, headers=alex
    ).json()


def test_json_export_of_a_list(client, alex, todo):
    item = new_item(client, alex, todo, title="Write report", tags=["home"], priority="high")
    sub = new_item(client, alex, todo, title="Outline", parent_id=item["id"])
    archived = new_item(client, alex, todo, title="Old")
    trashed = new_item(client, alex, todo, title="Gone")
    client.post(f"{API}/items/{archived['id']}/archive", headers=alex)
    client.post(f"{API}/items/{trashed['id']}/trash", headers=alex)
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "hi"}, headers=alex)
    elsewhere = new_item(client, alex, other_list(client, alex), title="Elsewhere")

    r = client.get(f"{API}/export", params={"list_id": todo["id"]}, headers=alex)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    assert 'filename="slate-todo-' in r.headers["content-disposition"]
    data = r.json()
    assert data["format"] == "slate-export" and data["version"] == 1
    assert data["scope"] == {"kind": "list", "id": todo["id"], "name": "Todo"}
    got = {i["id"] for i in data["items"]}
    assert got == {item["id"], sub["id"], archived["id"]}
    assert trashed["id"] not in got and elsewhere["id"] not in got
    assert [lst["id"] for lst in data["lists"]] == [todo["id"]]
    assert data["lists"][0]["statuses"]
    assert [c["body_md"] for c in data["comments"]] == ["hi"]
    assert any(a["handle"] == "alex" for a in data["accounts"])

    r = client.get(
        f"{API}/export", params={"list_id": todo["id"], "include_archived": "false"}, headers=alex
    )
    assert archived["id"] not in {i["id"] for i in r.json()["items"]}


def test_json_export_of_everything_and_a_section(client, alex, todo):
    new_item(client, alex, todo, title="Mine")
    work = other_list(client, alex)
    new_item(client, alex, work, title="Work thing")
    everything = client.get(f"{API}/export", headers=alex).json()
    assert everything["scope"]["kind"] == "everything"
    assert {i["title"] for i in everything["items"]} == {"Mine", "Work thing"}
    assert len(everything["sections"]) == 2

    section = client.get(
        f"{API}/export", params={"section_id": work["section_id"]}, headers=alex
    ).json()
    assert [i["title"] for i in section["items"]] == ["Work thing"]


def test_csv_export(client, alex, todo, make_account):
    agent, _ = make_account("agent")
    new_item(
        client,
        alex,
        todo,
        title='Comma, "quote"',
        tags=["a", "b"],
        assignees=["agent"],
        estimate_minutes=60,
        body_md="line one\nline two",
    )
    r = client.get(f"{API}/export", params={"format": "csv"}, headers=alex)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert r.headers["content-disposition"].endswith('.csv"')
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == 'Comma, "quote"' and row["list"] == "Todo"
    assert row["section"] == "Personal" and row["status_kind"] == "open"
    assert row["assignees"] == "agent" and row["tags"] == "a; b"
    assert row["estimate_minutes"] == "60"
    assert row["created_by"] == "alex" and row["description"] == "line one\nline two"


def test_export_unknown_scope_is_404(client, alex):
    assert client.get(f"{API}/export", params={"list_id": "nope"}, headers=alex).status_code == 404
    r = client.get(f"{API}/export", params={"format": "xml"}, headers=alex)
    assert r.status_code == 422


def test_backup_download_is_for_admins(client, alex, todo, make_account, tmp_path):
    _, agent = make_account("agent", scopes=("write",))
    new_item(client, alex, todo, title="Backed up")
    assert client.get(f"{API}/admin/backup", headers=agent).status_code == 403
    r = client.get(f"{API}/admin/backup", headers=alex)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/vnd.sqlite3"
    assert "slate-backup-" in r.headers["content-disposition"]
    path = tmp_path / "copy.db"
    path.write_bytes(r.content)
    with sqlite3.connect(path) as conn:
        titles = [t for (t,) in conn.execute("select title from items")]
    assert titles == ["Backed up"]


def test_json_export_carries_the_scope_chat(client, alex, todo):
    work = other_list(client, alex)
    root = client.post(
        f"{API}/channels/list:{todo['id']}/messages",
        json={"body_md": "Shopping today"},
        headers=alex,
    ).json()
    reply = client.post(
        f"{API}/channels/list:{todo['id']}/messages",
        json={"body_md": "Got it", "parent_id": root["id"]},
        headers=alex,
    ).json()
    client.post(f"{API}/messages/{root['id']}/reactions", json={"emoji": "👍"}, headers=alex)
    client.post(
        f"{API}/channels/list:{work['id']}/messages", json={"body_md": "Standup"}, headers=alex
    )
    client.post(f"{API}/channels/general/messages", json={"body_md": "Hello all"}, headers=alex)
    client.post(
        f"{API}/channels/section:{work['section_id']}/messages",
        json={"body_md": "Section news"},
        headers=alex,
    )

    data = client.get(f"{API}/export", params={"list_id": todo["id"]}, headers=alex).json()
    assert [(m["id"], m["parent_id"]) for m in data["messages"]] == [
        (root["id"], None),
        (reply["id"], root["id"]),
    ]
    assert data["messages"][0]["channel"] == f"list:{todo['id']}"
    assert [(r["message_id"], r["emoji"]) for r in data["message_reactions"]] == [
        (root["id"], "👍")
    ]

    section = client.get(
        f"{API}/export", params={"section_id": work["section_id"]}, headers=alex
    ).json()
    assert {m["body_md"] for m in section["messages"]} == {"Standup", "Section news"}
    assert section["message_reactions"] == []

    everything = client.get(f"{API}/export", headers=alex).json()
    assert {m["body_md"] for m in everything["messages"]} == {
        "Shopping today",
        "Got it",
        "Standup",
        "Hello all",
        "Section news",
    }


def test_csv_cells_that_a_spreadsheet_would_run_are_quoted(client, alex, todo):
    for title in ('=HYPERLINK("https://example.com","x")', "+1+2", "-2+3", "@SUM(A1)"):
        new_item(client, alex, todo, title=title, body_md="\tlead\ttab", tags=["=t"])
    new_item(client, alex, todo, title="plain - title", body_md="ok")
    r = client.get(f"{API}/export", params={"format": "csv"}, headers=alex)
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    for row in rows:
        for column, cell in row.items():
            assert not cell or cell[0] not in "=+-@\t\r", (column, cell)
    titles = {row["title"] for row in rows}
    assert '\'=HYPERLINK("https://example.com","x")' in titles and "plain - title" in titles
    assert "'+1+2" in titles and {row["tags"] for row in rows} >= {"'=t"}


def test_a_failed_backup_leaves_no_temp_folder(client, alex, monkeypatch, tmp_path):
    import tempfile

    from slate.service import export

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def full_disk(session, path):
        path.write_bytes(b"partial copy")
        raise OSError("no space left on device")

    monkeypatch.setattr(export, "backup_to", full_disk)
    with pytest.raises(OSError):
        client.get(f"{API}/admin/backup", headers=alex)
    assert list(tmp_path.iterdir()) == []


def test_the_audit_csv_quotes_formula_cells_too(client, alex, todo):
    new_item(client, alex, todo, title="=1+1")
    r = client.get(f"{API}/audit.csv", headers=alex)
    assert r.status_code == 200, r.text
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert "'=1+1" in {row["item_title"] for row in rows}
    assert all(not v or v[0] not in "=+-@\t\r" for row in rows for v in row.values())
