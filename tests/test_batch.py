"""POST /batch: MCP-shaped ops in one round trip, atomic by default."""

from __future__ import annotations

from conftest import API


def _batch(client, headers, ops, **kw):
    return client.post(f"{API}/batch", json={"ops": ops, **kw}, headers=headers)


def test_ops_chain_through_refs_and_commit_together(client, alex, todo):
    r = _batch(
        client,
        alex,
        [
            {
                "ref": "t",
                "tool": "item",
                "action": "create",
                "params": {"list_id": todo["id"], "title": "Renew passport", "tags": ["errand"]},
            },
            {
                "tool": "slate_comment",
                "action": "add",
                "params": {"id": "$t.id", "body_md": "Filed by batch"},
            },
            {"tool": "item", "action": "set_status", "params": {"id": "$t.id", "status": "Done"}},
            {"tool": "item", "action": "get", "params": {"id": "$t.id"}},
        ],
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert all(x["ok"] for x in results)
    detail = results[3]["result"]
    assert detail["status"]["name"] == "Done"
    assert detail["comments"][0]["body_md"] == "Filed by batch"
    # Every op is a normal event with the caller as actor.
    events = client.get(f"{API}/items/{detail['id']}/events", headers=alex).json()
    assert {"item.created", "item.commented", "item.status_changed"} <= {e["type"] for e in events}


def test_atomic_failure_writes_nothing_and_names_the_op(client, alex, todo):
    before = client.get(f"{API}/events", headers=alex).json()["next_since"]
    r = _batch(
        client,
        alex,
        [
            {"tool": "item", "action": "create", "params": {"list_id": todo["id"], "title": "a"}},
            {"tool": "item", "action": "update", "params": {"id": "nosuchitem12", "title": "b"}},
        ],
    )
    assert r.status_code == 404
    assert r.json()["failed_op"] == 1
    assert "Nothing was written" in r.json()["detail"]
    assert client.get(f"{API}/items", headers=alex).json() == []
    after = client.get(f"{API}/events?since={before}", headers=alex).json()
    assert after["events"] == []


def test_non_atomic_reports_each_slot(client, alex, todo):
    r = _batch(
        client,
        alex,
        [
            {"tool": "item", "action": "create", "params": {"list_id": todo["id"], "title": "a"}},
            {"tool": "item", "action": "update", "params": {"id": "nosuchitem12", "title": "b"}},
            {"tool": "item", "action": "create", "params": {"list_id": todo["id"], "title": "c"}},
        ],
        atomic=False,
    )
    assert r.status_code == 200
    res = r.json()["results"]
    assert [x["ok"] for x in res] == [True, False, True]
    assert res[1]["status"] == 404
    assert {i["title"] for i in client.get(f"{API}/items", headers=alex).json()} == {"a", "c"}


def test_each_op_needs_its_own_scope(client, alex, todo, make_account):
    _, reader = make_account("reader", ("read",))
    ok = _batch(client, reader, [{"tool": "item", "action": "list", "params": {}}])
    assert ok.status_code == 200
    r = _batch(
        client,
        reader,
        [{"tool": "item", "action": "create", "params": {"list_id": todo["id"], "title": "x"}}],
    )
    assert r.status_code == 403 and r.json()["failed_op"] == 0


def test_single_action_tools_need_no_action(client, alex):
    r = _batch(client, alex, [{"tool": "today"}])
    assert r.status_code == 200 and "overdue" in r.json()["results"][0]["result"]


def test_bad_refs_tools_and_params_are_plain_errors(client, alex, todo):
    cases = [
        ({"tool": "item", "action": "get", "params": {"id": "$nope.id"}}, "names no earlier op"),
        ({"tool": "nosuch", "action": "x"}, "unknown tool"),
        ({"tool": "item", "action": "fly"}, "unknown action"),
        ({"tool": "item"}, "needs an action"),
        ({"tool": "webhook", "action": "test", "params": {"id": "x"}}, "cannot run inside"),
        ({"tool": "item", "action": "create", "params": {"title": "no list"}}, "list_id"),
    ]
    for op, text in cases:
        r = _batch(client, alex, [op])
        assert r.status_code == 422, (op, r.text)
        assert text in r.json()["detail"], (op, r.json())


def test_dollar_text_that_is_not_a_ref_shape_stays_literal(client, alex, todo):
    r = _batch(
        client,
        alex,
        [
            {
                "tool": "item",
                "action": "create",
                "params": {"list_id": todo["id"], "title": "$5 lunch"},
            }
        ],
    )
    assert r.json()["results"][0]["result"]["title"] == "$5 lunch"


def test_batch_size_is_capped(client, alex):
    ops = [{"tool": "today"}] * 101
    assert _batch(client, alex, ops).status_code == 422


def _two_ops_on_one_task(client, alex, todo, **kw):
    made = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Book flights"}, headers=alex
    ).json()
    ops = [
        {"tool": "item", "action": "update", "params": {"id": made["id"], "priority": "high"}},
        {"tool": "item", "action": "update", "params": {"id": made["id"], "title": "Book trains"}},
    ]
    r = _batch(client, alex, ops, **kw)
    assert r.status_code == 200, r.text
    return [x["result"] for x in r.json()["results"]]


