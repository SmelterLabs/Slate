"""A page whose body starts with a ```facts block of `key: value` lines carries them as
structured `facts` in its view (REST and MCP), and a value is findable by search."""

from __future__ import annotations

import pytest

from slate.service.docs import parse_facts

API = "/api/v1"

RECORD = """```facts
Policy number: HO-4471-220
Carrier : Example Mutual
renews: 2027-03-01
Deductible:
notes: call the office at 555-0100
Policy number: HO-4471-220-B
```

## Notes

The claim is closed.
"""


def test_parse_facts_reads_the_leading_block_only():
    assert parse_facts(RECORD) == [
        {"key": "Policy number", "value": "HO-4471-220"},
        {"key": "Carrier", "value": "Example Mutual"},
        {"key": "renews", "value": "2027-03-01"},
        {"key": "Deductible", "value": ""},
        {"key": "notes", "value": "call the office at 555-0100"},
        {"key": "Policy number", "value": "HO-4471-220-B"},  # duplicates keep their order
    ]


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Just prose.\n",
        "## Heading\n\n```facts\nkey: value\n```\n",  # not first: ordinary code
        "```\nkey: value\n```\n",  # no tag
        "```python\nkey: value\n```\n",
        "```facts\nkey: value\n",  # never closed
    ],
)
def test_parse_facts_is_empty_without_a_leading_facts_block(body):
    assert parse_facts(body) == []


def test_parse_facts_tolerates_blank_lines_tag_case_and_odd_lines():
    body = "\n\n```FACTS  \n  Make : Honda  \nno colon here\n: no key\nyear:2019\n```\nbody"
    assert parse_facts(body) == [
        {"key": "Make", "value": "Honda"},
        {"key": "year", "value": "2019"},
    ]
    assert parse_facts("```facts\n```\n") == []
    assert parse_facts("```facts\nurl: https://example.com/a:b\n```") == [
        {"key": "url", "value": "https://example.com/a:b"}
    ]


def test_rest_view_carries_facts_and_search_finds_a_value(client, alex, todo):
    page = client.post(
        f"{API}/docs",
        json={"title": "Home insurance", "list_id": todo["id"], "body_md": RECORD},
        headers=alex,
    )
    assert page.status_code == 201, page.text
    doc = page.json()
    assert doc["facts"][0] == {"key": "Policy number", "value": "HO-4471-220"}
    assert len(doc["facts"]) == 6
    got = client.get(f"{API}/docs/{doc['id']}", headers=alex).json()
    assert got["facts"] == doc["facts"]
    assert got["outline"] == [{"level": 2, "text": "Notes", "line": 9}]
    plain = client.post(
        f"{API}/docs",
        json={"title": "Plain", "list_id": todo["id"], "body_md": "Hi"},
        headers=alex,
    ).json()
    assert plain["facts"] == []
    hits = client.get(f"{API}/docs", params={"q": "ho-4471-220"}, headers=alex).json()
    assert [d["id"] for d in hits] == [doc["id"]]
    assert "HO-4471-220" in hits[0]["snippet"]
    # Edited the way agents do it: the block is part of the body.
    edited = client.patch(
        f"{API}/docs/{doc['id']}",
        json={"body_md": "```facts\nCarrier: Sample Insurance\n```\n"},
        headers=alex,
    ).json()
    assert edited["facts"] == [{"key": "Carrier", "value": "Sample Insurance"}]


async def test_mcp_view_carries_facts(client, alex, todo):
    from test_mcp import call, http_mcp, ok

    async with http_mcp(alex) as mcp:
        made = ok(
            await call(mcp, "slate_doc", "create", list_id=todo["id"], title="Car", body_md="plain")
        )
        assert made["facts"] == []
        updated = ok(
            await call(
                mcp,
                "slate_doc",
                "update",
                id=made["id"],
                body_md="```facts\nVIN: 1HGCM82633A004352\nplate: ABC 123\n```\n\nNotes\n",
            )
        )
        assert updated["facts"] == [
            {"key": "VIN", "value": "1HGCM82633A004352"},
            {"key": "plate", "value": "ABC 123"},
        ]
        got = ok(await call(mcp, "slate_doc", "get", id=made["id"]))
        assert got["facts"] == updated["facts"]
