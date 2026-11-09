"""A 10,000-item seed answers a list+tag query in under 50 ms.

Skip with `-m "not perf"` if a slow CI runner makes it flaky.
"""

from __future__ import annotations

import random
import statistics
import time

import pytest
from sqlalchemy import insert

from conftest import API
from slate.ids import new_id
from slate.models import Item, ItemAssignee, ItemTag, Source, utcnow
from slate.service import accounts, lists, sections, tags
from slate.service.core import Actor, apply_change

ITEMS = 10_000
LISTS = 5
TAGS = 15


@pytest.mark.perf
def test_10k_items_list_and_tag_query_under_50ms(client, db):
    account, _, token = accounts.bootstrap(db, handle="alex", display_name="Alex")
    actor = Actor(account_id=account.id, source=Source.system)
    section = sections.create_section(db, actor, name="Personal", audience="personal")
    all_lists = [
        lists.create_list(db, actor, section_id=section.id, name=f"List {n}") for n in range(LISTS)
    ]
    all_tags = tags.ensure_tags(db, actor, [f"tag{n}" for n in range(TAGS)])

    rng = random.Random(3)
    now = utcnow()
    item_rows, tag_rows, assignee_rows = [], [], []
    for n in range(ITEMS):
        lst = all_lists[n % LISTS]
        item_id = new_id()
        item_rows.append(
            {
                "id": item_id,
                "list_id": lst.id,
                "kind": "task",
                "title": f"Item {n}",
                "body_md": "",
                "status_id": lst.default_status_id,
                "position": n,
                "created_by": account.id,
                "created_at": now,
                "updated_at": now,
            }
        )
        for tag in rng.sample(all_tags, rng.randint(1, 2)):
            tag_rows.append({"item_id": item_id, "tag_id": tag.id})
        if n % 3 == 0:
            assignee_rows.append({"item_id": item_id, "account_id": account.id})
    with apply_change(db, actor) as cx:
        db.execute(insert(Item), item_rows)
        db.execute(insert(ItemTag), tag_rows)
        db.execute(insert(ItemAssignee), assignee_rows)
        cx.record("test.seeded", after={"items": ITEMS})

    headers = {"Authorization": f"Bearer {token}"}
    params = {"list_id": all_lists[2].id, "tag": "tag7"}
    first = client.get(f"{API}/items", params=params, headers=headers)  # warm caches
    assert first.status_code == 200
    expected = len(first.json())
    assert 50 < expected < ITEMS // LISTS  # the filter really narrows

    timings = []
    for _ in range(7):
        start = time.perf_counter()
        r = client.get(f"{API}/items", params=params, headers=headers)
        timings.append((time.perf_counter() - start) * 1000)
        assert len(r.json()) == expected
    median = statistics.median(timings)
    print(
        f"\nGET /items?list_id=&tag= over {ITEMS} items -> {expected} rows: "
        f"median {median:.1f} ms, best {min(timings):.1f} ms"
    )
    assert median < 50, f"median {median:.1f} ms"
