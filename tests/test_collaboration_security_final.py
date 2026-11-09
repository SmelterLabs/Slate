"""Synthetic revocation and resource-bound acceptance; no network senders."""

import io
import json
import tracemalloc
import zipfile
from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import API
from slate.db import SessionLocal
from slate.models import Account, Event, utcnow
from slate.service import access, doc_compare, push, reminders
from slate.service.core import event_payload
from test_assigned_comments import _item
from test_collaboration_attention import guest
from test_push import Browser


def setup_digest(client, alex, todo, db, monkeypatch, partial=False):
    account, headers, share = guest(client, alex, todo)
    assert (
        client.post(API + "/push/subscriptions", headers=headers, json=Browser().body()).status_code
        == 201
    )
    assert (
        client.patch(
            API + "/me/prefs",
            headers=headers,
            json={"push_reasons": ["digest"], "digest": True, "digest_hour": 0},
        ).status_code
        == 200
    )
    task = _item(
        client,
        alex,
        todo["id"],
        title="CONFIDENTIAL AFTER REVOCATION",
        assignees=["guest"],
        due_at="2026-01-01",
    )
    if partial:
        other = client.post(
            API + "/lists",
            headers=alex,
            json={"section_id": todo["section_id"], "name": "Still shared"},
        ).json()
        assert (
            client.put(
                API + "/shares",
                headers=alex,
                json={
                    "account_id": account["id"],
                    "scope_kind": "list",
                    "scope_id": other["id"],
                    "level": "view",
                },
            ).status_code
            == 200
        )
        _item(
            client,
            alex,
            other["id"],
            title="STILL AUTHORIZED",
            assignees=["guest"],
            due_at="2026-01-01",
        )
    now = utcnow()
    monkeypatch.setattr(push, "utcnow", lambda: now)
    push.process(db, lambda *args: (201, ""))
    reminders.send_digests(db, now=now)
    ev = db.scalar(
        select(Event).where(Event.type == "account.digest", Event.subject_id == account["id"])
    )
    assert ev is not None
    assert "CONFIDENTIAL" in json.dumps(ev.after_json)
    assert client.delete(f"{API}/shares/{share['id']}", headers=alex).status_code == 204
    assert client.get(f"{API}/items/{task['id']}", headers=headers).status_code == 404
    return account, headers, ev.id, now


@pytest.mark.parametrize("partial", [False, True])
def test_historical_digest_surfaces_revalidate_nested_items(
    client, alex, todo, db, monkeypatch, partial
):
    account, headers, event_id, _ = setup_digest(client, alex, todo, db, monkeypatch, partial)
    for path in (
        "/inbox",
        "/events?since=0&limit=1000",
        "/checkin",
        "/audit?limit=500",
        "/audit.csv",
        "/export",
        "/me/digest",
    ):
        response = client.get(API + path, headers=headers)
        assert response.status_code == 200, response.text
        assert "CONFIDENTIAL AFTER REVOCATION" not in response.text, path
    page = client.get(API + "/events?type=account.digest", headers=headers).json()["events"]
    body = next(e for e in page if e["id"] == event_id)["after"]
    assert body["counts"] == {"overdue": int(partial), "today": 0, "waiting": 0}
    if partial:
        assert [i["title"] for i in body["overdue"]] == ["STILL AUTHORIZED"]
    # Projection must not rewrite the historical actor/event or poison another viewer.
    original = db.get(Event, event_id)
    assert "CONFIDENTIAL" in json.dumps(original.after_json)
    owner = client.get(API + "/events?type=account.digest", headers=alex).json()["events"]
    assert "CONFIDENTIAL" in json.dumps(owner)
    gate = access.EventGate(account["id"])
    gate.load(db)
    payload = event_payload(original)
    # Boolean consumers (webhook fan-out) must not accept an unsanitized mixed payload.
    assert not gate.allows(db, payload)