def test_write_results_show_the_task_as_the_whole_batch_left_it(client, alex, todo):
    first, second = _two_ops_on_one_task(client, alex, todo)
    assert first == second
    assert (first["title"], first["priority"]) == ("Book trains", "high")


def test_views_each_shows_the_task_as_each_op_left_it(client, alex, todo):
    first, second = _two_ops_on_one_task(client, alex, todo, views="each")
    assert (first["title"], first["priority"]) == ("Book flights", "high")
    assert (second["title"], second["priority"]) == ("Book trains", "high")


def test_non_atomic_batches_build_final_views_too(client, alex, todo):
    first, second = _two_ops_on_one_task(client, alex, todo, atomic=False)
    assert first["title"] == second["title"] == "Book trains"


def test_refs_and_reads_see_the_state_at_their_point(client, alex, todo):
    r = _batch(
        client,
        alex,
        [
            {"ref": "t", "tool": "item", "action": "create",
             "params": {"list_id": todo["id"], "title": "Pay rent"}},
            {"tool": "comment", "action": "add",
             "params": {"id": "$t.id", "body_md": "Was $t.status.name"}},
            {"tool": "item", "action": "update", "params": {"id": "$t.id", "title": "$t.title"}},
            {"tool": "item", "action": "get", "params": {"id": "$t.id"}},
            {"tool": "item", "action": "set_status", "params": {"id": "$t.id", "status": "Done"}},
        ],
    )  # fmt: skip
    assert r.status_code == 200, r.text
    res = [x["result"] for x in r.json()["results"]]
    status = res[0]["status"]["name"]
    assert status == "Done"  # the create answers with the end state
    # `$t.title` was resolved inside the batch; the read saw the task before set_status.
    assert res[2]["title"] == "Pay rent"
    assert res[3]["status"]["name"] != "Done"


def test_a_task_purged_later_in_the_batch_answers_null(client, alex, todo):
    r = _batch(
        client,
        alex,
        [
            {"ref": "t", "tool": "item", "action": "create",
             "params": {"list_id": todo["id"], "title": "Oops"}},
            {"tool": "item", "action": "trash", "params": {"id": "$t.id"}},
            {"tool": "item", "action": "purge", "params": {"id": "$t.id"}},
        ],
    )  # fmt: skip
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert all(x["ok"] for x in res)
    assert res[0].get("result") is None and res[1].get("result") is None


def test_bulk_results_are_lists_of_final_views(client, alex, todo):
    a, b = (
        client.post(f"{API}/items", json={"list_id": todo["id"], "title": t}, headers=alex).json()
        for t in ("a", "b")
    )
    r = _batch(
        client,
        alex,
        [
            {"tool": "item", "action": "bulk_update",
             "params": {"ids": [a["id"], b["id"]], "changes": {"priority": "low"}}},
            {"tool": "item", "action": "update", "params": {"id": b["id"], "priority": "urgent"}},
        ],
    )  # fmt: skip
    assert r.status_code == 200, r.text
    bulk = r.json()["results"][0]["result"]
    assert [(x["id"], x["priority"]) for x in bulk] == [(a["id"], "low"), (b["id"], "urgent")]