def test_digest_generation_does_not_treat_assignment_as_access(client, alex, todo, db, monkeypatch):
    account, headers, _, now = setup_digest(client, alex, todo, db, monkeypatch)
    reminders.send_digests(db, now=now + timedelta(days=1))
    rows = list(
        db.scalars(
            select(Event).where(Event.type == "account.digest", Event.subject_id == account["id"])
        )
    )
    assert len(rows) == 1
    assert reminders.digest_for(db, db.get(Account, account["id"]), now=now)["counts"] == {
        "overdue": 0,
        "today": 0,
        "waiting": 0,
    }


@pytest.mark.parametrize("partial", [False, True])
def test_digest_retry_after_revoke_is_dropped_or_safely_partial(
    client, alex, todo, db, monkeypatch, partial
):
    # Queue while authorized, fail, revoke, then restart the delivery worker.
    account, headers, share = guest(client, alex, todo)
    client.post(API + "/push/subscriptions", headers=headers, json=Browser().body())
    client.patch(
        API + "/me/prefs",
        headers=headers,
        json={"push_reasons": ["digest"], "digest": True, "digest_hour": 0},
    )
    _item(
        client,
        alex,
        todo["id"],
        title="CONFIDENTIAL AFTER REVOCATION",
        assignees=["guest"],
        due_at="2026-01-01",
    )
    if partial:
        other = client.post(
            API + "/lists", headers=alex, json={"section_id": todo["section_id"], "name": "Other"}
        ).json()
        client.put(
            API + "/shares",
            headers=alex,
            json={
                "account_id": account["id"],
                "scope_kind": "list",
                "scope_id": other["id"],
                "level": "view",
            },
        )
        _item(
            client,
            alex,
            other["id"],
            title="STILL AUTHORIZED",
            assignees=["guest"],
            due_at="2026-01-01",
        )
    now = utcnow()
    monkeypatch.setattr(push, "utcnow", lambda: now)
    push.process(db, lambda *args: (201, ""))
    reminders.send_digests(db, now=now)
    sent = []

    def sender(*args):
        sent.append(args[3])
        return (503, "HTTP 503") if len(sent) == 1 else (201, "")

    assert push.process(db, sender) == 0
    assert len(sent) == 1
    client.delete(f"{API}/shares/{share['id']}", headers=alex)
    now += timedelta(minutes=2)
    with SessionLocal() as restarted:
        assert push.process(restarted, sender) == int(partial)
    assert len(sent) == 1 + int(partial)
    if partial:
        assert sent[-1]["body"] == "1 overdue: STILL AUTHORIZED"


def docx(xml):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return stream.getvalue()


def test_docx_nested_paragraphs_rejected_without_amplification():
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        + "<w:p>" * 1000
        + "<w:t>"
        + "A" * 10000
        + "</w:t>"
        + "</w:p>" * 1000
        + "</w:document>"
    ).encode()
    data = docx(xml)
    tracemalloc.start()
    try:
        result = doc_compare.extract_text("hostile.docx", "", data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result is None
    assert peak < 2_000_000


def test_docx_output_budget_applies_before_join(monkeypatch):
    monkeypatch.setattr(doc_compare, "MAX_TEXT", 1000)
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        + ("<w:p><w:r><w:t>" + "A" * 10000 + "</w:t></w:r></w:p>") * 100
        + "</w:document>"
    ).encode()
    data = docx(xml)
    tracemalloc.start()
    try:
        result = doc_compare._docx_text(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == "A" * 1000
    assert peak < 2_500_000


def test_digest_live_projection_is_per_recipient(client, alex, todo, db, monkeypatch):
    from slate.api.events import Filters, _gate_check, _read_page

    account, headers, event_id, _ = setup_digest(client, alex, todo, db, monkeypatch, True)
    payload = event_payload(db.get(Event, event_id))
    guest_gate = access.EventGate(account["id"])
    projected = _gate_check(guest_gate, payload)
    assert projected["after"]["counts"]["overdue"] == 1
    assert "CONFIDENTIAL" not in json.dumps(projected)
    assert "CONFIDENTIAL" in json.dumps(payload)
    replay = _read_page("0", Filters.parse("account.digest", None), guest_gate)
    assert replay[0]["after"] == projected["after"]
    owner_id = client.get(API + "/me", headers=alex).json()["account"]["id"]
    assert _gate_check(access.EventGate(owner_id), payload) == payload


def test_queued_webhook_digest_is_suppressed_after_revoke(client, alex, todo, db, monkeypatch):
    from slate.models import Delivery, Source
    from slate.service import deliveries, webhooks
    from slate.service.core import Actor

    account, _, event_id, now = setup_digest(client, alex, todo, db, monkeypatch, True)
    hook, _ = webhooks.create_webhook(
        db,
        Actor(account["id"], Source.api),
        url="https://rx.example.com/slate",
        events=["account.digest"],
    )
    original = deliveries.delivery_body(db, db.get(Event, event_id))
    row = Delivery(webhook_id=hook.id, event_id=event_id, attempt=2, state="sending", body=original)
    db.add(row)
    db.commit()
    assert deliveries.prepare_attempt(db, row.id, now=now) is None
    db.refresh(row)
    assert row.state == "skipped"
    assert row.body == original


def test_snapshot_counts_never_reuse_unverifiable_totals(client, alex, todo, db):
    account, _, _ = guest(client, alex, todo)
    task = _item(client, alex, todo["id"], title="Safe")
    caller = access.compute(db, db.get(Account, account["id"]))
    body = {
        "date": "2026-11-02",
        "counts": {k: 999 for k in access.DIGEST_GROUPS},
        **{
            k: [{"id": task["id"], "title": "Safe"}, {"id": "missing", "title": "Secret"}]
            for k in access.DIGEST_GROUPS
        },
    }
    safe = access.digest_snapshot(db, body, caller)
    assert safe["counts"] == {k: 1 for k in access.DIGEST_GROUPS}
    assert "Secret" not in json.dumps(safe)
    assert body["counts"]["overdue"] == 999


@pytest.mark.parametrize(
    "inner",
    [
        "<w:r>" * 200 + "<w:t>A</w:t>" + "</w:r>" * 200,
        "<w:t><w:t>A</w:t></w:t>",
    ],
)
def test_docx_rejects_deep_or_nested_text(inner):
    data = docx(
        (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p>'
            + inner
            + "</w:p></w:document>"
        ).encode()
    )
    assert doc_compare.extract_text("hostile.docx", "", data) is None


def test_docx_budget_preserves_paragraphs_and_validates_tail(monkeypatch):
    monkeypatch.setattr(doc_compare, "MAX_TEXT", 5)
    prefix = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    valid = (
        prefix
        + "<w:p><w:r><w:t>AB</w:t></w:r></w:p><w:p><w:r><w:t>CDmore</w:t></w:r></w:p></w:document>"
    )
    assert doc_compare._docx_text(docx(valid.encode())) == "AB\nCD"
    invalid = valid[:-13] + "<w:p><w:p/></w:p></w:document>"
    assert doc_compare.extract_text("invalid.docx", "", docx(invalid.encode())) is None


_OOXML = "http://schemas.openxmlformats.org"
_REL = f"{_OOXML}/officeDocument/2006/relationships"
_SML = f"{_OOXML}/spreadsheetml/2006/main"
_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml"
_PARTS = {
    "[Content_Types].xml": (
        f'<Types xmlns="{_OOXML}/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'  # noqa: E501
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{_CT}.sheet.main+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{_CT}.worksheet+xml"/>'
        f'<Override PartName="/xl/sharedStrings.xml" ContentType="{_CT}.sharedStrings+xml"/>'
        "</Types>"
    ),
    "_rels/.rels": (
        f'<Relationships xmlns="{_OOXML}/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    ),
    "xl/workbook.xml": (
        f'<workbook xmlns="{_SML}" xmlns:r="{_REL}">'
        '<sheets><sheet name="Quote" sheetId="1" r:id="rId1"/></sheets></workbook>'
    ),
    "xl/_rels/workbook.xml.rels": (
        f'<Relationships xmlns="{_OOXML}/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{_REL}/sharedStrings" Target="sharedStrings.xml"/>'
        "</Relationships>"
    ),
    "xl/worksheets/sheet1.xml": (
        f'<worksheet xmlns="{_SML}"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    ),
}


def xlsx_package(shared_strings: bytes) -> bytes:
    """A hand-built workbook: the parts openpyxl opens, with a sharedStrings part of our making."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, xml in _PARTS.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8"?>' + xml)
        archive.writestr("xl/sharedStrings.xml", shared_strings)
    return stream.getvalue()


def test_xlsx_shared_strings_bomb_is_refused_before_openpyxl_inflates_it(monkeypatch):
    from slate.settings import settings

    monkeypatch.setattr(settings, "doc_extract_max_bytes", 8 * 1024 * 1024)
    # 32 MiB of shared strings that deflate to a few tens of KB: a small upload that would
    # cost minutes and gigabytes of memory to index.
    one = b"<si><t>" + b"A" * 1000 + b"</t></si>"
    body = (
        f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{_SML}">'.encode()
        + one * (32 * 1024 * 1024 // len(one))
        + b"</sst>"
    )
    data = xlsx_package(body)
    assert len(data) < 200_000

    def never(*_a, **_k):
        raise AssertionError("openpyxl must not open a workbook over the extraction cap")

    monkeypatch.setattr("openpyxl.load_workbook", never)
    import time

    tracemalloc.start()
    started = time.perf_counter()
    try:
        text, tables = doc_compare.extract(
            "hostile.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data,
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert time.perf_counter() - started < 1.0
    assert (text, tables) == (None, None)
    assert peak < 4_000_000


def test_xlsx_absurd_inflation_ratio_is_refused_under_the_cap(monkeypatch):
    # Well under the byte cap, but a 6 MiB part from a few KB is not a spreadsheet anyone made.
    body = b"<sst>" + b"\x00" * (6 * 1024 * 1024) + b"</sst>"
    data = xlsx_package(body)
    assert len(data) < 20_000
    with pytest.raises(ValueError, match="inflates"):
        doc_compare._check_archive(data)


def test_a_small_xlsx_still_indexes_its_cells_and_table():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Quote"
    ws.append(["Item", "Price"])
    ws.append(["Banner", 12.5])
    buf = io.BytesIO()
    wb.save(buf)
    text, tables = doc_compare.extract("quote.xlsx", "", buf.getvalue())
    assert text == "## Quote\nItem\tPrice\nBanner\t12.5"
    assert tables == [["Quote", [["Item", "Price"], ["Banner", "12.5"]]]]


def test_pdf_extraction_stops_at_the_inflated_byte_budget(monkeypatch):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from slate.settings import settings

    monkeypatch.setattr(settings, "doc_extract_max_bytes", 1024 * 1024)
    w = PdfWriter()
    for _ in range(3):
        page = w.add_blank_page(width=200, height=200)
        stream = DecodedStreamObject()
        stream.set_data(b"BT (hi) Tj ET\n" * 50_000)  # 700 KB inflated per page
        page[NameObject("/Contents")] = w._add_object(stream.flate_encode())
    buf = io.BytesIO()
    w.write(buf)
    data = buf.getvalue()
    assert len(data) < 20_000
    text = doc_compare._pdf_text(data)
    assert "--- page 1 ---" in text
    assert "--- page 2 ---" not in text  # the second page would pass 1 MiB inflated
