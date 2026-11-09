"""The MCP server: tools over streamable HTTP at /mcp (in-process) and the stdio REST client."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from slate import mcp_server, mcp_tools, notify
from slate.app import create_app
from slate.db import SessionLocal
from slate.models import Event, Source

TOOLS = {
    "slate_item",
    "slate_comment",
    "slate_chat",
    "slate_checklist",
    "slate_view",
    "slate_favorite",
    "slate_automation",
    "slate_doc",
    "slate_workload",
    "slate_reminder",
    "slate_inbox",
    "slate_list",
    "slate_section",
    "slate_tag",
    "slate_recurrence",
    "slate_webhook",
    "slate_account",
    "slate_today",
    "slate_events",
    "slate_batch",
    "slate_export",
    "slate_agents",
    "slate_health",
    "slate_checkin",
    "slate_work",
    "slate_share",
    "slate_audit",
    "slate_search",
}


@asynccontextmanager
async def http_mcp(headers: dict[str, str]):
    """An MCP client talking streamable HTTP to a fresh app, in-process (no socket)."""
    app = create_app()
    async with app.state.mcp_manager.run():
        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(transport=transport, base_url="http://slate", headers=headers) as hc,
            Client(streamable_http_client("http://slate/mcp", http_client=hc)) as client,
        ):
            yield client


async def call(client: Client, tool: str, action: str | None = None, **params):
    args: dict = {"params": params} if params else {}
    if action is not None:
        args["action"] = action
    return await client.call_tool(tool, args)


def ok(result) -> object:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def err(result) -> str:
    assert result.is_error, result.content[0].text
    return result.content[0].text


async def make_list(client: Client) -> dict:
    section = ok(
        await call(client, "slate_section", "create", name="Personal", audience="personal")
    )
    return ok(await call(client, "slate_list", "create", section_id=section["id"], name="Todo"))


# --- tool list -------------------------------------------------------------------------------


async def test_lists_one_tool_per_resource_with_hints_and_the_one_item_rule(alex):
    async with http_mcp(alex) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == TOOLS
    for tool in tools.values():
        assert "exactly one item" in tool.description and "split" in tool.description
        assert "Example:" in tool.description
        assert tool.input_schema["type"] == "object"
    assert tools["slate_today"].annotations.read_only_hint is True
    assert tools["slate_events"].annotations.read_only_hint is True
    assert tools["slate_item"].annotations.read_only_hint is False
    assert tools["slate_item"].annotations.destructive_hint is True
    assert tools["slate_tag"].annotations.destructive_hint is True
    assert tools["slate_recurrence"].annotations.destructive_hint is False
    item_schema = tools["slate_item"].input_schema
    assert "split" in item_schema["properties"]["action"]["enum"]
    assert {"title", "list_id", "children", "id"} <= set(
        item_schema["properties"]["params"]["properties"]
    )


def test_a_param_shared_by_actions_with_different_values_accepts_all_of_them():
    """`kind` is the item kind for `list` and the link kind for `link`; a client that validates
    against the merged schema must be able to send either."""
    schema = mcp_tools.input_schema(mcp_tools.TOOLS["slate_item"])
    kind = schema["properties"]["params"]["properties"]["kind"]
    assert {"task", "blocks", "blocked_by", "relates"} <= set(kind["enum"])
    assert "For:" in kind["description"]


@pytest.mark.parametrize("tool_name", ["slate_item", "slate_list"])
async def test_list_with_client_applied_schema_defaults(alex, tool_name):
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        item = ok(await call(client, "slate_item", "create", list_id=todo["id"], title="Keep me"))
        tools = {t.name: t for t in (await client.list_tools()).tools}
        properties = tools[tool_name].input_schema["properties"]["params"]["properties"]
        # Some clients fill omitted fields from tools/list before sending tools/call.
        params = {key: prop["default"] for key, prop in properties.items() if "default" in prop}
        if tool_name == "slate_item":
            params["list_id"] = todo["id"]
        listed = ok(await call(client, tool_name, "list", **params))
        expected = item if tool_name == "slate_item" else todo
        assert [row["id"] for row in listed] == [expected["id"]]


@pytest.mark.parametrize("spec", mcp_tools.TOOLS.values(), ids=lambda spec: spec.name)
def test_schema_defaults_are_only_removed_from_merged_fields(spec):
    before = {name: action.params.model_json_schema() for name, action in spec.actions.items()}
    schema = mcp_tools.input_schema(spec)
    properties = schema["properties"].get("params", {}).get("properties", {})
    expected = {}
    definitions = {}
    shapes: dict[str, list[dict]] = {}
    for action_schema in before.values():
        stripped = mcp_tools._strip_titles(action_schema)
        definitions.update(stripped.get("$defs", {}))
        for key, prop in stripped.get("properties", {}).items():
            expected.setdefault(key, prop)
            if mcp_tools._shape(prop) not in [
                mcp_tools._shape(v) for v in shapes.setdefault(key, [])
            ]:
                shapes[key].append(prop)
    assert schema.get("$defs", {}) == definitions  # Nested defaults still describe real objects.
    assert properties.keys() == expected.keys()
    for key, prop in properties.items():
        if mcp_tools.single_action(spec):
            assert prop == expected[key]  # Single-action defaults remain meaningful.
        else:
            assert "default" not in prop
            # One name, several shapes (`kind`): the merged field accepts every action's values.
            want = (
                mcp_tools._merged_prop(shapes[key], definitions)
                if len(shapes[key]) > 1
                else {k: v for k, v in expected[key].items() if k not in {"default", "description"}}
            )
            assert {k: v for k, v in prop.items() if k != "description"} == want
            assert "For:" in prop["description"]
    assert before == {
        name: action.params.model_json_schema() for name, action in spec.actions.items()
    }


async def test_schema_defaults_do_not_turn_a_partial_update_into_a_reset(alex):
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        item = ok(
            await call(
                client,
                "slate_item",
                "create",
                list_id=todo["id"],
                title="Before",
                body_md="Keep this body",
                due_at="2030-01-01",
                assignees=["alex"],
            )
        )
        tools = {t.name: t for t in (await client.list_tools()).tools}
        properties = tools["slate_item"].input_schema["properties"]["params"]["properties"]
        params = {key: prop["default"] for key, prop in properties.items() if "default" in prop}
        params.update(id=item["id"], title="After")
        updated = ok(await call(client, "slate_item", "update", **params))
        assert updated["title"] == "After"
        for key in ("body_md", "due_at", "assignees"):
            assert updated[key] == item[key]
        cleared = ok(await call(client, "slate_item", "update", id=item["id"], due_at=None))
        assert cleared.get("due_at") is None  # Explicit null still clears.
        assert "Extra inputs are not permitted" in err(
            await call(client, "slate_item", "list", body_md="")
        )  # Do not hide invalid explicit input by relaxing per-action validation.


# --- the item lifecycle ----------------------------------------------------------------------


async def test_create_update_split_today_and_events_through_mcp(alex):
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        item = ok(
            await call(
                client,
                "slate_item",
                "create",
                list_id=todo["id"],
                title="Pay the water bill",
                body_md="- call them\n- pay",
                due_at="2000-01-01",
                tags=["home"],
            )
        )
        assert item["title"] == "Pay the water bill"
        assert item.get("parent_id") is None and item["subtask_count"] == 0  # one item, no children

        listed = ok(await call(client, "slate_item", "list", list_id=todo["id"]))
        assert [i["id"] for i in listed] == [item["id"]]
        slim = ok(await call(client, "slate_item", "list", fields=["title"], limit=5))
        assert slim == [{"id": item["id"], "title": "Pay the water bill"}]
        assert "unknown field" in err(await call(client, "slate_item", "list", fields=["nope"]))

        updated = ok(await call(client, "slate_item", "update", id=item["id"], priority="high"))
        assert updated["priority"] == "high"
        moved = ok(
            await call(client, "slate_item", "set_status", id=item["id"], status="In progress")
        )
        assert moved["status"]["name"] == "In progress"
        assigned = ok(await call(client, "slate_item", "assign", id=item["id"], assignees=["alex"]))
        assert [a["handle"] for a in assigned["assignees"]] == ["alex"]
        tagged = ok(await call(client, "slate_item", "tag", id=item["id"], tags=["home", "money"]))
        assert tagged["tags"] == ["home", "money"]
        ok(await call(client, "slate_item", "comment", id=item["id"], body_md="called, on hold"))

        children = ok(
            await call(
                client,
                "slate_item",
                "split",
                id=item["id"],
                children=[{"title": "Call"}, {"title": "Pay"}],
            )
        )
        assert [c["title"] for c in children] == ["Call", "Pay"]
        assert all(c["parent_id"] == item["id"] for c in children)

        detail = ok(await call(client, "slate_item", "get", id=item["id"]))
        assert [s["title"] for s in detail["subtasks"]] == ["Call", "Pay"]
        assert detail["comments"][0]["body_md"] == "called, on hold"
        history = ok(await call(client, "slate_item", "history", id=item["id"]))
        assert "item.split" in [e["type"] for e in history]
        versions = ok(await call(client, "slate_item", "versions", id=item["id"]))
        assert [v["version_no"] for v in versions] == [1]

        today = ok(await call(client, "slate_today"))
        assert [i["id"] for i in today["overdue"]] == [item["id"]]

        page = ok(await call(client, "slate_events", since="0", item_id=item["id"]))
        assert page["events"][0]["type"] == "item.created"
        assert all(e["source"] == "mcp" for e in page["events"])

        archived = ok(await call(client, "slate_item", "archive", id=item["id"]))
        assert archived["archived_at"]
        ok(await call(client, "slate_item", "unarchive", id=item["id"]))
        gone = ok(await call(client, "slate_item", "delete", id=children[0]["id"]))
        assert gone == {"deleted": children[0]["id"]}

    with SessionLocal() as s:
        events = list(s.scalars(select(Event).where(Event.item_id == item["id"])))
    assert events and {e.source for e in events} == {Source.mcp}


async def test_session_ref_lands_on_the_events(alex):
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        item = ok(
            await client.call_tool(
                "slate_item",
                {
                    "action": "create",
                    "params": {"list_id": todo["id"], "title": "x"},
                    "session_ref": "run-42",
                },
            )
        )
    with SessionLocal() as s:
        ev = s.scalar(select(Event).where(Event.item_id == item["id"]))
    assert ev.session_ref == "run-42"


async def test_lists_sections_and_tags_round_trip(alex):
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        assert [x["name"] for x in ok(await call(client, "slate_section", "list"))] == ["Personal"]
        renamed = ok(await call(client, "slate_list", "update", id=todo["id"], name="Daily"))
        assert renamed["name"] == "Daily"
        statuses = ok(
            await call(
                client,
                "slate_list",
                "set_statuses",
                id=todo["id"],
                statuses=[{"name": "todo"}, {"name": "done", "kind": "done"}],
            )
        )
        assert [s["name"] for s in statuses["statuses"]] == ["todo", "done"]
        assert len(ok(await call(client, "slate_list", "list"))) == 1
        sec = ok(await call(client, "slate_section", "list"))[0]
        ok(await call(client, "slate_section", "update", id=sec["id"], name="Home"))
        ok(await call(client, "slate_tag", "create", name="H3"))
        ok(await call(client, "slate_tag", "rename", tag="h3", name="h4"))
        assert [t["name"] for t in ok(await call(client, "slate_tag", "list"))] == ["h4"]


async def exercise_clickup_parity(client: Client, todo: dict) -> None:
    """Dependencies, estimates, tag merge/delete and status templates."""
    a = ok(
        await call(
            client,
            "slate_item",
            "create",
            list_id=todo["id"],
            title="A",
            estimate_minutes=30,
            tags=["p1", "p2"],
        )
    )
    assert a["estimate_minutes"] == 30
    b = ok(await call(client, "slate_item", "create", list_id=todo["id"], title="B"))
    linked = ok(
        await call(client, "slate_item", "link", id=b["id"], kind="blocked_by", item_id=a["id"])
    )
    assert linked["blocked"] is True
    assert [i["id"] for i in ok(await call(client, "slate_item", "list", blocked=True))] == [
        b["id"]
    ]
    assert (
        ok(
            await call(
                client, "slate_item", "unlink", id=b["id"], kind="blocked_by", item_id=a["id"]
            )
        )["blocked_by"]
        == []
    )
    ok(await call(client, "slate_tag", "merge", tag="p1", into="p2"))
    assert ok(await call(client, "slate_tag", "delete", tag="p2")) == {"deleted": "p2"}


async def test_clickup_parity_actions_in_process(alex):
    async with http_mcp(alex) as client:
        await exercise_clickup_parity(client, await make_list(client))


async def test_accounts_me_and_admin_actions(alex):
    async with http_mcp(alex) as client:
        me = ok(await call(client, "slate_account", "me"))
        assert me["account"]["handle"] == "alex" and me["scopes"] == ["admin"]
        bot = ok(
            await call(
                client,
                "slate_account",
                "create",
                handle="agent2",
                display_name="Agent 2",
                kind="agent",
            )
        )
        tok = ok(
            await call(
                client, "slate_account", "create_token", account_id=bot["id"], scopes=["write"]
            )
        )
        assert tok["token"].startswith("slate_")
        assert {a["handle"] for a in ok(await call(client, "slate_account", "list"))} == {
            "alex",
            "agent2",
        }

        [listed] = ok(await call(client, "slate_account", "tokens", account_id=bot["id"]))
        assert listed["id"] == tok["id"] and "token" not in listed
        usage = ok(await call(client, "slate_account", "usage", account_id=bot["id"], days=3))
        assert len(usage["days"]) == 3 and usage["tokens"][0]["id"] == tok["id"]
        renamed = ok(
            await call(
                client, "slate_account", "update", account_id=bot["id"], display_name="Renamed"
            )
        )
        assert renamed["display_name"] == "Renamed" and "disabled_at" not in renamed

    async with http_mcp({"Authorization": f"Bearer {tok['token']}"}) as client:
        assert "admin" in err(await call(client, "slate_account", "list"))
        assert ok(await call(client, "slate_account", "me"))["account"]["handle"] == "agent2"
        assert "admin" in err(
            await call(client, "slate_account", "update", account_id=bot["id"], disabled=True)
        )
        assert ok(await call(client, "slate_account", "revoke_token", token_id=tok["id"]))
    assert client_401(tok["token"])


def client_401(token: str) -> bool:
    from fastapi.testclient import TestClient

    r = TestClient(create_app()).get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    return r.status_code == 401


# --- errors ----------------------------------------------------------------------------------


async def test_errors_are_tool_results_with_plain_english_detail(alex, make_account):
    async with http_mcp(alex) as client:
        assert "list nope not found" in err(
            await call(client, "slate_item", "create", list_id="nope", title="x")
        )
        assert "title" in err(await call(client, "slate_item", "create", list_id="x"))
        assert "unknown action" in err(await call(client, "slate_item", "explode", id="x"))
        assert "action" in err(await client.call_tool("slate_item", {"params": {}}))
        assert "bogus" in err(await call(client, "slate_item", "get", id="x", bogus=1))
        assert "recurrence nope not found" in err(
            await call(client, "slate_recurrence", "get", id="nope")
        )

    _, reader = make_account("reader", scopes=("read",))
    async with http_mcp(reader) as client:
        assert "write" in err(
            await call(client, "slate_section", "create", name="X", audience="ai")
        )
        assert ok(await call(client, "slate_section", "list")) == []
        assert "write" in err(await call(client, "slate_webhook", "list"))  # as in REST


# --- recurrences and webhooks ----------------------------------------------------------------


async def exercise_automations(mcp, todo: dict) -> None:
    """Every slate_automation action; shared by the HTTP and stdio-bridge tests."""
    cat = ok(await call(mcp, "slate_automation", "catalog"))
    assert "status_changed" in {t["type"] for t in cat["triggers"]}
    rule = ok(
        await call(
            mcp,
            "slate_automation",
            "create",
            name="Tag done",
            list_id=todo["id"],
            trigger={"type": "status_changed", "to_kind": "done"},
            actions=[{"type": "add_tag", "tag": "shipped"}],
        )
    )
    assert rule["description"].startswith("When status changes")
    assert ok(await call(mcp, "slate_automation", "get", id=rule["id"]))["name"] == "Tag done"
    listed = ok(await call(mcp, "slate_automation", "list", list_id=todo["id"]))
    assert [r["id"] for r in listed] == [rule["id"]]
    paused = ok(await call(mcp, "slate_automation", "update", id=rule["id"], enabled=False))
    assert paused["enabled"] is False
    item = ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="Auto"))
    run = ok(await call(mcp, "slate_automation", "run", id=rule["id"], item_id=item["id"]))
    assert run["status"] == "ok"
    assert len(ok(await call(mcp, "slate_automation", "runs", id=rule["id"]))) == 1
    secret = ok(await call(mcp, "slate_automation", "rotate_secret", id=rule["id"]))
    assert secret["secret"].startswith("whsec_")
    assert len(ok(await call(mcp, "slate_automation", "runs"))) >= 1
    assert ok(await call(mcp, "slate_automation", "delete", id=rule["id"])) == {
        "deleted": rule["id"]
    }


async def exercise_reminders(mcp, todo: dict) -> None:
    """Every slate_reminder action; shared by the HTTP and stdio-bridge tests."""
    item = ok(
        await call(
            mcp, "slate_item", "create", list_id=todo["id"], title="Rent", due_at="2030-01-09"
        )
    )
    r = ok(await call(mcp, "slate_reminder", "add", item_id=item["id"], before_minutes=60))
    assert r["before_minutes"] == 60 and r["fired"] is False
    assert [x["id"] for x in ok(await call(mcp, "slate_reminder", "list", item_id=item["id"]))] == [
        r["id"]
    ]
    assert r["id"] in {x["id"] for x in ok(await call(mcp, "slate_reminder", "upcoming"))}
    digest = ok(await call(mcp, "slate_reminder", "digest"))
    assert set(digest["counts"]) == {"overdue", "today", "waiting"}
    assert ok(await call(mcp, "slate_reminder", "remove", id=r["id"])) == {"deleted": r["id"]}
    assert "at" in err(await call(mcp, "slate_reminder", "add", item_id=item["id"]))


async def exercise_docs(mcp, todo: dict) -> None:
    """Every slate_doc action; shared by the HTTP and stdio-bridge tests."""
    assert "plan" in {t["key"] for t in ok(await call(mcp, "slate_doc", "templates"))}
    plan = ok(await call(mcp, "slate_doc", "create", list_id=todo["id"], template="plan"))
    assert plan["title"] == "Project plan" and plan["list_id"] == todo["id"]
    log = ok(await call(mcp, "slate_doc", "create", title="Log", parent_id=plan["id"]))
    log = ok(await call(mcp, "slate_doc", "update", id=log["id"], append_md="- started"))
    log = ok(
        await call(
            mcp,
            "slate_doc",
            "update",
            id=log["id"],
            replace_section={"heading": "Status", "body_md": "green"},
            note="Checkpoint",
        )
    )
    assert log["body_md"] == "- started\n\n## Status\n\ngreen\n"
    assert "revision" in err(
        await call(mcp, "slate_doc", "update", id=log["id"], body_md="x", base_revision=1)
    )
    made = ok(
        await call(mcp, "slate_doc", "create_task", id=log["id"], title="Ship", replace="green")
    )
    assert made["item"]["list_id"] == todo["id"]
    assert f"[[task:{made['item']['id']}]]" in made["doc"]["body_md"]
    got = ok(await call(mcp, "slate_doc", "get", id=log["id"]))
    assert [t["title"] for t in got["tasks"]] == ["Ship"] and got["path"][0]["id"] == plan["id"]
    hits = ok(await call(mcp, "slate_doc", "list", q="started"))
    assert [h["id"] for h in hits] == [log["id"]] and "started" in hits[0]["snippet"]
    versions = ok(await call(mcp, "slate_doc", "versions", id=log["id"]))
    assert versions[-1]["version_no"] == 1 and "body_md" not in {
        k for k, v in versions[0].items() if v is not None
    }
    v1 = ok(await call(mcp, "slate_doc", "version", id=log["id"], version_no=1))
    assert v1["note"] == "Checkpoint" and v1["body_md"].endswith("green\n")
    d = ok(await call(mcp, "slate_doc", "diff", id=log["id"], from_version=1))
    assert d["added"] >= 1
    ok(await call(mcp, "slate_doc", "restore", id=log["id"], version_no=1))
    f = ok(
        await call(
            mcp, "slate_doc", "attach", id=log["id"], filename="a.png", content_base64="iVBORw0="
        )
    )
    assert f["content_type"] == "image/png"
    assert ok(await call(mcp, "slate_doc", "remove_file", file_id=f["id"])) == {"removed": f["id"]}
    spec = ok(await call(mcp, "slate_doc", "create", title="Spec", kind="file", list_id=todo["id"]))
    assert spec["kind"] == "file" and spec.get("file") is None
    assert "note" in err(
        await call(
            mcp,
            "slate_doc",
            "upload",
            id=spec["id"],
            filename="s.pdf",
            content_base64="JVBERg==",
            note=" ",
        )
    )
    r1 = ok(
        await call(
            mcp,
            "slate_doc",
            "upload",
            id=spec["id"],
            filename="s.pdf",
            content_base64="JVBERg==",
            note="First",
        )
    )
    assert r1["revision_no"] == 1 and r1["content_type"] == "application/pdf"
    ok(
        await call(
            mcp,
            "slate_doc",
            "upload",
            id=spec["id"],
            filename="s.pdf",
            content_base64="JVBERjI=",
            note="Second",
        )
    )
    revs = ok(await call(mcp, "slate_doc", "revisions", id=spec["id"]))
    assert [r["revision_no"] for r in revs] == [2, 1] and revs[1]["superseded_at"]
    back = ok(await call(mcp, "slate_doc", "restore_revision", id=spec["id"], revision_no=1))
    assert back["file"]["revision_no"] == 3 and back["file"]["restored_from"] == 1
    talk = ok(await call(mcp, "slate_doc", "comment", id=log["id"], body_md="Looks good"))
    assert talk["surface"] == "discussion" and talk["anchor_revision"] >= 1
    thread = ok(
        await call(
            mcp,
            "slate_doc",
            "comment",
            id=log["id"],
            body_md="Make it 'amber'",
            anchor={"quote": "green"},
            suggestion_md="amber",
        )
    )
    assert thread["surface"] == "review" and thread["suggestion_md"] == "amber"
    reply = ok(
        await call(mcp, "slate_doc", "comment", id=log["id"], body_md="ok", parent_id=thread["id"])
    )
    assert reply["parent_id"] == thread["id"]
    edited = ok(
        await call(mcp, "slate_doc", "edit_comment", comment_id=talk["id"], body_md="Looks great")
    )
    assert edited["edited_at"]
    assert len(ok(await call(mcp, "slate_doc", "comments", id=log["id"]))) == 3
    accepted = ok(await call(mcp, "slate_doc", "accept_suggestion", comment_id=thread["id"]))
    assert "amber" in accepted["doc"]["body_md"] and accepted["comment"]["resolved_at"]
    reopened = ok(await call(mcp, "slate_doc", "reopen_comment", comment_id=thread["id"]))
    assert reopened.get("resolved_at") is None
    ok(await call(mcp, "slate_doc", "resolve_comment", comment_id=thread["id"]))
    asked = ok(
        await call(mcp, "slate_doc", "assign_comment", comment_id=talk["id"], assignee="alex")
    )
    assert asked["assignee_id"]
    act = ok(await call(mcp, "slate_doc", "activity", id=log["id"]))
    assert len(act["comments"]) == 3 and any(
        e["type"] == "doc.suggestion_accepted" for e in act["events"]
    )
    card = ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="Contract"))
    att = ok(
        await call(
            mcp, "slate_item", "attach", id=card["id"], filename="d.pdf", content_base64="JVBERg=="
        )
    )
    promoted = ok(
        await call(
            mcp, "slate_item", "promote_attachment", id=att["id"], section_id=todo["section_id"]
        )
    )
    assert (
        promoted["kind"] == "file"
        and promoted["title"] == "d.pdf"
        and promoted["file"]["revision_no"] == 1
    )
    moved = ok(await call(mcp, "slate_doc", "move", id=log["id"], list_id=todo["id"]))
    assert moved.get("parent_id") is None
    copy = ok(await call(mcp, "slate_doc", "duplicate", id=plan["id"]))
    assert copy["title"] == "Project plan (copy)"
    ok(await call(mcp, "slate_doc", "archive", id=copy["id"]))
    ok(await call(mcp, "slate_doc", "unarchive", id=copy["id"]))
    ok(await call(mcp, "slate_doc", "archive", id=copy["id"]))
    assert ok(await call(mcp, "slate_doc", "delete", id=copy["id"])) == {"deleted": [copy["id"]]}


async def test_docs_through_mcp(alex):
    async with http_mcp(alex) as client:
        await exercise_docs(client, await make_list(client))


async def exercise_work(mcp, todo: dict) -> None:
    """Every slate_work action; shared by the HTTP and stdio-bridge tests."""
    me = ok(await call(mcp, "slate_account", "me"))["account"]["handle"]
    first = ok(
        await call(
            mcp,
            "slate_item",
            "create",
            list_id=todo["id"],
            title="First",
            assignees=[me],
            priority="high",
        )
    )
    second = ok(
        await call(mcp, "slate_item", "create", list_id=todo["id"], title="Second", assignees=[me])
    )
    got = ok(await call(mcp, "slate_work", "next", lease_seconds=600))
    assert got["item"]["id"] == first["id"] and got["remaining"] == 1
    lease = ok(
        await call(
            mcp,
            "slate_work",
            "heartbeat",
            item_id=first["id"],
            progress=50,
            run_id=got["lease"]["run_id"],
        )
    )
    assert lease["item_id"] == first["id"]
    assert [r["item_id"] for r in ok(await call(mcp, "slate_work", "held"))] == [first["id"]]
    out = ok(
        await call(
            mcp,
            "slate_work",
            "finish",
            item_id=first["id"],
            outcome="done",
            note="ok",
            run_id=got["lease"]["run_id"],
        )
    )
    assert out["item"]["status"]["kind"] == "done"
    nxt = ok(await call(mcp, "slate_work", "next"))
    assert nxt["item"]["id"] == second["id"]
    out = ok(
        await call(
            mcp,
            "slate_work",
            "finish",
            item_id=second["id"],
            outcome="release",
            run_id=nxt["lease"]["run_id"],
        )
    )
    assert out.get("next") is None  # nulls are left out of tool results
    empty = ok(await call(mcp, "slate_work", "next", resume=False, list_id=todo["id"], tag="x"))
    assert empty.get("item") is None and empty["remaining"] == 0
    assert "hand_to" in err(
        await call(mcp, "slate_work", "finish", item_id=second["id"], outcome="done", hand_to=me)
    )


async def test_work_through_mcp(alex):
    async with http_mcp(alex) as client:
        await exercise_work(client, await make_list(client))


async def exercise_chat(mcp, todo: dict) -> None:
    """Every slate_chat action; shared by the HTTP and stdio-bridge tests."""
    key = f"list:{todo['id']}"
    chans = ok(await call(mcp, "slate_chat", "channels"))
    assert key in [c["key"] for c in chans]
    root = ok(await call(mcp, "slate_chat", "post", channel=key, body_md="Plan for **Saturday**"))
    reply = ok(
        await call(mcp, "slate_chat", "post", channel=key, body_md="ok", parent_id=root["id"])
    )
    assert reply["parent_id"] == root["id"]
    page = ok(await call(mcp, "slate_chat", "messages", channel=key))
    assert [m["reply_count"] for m in page] == [1]
    assert len(ok(await call(mcp, "slate_chat", "messages", channel=key, after=root["id"]))) == 1
    assert len(ok(await call(mcp, "slate_chat", "thread", id=reply["id"]))) == 2
    ok(await call(mcp, "slate_chat", "edit", id=reply["id"], body_md="sure"))
    reacted = ok(await call(mcp, "slate_chat", "react", id=root["id"], emoji="👍"))
    assert reacted["reactions"][0]["emoji"] == "👍"
    ok(await call(mcp, "slate_chat", "unreact", id=root["id"], emoji="👍"))
    task = ok(await call(mcp, "slate_chat", "task", id=root["id"]))
    assert task["title"] == "Plan for Saturday" and task["list_id"] == todo["id"]
    ok(await call(mcp, "slate_chat", "delete", id=reply["id"]))
    assert ok(await call(mcp, "slate_chat", "read", channel=key))["channel"] == key
    assert "channels" in ok(await call(mcp, "slate_chat", "read_all"))


async def test_chat_through_mcp(alex):
    async with http_mcp(alex) as client:
        await exercise_chat(client, await make_list(client))


async def exercise_trash_export(mcp, todo: dict) -> None:
    """Trash, restore, purge, the archive filters and slate_export; shared by both bindings."""
    item = ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="Binned"))
    trashed = ok(await call(mcp, "slate_item", "trash", id=item["id"]))
    assert trashed["trashed_at"] is not None
    found = ok(await call(mcp, "slate_item", "list", trashed=True, order="trashed"))
    assert [i["id"] for i in found] == [item["id"]]
    ok(await call(mcp, "slate_item", "restore", id=item["id"]))
    ok(await call(mcp, "slate_item", "trash", id=item["id"]))
    assert ok(await call(mcp, "slate_item", "purge", id=item["id"])) == {"purged": item["id"]}
    gone = ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="Gone"))
    ok(await call(mcp, "slate_item", "trash", id=gone["id"]))
    assert ok(await call(mcp, "slate_export", "empty_trash")) == {"purged": 1}
    data = ok(await call(mcp, "slate_export", "json", list_id=todo["id"]))
    assert data["format"] == "slate-export" and data["scope"]["id"] == todo["id"]
    assert item["id"] not in {i["id"] for i in data["items"]}
    keep = ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="Keep"))
    dup = ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="Dup", tags=["x"]))
    marked = ok(
        await call(
            mcp, "slate_item", "link", id=keep["id"], kind="duplicated_by", item_id=dup["id"]
        )
    )
    assert marked["duplicated_by"] == [dup["id"]]
    links = ok(
        await call(mcp, "slate_item", "link", id=keep["id"], kind="relates", item_id=dup["id"])
    )
    assert links["duplicated_by"] == [dup["id"]] and links["related"] == [dup["id"]]


async def test_trash_and_export_through_mcp(alex):
    async with http_mcp(alex) as client:
        await exercise_trash_export(client, await make_list(client))


async def test_reminders_through_mcp(alex):
    async with http_mcp(alex) as client:
        await exercise_reminders(client, await make_list(client))


async def test_automations_through_mcp(alex):
    async with http_mcp(alex) as client:
        await exercise_automations(client, await make_list(client))


async def test_favorites_through_mcp(alex):
    async with http_mcp(alex) as client:
        lst = await make_list(client)
        fav = ok(await call(client, "slate_favorite", "add", kind="list", target_id=lst["id"]))
        assert fav["name"] == "Todo" and fav["scope_id"] == lst["id"]
        view = ok(
            await call(client, "slate_favorite", "add", kind="view", target_id="vwagenda0001")
        )
        listed = ok(await call(client, "slate_favorite", "list"))
        assert [f["id"] for f in listed] == [fav["id"], view["id"]]
        order = ok(await call(client, "slate_favorite", "reorder", ids=[view["id"], fav["id"]]))
        assert [f["kind"] for f in order] == ["view", "list"]
        assert ok(await call(client, "slate_favorite", "remove", id=fav["id"])) == {
            "removed": fav["id"]
        }
        assert len(ok(await call(client, "slate_favorite", "list"))) == 1


async def test_inbox_through_mcp(client, alex, make_account):
    _, agent = make_account("agent")
    async with http_mcp(alex) as c:
        lst = await make_list(c)
        item = ok(await call(c, "slate_item", "create", list_id=lst["id"], title="Fix gutter"))
    async with http_mcp(agent) as c:
        ok(await call(c, "slate_comment", "add", id=item["id"], body_md="Done @alex"))
    async with http_mcp(alex) as c:
        assert ok(await call(c, "slate_inbox", "count")) == {"unread": 1}
        [entry] = ok(await call(c, "slate_inbox", "list", unread=True))
        assert entry["reason"] == "mention" and entry["item_title"] == "Fix gutter"
        marked = ok(await call(c, "slate_inbox", "read", item_id=item["id"]))
        assert marked["event_ids"] == [entry["event"]["id"]]
        assert ok(await call(c, "slate_inbox", "count")) == {"unread": 0}
        ok(await call(c, "slate_inbox", "clear", all=True))
        assert ok(await call(c, "slate_inbox", "list")) == []


async def test_recurrence_rules_through_mcp(alex):
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        rule = ok(
            await call(
                client,
                "slate_recurrence",
                "create",
                list_id=todo["id"],
                title_template="Bulk trash {date}",
                rrule="FREQ=WEEKLY;INTERVAL=2;BYDAY=WE",
                dtstart="2030-01-02",
            )
        )
        assert rule["next_run_at"] == "2030-01-02T06:00:00Z" and len(rule["next_occurrences"]) == 5
        assert ok(await call(client, "slate_recurrence", "get", id=rule["id"])) == rule
        assert [r["id"] for r in ok(await call(client, "slate_recurrence", "list"))] == [rule["id"]]
        changed = ok(await call(client, "slate_recurrence", "update", id=rule["id"], lead_days=3))
        assert changed["lead_days"] == 3
        paused = ok(await call(client, "slate_recurrence", "pause", id=rule["id"]))
        assert paused["paused_at"] is not None
        resumed = ok(await call(client, "slate_recurrence", "resume", id=rule["id"]))
        assert resumed.get("paused_at") is None

        daily = ok(
            await call(
                client,
                "slate_recurrence",
                "create",
                list_id=todo["id"],
                title_template="Water plants",
                rrule="FREQ=DAILY",
                lead_days=0,
            )
        )
        ran = ok(await call(client, "slate_recurrence", "run_now", id=daily["id"]))
        assert ran["recurrence"]["id"] == daily["id"]
        assert [i["title"] for i in ran["spawned"]] == ["Water plants"]

        assert "stays ended" in err(
            await call(client, "slate_recurrence", "update", id=rule["id"], ended=False)
        )
        ended = ok(await call(client, "slate_recurrence", "update", id=rule["id"], ended=True))
        assert ended["ended_at"] is not None
        assert "rrule" in err(await call(client, "slate_recurrence", "create", list_id="x"))

        seen = ok(
            await call(
                client,
                "slate_recurrence",
                "preview",
                rrule="FREQ=MONTHLY;BYDAY=2WE,4WE",
                dtstart="2030-01-01",
                count=2,
            )
        )
        assert seen["description"] == "every month on the 2nd and 4th Wednesday"
        assert [o["local"][:10] for o in seen["occurrences"]] == ["2030-01-09", "2030-01-23"]
        assert "FREQ" in err(await call(client, "slate_recurrence", "preview", rrule="nope"))
        item = ok(
            await call(
                client, "slate_item", "create", list_id=todo["id"], title="Mow", due_at="2030-01-09"
            )
        )
        adopted = ok(
            await call(client, "slate_recurrence", "adopt", id=daily["id"], item_id=item["id"])
        )
        assert adopted["recurrence_id"] == daily["id"]

    with SessionLocal() as session:
        created = session.scalars(select(Event).where(Event.type == "recurrence.created")).all()
    assert {e.source for e in created} == {Source.mcp}


async def test_webhooks_through_mcp(alex, monkeypatch):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    monkeypatch.setattr(notify, "transport", httpx.MockTransport(handle))
    async with http_mcp(alex) as client:
        made = ok(
            await call(
                client,
                "slate_webhook",
                "create",
                url="https://agent.example.com/slate",
                events=["item.due"],
                filter={"assignee_me": True},
            )
        )
        secret = made["secret"]
        assert secret.startswith("whsec_")
        assert made["filter"] == {"assignee_me": True, "due_lead_hours": 24}

        listed = await call(client, "slate_webhook", "list")
        assert [h["id"] for h in ok(listed)] == [made["id"]]
        assert secret not in listed.content[0].text

        updated = ok(
            await call(client, "slate_webhook", "update", id=made["id"], events=["item.assigned"])
        )
        assert updated["events"] == ["item.assigned"] and "secret" not in updated

        sent = ok(await call(client, "slate_webhook", "test", id=made["id"]))
        assert (sent["state"], sent["status_code"], sent["attempt"]) == ("ok", 200, 1)
        [req] = seen
        want = hmac.new(secret.encode(), req.content, hashlib.sha256).hexdigest()
        assert req.headers["X-Slate-Signature"] == f"sha256={want}"

        ok(await call(client, "slate_webhook", "test", id=made["id"]))
        page = ok(await call(client, "slate_webhook", "deliveries", id=made["id"], limit=1))
        assert len(page["deliveries"]) == 1 and page["next_offset"] == 1

        off = ok(await call(client, "slate_webhook", "update", id=made["id"], disabled=True))
        assert off["disabled_at"] is not None
        assert "bogus" in err(
            await call(client, "slate_webhook", "create", url="https://a.example.com", bogus=1)
        )


def test_bad_or_missing_token_is_401(alex):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(create_app()) as client:
        r = client.post("/mcp", json=body, headers=headers)
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
        r = client.post("/mcp", json=body, headers={**headers, "Authorization": "Bearer slate_x_y"})
        assert r.status_code == 401
        # The lifespan wiring works with a good token over plain HTTP too.
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        }
        r = client.post("/mcp", json=init, headers={**headers, **alex})
        assert r.status_code == 200, r.text
        assert r.json()["result"]["serverInfo"]["name"] == "slate"


# --- resources -------------------------------------------------------------------------------


async def test_today_and_item_resources_render_markdown(alex):
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        item = ok(
            await call(
                client,
                "slate_item",
                "create",
                list_id=todo["id"],
                title="Renew passport",
                body_md="Form DS-82",
                due_at="2000-01-01",
                priority="urgent",
            )
        )
        resources = {str(r.uri) for r in (await client.list_resources()).resources}
        assert resources == {"slate://today"}
        templates = (await client.list_resource_templates()).resource_templates
        assert [t.uri_template for t in templates] == ["slate://item/{id}"]

        today = (await client.read_resource("slate://today")).contents[0]
        assert today.mime_type == "text/markdown"
        assert "## Overdue" in today.text and "Renew passport" in today.text
        doc = (await client.read_resource(f"slate://item/{item['id']}")).contents[0]
        assert doc.text.startswith("# Renew passport")
        assert "Form DS-82" in doc.text and "urgent" in doc.text
        assert "<" not in doc.text  # markdown, never HTML


# --- stdio mode: a thin REST client ----------------------------------------------------------


async def test_stdio_mode_calls_rest_with_the_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/items/missing"):
            return httpx.Response(404, json={"detail": "item missing not found"})
        if request.url.path.endswith("/lists"):
            return httpx.Response(
                422, json={"detail": [{"loc": ["body", "name"], "msg": "Field required"}]}
            )
        return httpx.Response(201, json={"id": "abc", "title": "x", "parent_id": None})

    backend = mcp_server.RestBackend(
        httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://slate.example.com"
        ),
        token="slate_tok_secret",
    )
    async with Client(mcp_server.build_server(lambda _ctx: backend)) as client:
        made = ok(
            await client.call_tool(
                "slate_item",
                {
                    "action": "create",
                    "params": {"list_id": "L1", "title": "x"},
                    "session_ref": "laptop",
                },
            )
        )
        assert made == {"id": "abc", "title": "x"}  # nulls are dropped
        assert "item missing not found" in err(
            await call(client, "slate_item", "get", id="missing")
        )
        assert "name: Field required" in err(
            await call(client, "slate_list", "create", section_id="S", name="x")
        )

    first = seen[0]
    assert (first.method, first.url.path) == ("POST", "/api/v1/items")
    assert json.loads(first.content) == {"list_id": "L1", "title": "x"}
    assert first.headers["authorization"] == "Bearer slate_tok_secret"
    assert first.headers["x-slate-session-ref"] == "laptop"


async def test_stdio_mode_end_to_end_against_the_rest_api(client, alex, todo):
    """The REST client's paths and bodies are right: route MockTransport into the real app."""

    def bridge(request: httpx.Request) -> httpx.Response:
        r = client.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        # TestClient already decoded a gzipped body; do not let httpx decode it twice.
        headers = {
            k: v for k, v in r.headers.items() if k not in ("content-encoding", "content-length")
        }
        return httpx.Response(r.status_code, content=r.content, headers=headers)

    token = alex["Authorization"].removeprefix("Bearer ")
    backend = mcp_server.RestBackend(
        httpx.AsyncClient(transport=httpx.MockTransport(bridge), base_url="http://slate"),
        token=token,
    )
    async with Client(mcp_server.build_server(lambda _ctx: backend)) as mcp:
        item = ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="Via REST"))
        ok(await call(mcp, "slate_item", "update", id=item["id"], title="Renamed"))
        ok(await call(mcp, "slate_item", "set_status", id=item["id"], status="done"))
        kids = ok(await call(mcp, "slate_item", "split", id=item["id"], children=[{"title": "a"}]))
        assert kids[0]["parent_id"] == item["id"]
        ok(await call(mcp, "slate_item", "comment", id=item["id"], body_md="hi"))
        ok(await call(mcp, "slate_item", "archive", id=item["id"]))
        ok(await call(mcp, "slate_item", "unarchive", id=item["id"]))
        assert ok(await call(mcp, "slate_item", "delete", id=kids[0]["id"])) == {
            "deleted": kids[0]["id"]
        }
        assert ok(await call(mcp, "slate_item", "get", id=item["id"]))["title"] == "Renamed"
        assert ok(await call(mcp, "slate_item", "list", q="renamed"))[0]["id"] == item["id"]
        assert ok(await call(mcp, "slate_item", "list", q="renamed", fields=["title"])) == [
            {"id": item["id"], "title": "Renamed"}
        ]
        assert ok(await call(mcp, "slate_item", "history", id=item["id"]))
        assert ok(await call(mcp, "slate_item", "versions", id=item["id"])) == []
        ok(await call(mcp, "slate_item", "assign", id=item["id"], assignees=["alex"]))
        ok(await call(mcp, "slate_item", "tag", id=item["id"], tags=["x"]))
        ok(
            await call(
                mcp,
                "slate_list",
                "set_statuses",
                id=todo["id"],
                statuses=[
                    {"name": "open"},
                    {"name": "active", "kind": "active"},
                    {"name": "done", "kind": "done"},
                ],
            )
        )
        ok(await call(mcp, "slate_list", "update", id=todo["id"], name="Todo 2"))
        assert ok(await call(mcp, "slate_list", "list"))[0]["name"] == "Todo 2"
        sec = ok(await call(mcp, "slate_section", "list"))[0]
        ok(await call(mcp, "slate_section", "update", id=sec["id"], name="Home"))
        ok(await call(mcp, "slate_section", "create", name="Work", audience="work"))
        ok(await call(mcp, "slate_tag", "create", name="y"))
        ok(await call(mcp, "slate_tag", "rename", tag="y", name="z"))
        assert "z" in [t["name"] for t in ok(await call(mcp, "slate_tag", "list"))]
        await exercise_clickup_parity(mcp, todo)
        await exercise_follow_files_views(mcp, item["id"])
        await exercise_automations(mcp, todo)
        await exercise_reminders(mcp, todo)
        await exercise_docs(mcp, todo)
        await exercise_work(mcp, todo)
        await exercise_chat(mcp, todo)
        await exercise_trash_export(mcp, todo)
        rule = ok(
            await call(
                mcp,
                "slate_recurrence",
                "create",
                list_id=todo["id"],
                title_template="Trash {date}",
                rrule="FREQ=DAILY",
                lead_days=0,
            )
        )
        assert ok(await call(mcp, "slate_recurrence", "get", id=rule["id"]))["id"] == rule["id"]
        assert ok(await call(mcp, "slate_recurrence", "list", list_id=todo["id"]))
        ok(await call(mcp, "slate_recurrence", "update", id=rule["id"], lead_days=2))
        ok(await call(mcp, "slate_recurrence", "pause", id=rule["id"]))
        ok(await call(mcp, "slate_recurrence", "resume", id=rule["id"]))
        assert ok(await call(mcp, "slate_recurrence", "run_now", id=rule["id"]))["spawned"]
        assert ok(await call(mcp, "slate_recurrence", "preview", rrule="FREQ=DAILY", count=1))
        dated = ok(
            await call(
                mcp, "slate_item", "create", list_id=todo["id"], title="Mow", due_at="2031-01-01"
            )
        )
        adopted = ok(
            await call(mcp, "slate_recurrence", "adopt", id=rule["id"], item_id=dated["id"])
        )
        assert adopted["recurrence_id"] == rule["id"]
        hook = ok(
            await call(
                mcp, "slate_webhook", "create", url="https://a.example.com", events=["item.due"]
            )
        )
        assert hook["secret"].startswith("whsec_")
        assert [h["id"] for h in ok(await call(mcp, "slate_webhook", "list"))] == [hook["id"]]
        ok(await call(mcp, "slate_webhook", "update", id=hook["id"], disabled=True))
        page = ok(await call(mcp, "slate_webhook", "deliveries", id=hook["id"], limit=5))
        assert page["deliveries"] == []
        assert ok(await call(mcp, "slate_account", "me"))["account"]["handle"] == "alex"
        assert ok(await call(mcp, "slate_account", "list"))
        acct = ok(
            await call(
                mcp,
                "slate_account",
                "create",
                handle="agent3",
                display_name="Agent 3",
                kind="agent",
            )
        )
        ok(await call(mcp, "slate_account", "create_token", account_id=acct["id"], scopes=["read"]))
        assert set(ok(await call(mcp, "slate_today"))) >= {"overdue", "today", "waiting"}
        assert ok(await call(mcp, "slate_events", since="0", limit=5))["has_more"] is True
        batched = ok(
            await call(
                mcp,
                "slate_batch",
                ops=[
                    {
                        "ref": "b",
                        "tool": "item",
                        "action": "create",
                        "params": {"list_id": todo["id"], "title": "Batched"},
                    },
                    {"tool": "comment", "action": "add", "params": {"id": "$b.id", "body_md": "x"}},
                ],
            )
        )
        assert batched["results"][0]["result"]["title"] == "Batched"
        reported = ok(
            await call(
                mcp, "slate_item", "report", id=item["id"], state="working", note="x", progress=5
            )
        )
        assert reported["agent"]["state"] == "working"
        assert ok(await call(mcp, "slate_agents", kind="all"))
        assert "queue" in ok(await call(mcp, "slate_checkin", limit=5))
        assert ok(await call(mcp, "slate_events", since="0", actor="alex", limit=1))["events"]
        tail = ok(await call(mcp, "slate_events", tail=True, actor_kind="person", limit=2))
        assert len(tail["events"]) == 2 and tail["has_more"] is False
        doc = (await mcp.read_resource(f"slate://item/{item['id']}")).contents[0]
        assert doc.text.startswith("# Renamed")
        assert "Today" in (await mcp.read_resource("slate://today")).contents[0].text


def test_stdio_main_needs_url_and_token(monkeypatch):
    monkeypatch.delenv("SLATE_TOKEN", raising=False)
    monkeypatch.setenv("SLATE_URL", "http://slate.example.com")
    with pytest.raises(SystemExit, match="SLATE_TOKEN"):
        mcp_server.main()


async def test_comments_checklists_attachments_and_activity_through_mcp(alex):
    import base64

    async with http_mcp(alex) as client:
        todo = await make_list(client)
        item = ok(await call(client, "slate_item", "create", list_id=todo["id"], title="Plants"))
        c = ok(await call(client, "slate_comment", "add", id=item["id"], body_md="hi @alex"))
        assert c["mentions"] == ["alex"]
        reacted = ok(await call(client, "slate_comment", "react", id=c["id"], emoji="👍"))
        assert reacted["reactions"][0]["emoji"] == "👍"
        edited = ok(await call(client, "slate_comment", "edit", id=c["id"], body_md="hello"))
        assert edited["edited_at"]
        assert len(ok(await call(client, "slate_comment", "list", id=item["id"]))) == 1

        cl = ok(
            await call(
                client,
                "slate_checklist",
                "create",
                item_id=item["id"],
                name="Steps",
                items=["call"],
            )
        )
        entry = cl["items"][0]["id"]
        cl = ok(await call(client, "slate_checklist", "update_entry", entry_id=entry, done=True))
        assert cl["items"][0]["done"] is True
        cl = ok(
            await call(client, "slate_checklist", "add_entry", checklist_id=cl["id"], title="pay")
        )
        assert [e["title"] for e in cl["items"]] == ["call", "pay"]

        att = ok(
            await call(
                client,
                "slate_item",
                "attach",
                id=item["id"],
                filename="r.txt",
                content_base64=base64.b64encode(b"receipt").decode(),
            )
        )
        assert att["size"] == 7 and att["content_type"] == "text/plain"
        assert "not valid base64" in err(
            await call(
                client,
                "slate_item",
                "attach",
                id=item["id"],
                filename="r.txt",
                content_base64="%%%",
            )
        )
        watching = ok(await call(client, "slate_item", "watch", id=item["id"]))
        assert [w["handle"] for w in watching] == ["alex"]

        feed = ok(await call(client, "slate_item", "activity", id=item["id"]))
        assert {e["kind"] for e in feed} == {"comment", "event"}
        types = {e["event"]["type"] for e in feed if e["kind"] == "event"}
        assert {"checklist.entry_checked", "item.attachment_added", "item.watched"} <= types
        await exercise_follow_files_views(client, item["id"])


async def exercise_follow_files_views(client: Client, item_id: str) -> None:
    """Watchers, attachments and view unarchive: the same calls in-process and over REST."""
    import base64

    w = ok(await call(client, "slate_item", "set_watchers", id=item_id, watchers=["alex"]))
    assert [x["handle"] for x in w] == ["alex"]
    assert [x["handle"] for x in ok(await call(client, "slate_item", "watchers", id=item_id))] == [
        "alex"
    ]
    assert ok(await call(client, "slate_item", "set_watchers", id=item_id, watchers=[])) == []
    att = ok(
        await call(
            client,
            "slate_item",
            "attach",
            id=item_id,
            filename="n.txt",
            content_base64=base64.b64encode(b"note").decode(),
        )
    )
    files = ok(await call(client, "slate_item", "attachments", id=item_id))
    assert att["id"] in [f["id"] for f in files]
    assert ok(await call(client, "slate_item", "detach", id=att["id"])) == {"deleted": att["id"]}
    assert att["id"] not in [
        f["id"] for f in ok(await call(client, "slate_item", "attachments", id=item_id))
    ]
    view = ok(
        await call(
            client,
            "slate_view",
            "create",
            name="Soon",
            scope_kind="everything",
            config={"layout": "list"},
        )
    )
    assert ok(await call(client, "slate_view", "archive", id=view["id"]))["archived_at"]
    assert (
        ok(await call(client, "slate_view", "unarchive", id=view["id"])).get("archived_at") is None
    )


async def test_batch_through_mcp_is_atomic(alex):
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        out = ok(
            await call(
                client,
                "slate_batch",
                ops=[
                    {
                        "ref": "a",
                        "tool": "item",
                        "action": "create",
                        "params": {"list_id": todo["id"], "title": "One"},
                    },
                    {
                        "tool": "item",
                        "action": "assign",
                        "params": {"id": "$a.id", "assignees": ["alex"]},
                    },
                ],
            )
        )
        assert [r["ok"] for r in out["results"]] == [True, True]
        failed = err(
            await call(
                client,
                "slate_batch",
                ops=[
                    {
                        "tool": "item",
                        "action": "create",
                        "params": {"list_id": todo["id"], "title": "Two"},
                    },
                    {"tool": "item", "action": "get", "params": {"id": "missingitem1"}},
                ],
            )
        )
        assert failed.startswith("op 1:") and "Nothing was written" in failed
        titles = [i["title"] for i in ok(await call(client, "slate_item", "list"))]
        assert titles == ["One"]


async def test_report_and_agents_roster_through_mcp(alex, make_account):
    acct, agent = make_account("agent")
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        item = ok(await call(client, "slate_item", "create", list_id=todo["id"], title="Job"))
    async with http_mcp(agent) as client:
        out = ok(
            await call(
                client, "slate_item", "report", id=item["id"], state="review", note="Have a look"
            )
        )
        assert out["agent"] == {
            "state": "review",
            "by": acct["id"],
            "at": out["agent"]["at"],
            "note": "Have a look",
        }
        roster = ok(await call(client, "slate_agents"))
        assert roster[0]["account"]["handle"] == "agent"
        assert roster[0]["last_seen_via"] == "mcp"
        assert roster[0]["working"][0]["state"] == "review"
        msg = err(await call(client, "slate_item", "report", id=item["id"], state="nope"))
        assert "state" in msg


# --- MCP -------------------------------------------------------------------------------------


async def test_share_and_audit_through_mcp(alex):
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        guest = ok(
            await call(
                client,
                "slate_account",
                "create",
                handle="guest",
                display_name="Guest",
                kind="person",
                role="guest",
            )
        )
        share = ok(
            await call(
                client,
                "slate_share",
                "set",
                account_id=guest["id"],
                scope_kind="list",
                scope_id=todo["id"],
                level="comment",
            )
        )
        assert share["level"] == "comment"
        who = ok(await call(client, "slate_share", "who", scope_kind="list", scope_id=todo["id"]))
        assert {r["account_id"] for r in who} >= {guest["id"]}
        ok(
            await call(
                client,
                "slate_share",
                "private",
                scope_kind="list",
                scope_id=todo["id"],
                private=True,
            )
        )
        page = ok(await call(client, "slate_audit", "list", type="share.", limit=10))
        assert [e["type"] for e in page["entries"]] == ["share.granted"]
        assert page["entries"][0]["actor"]["handle"] == "alex"
        facets = ok(await call(client, "slate_audit", "facets"))
        assert "share.granted" in facets["types"]


async def test_item_list_by_external_ref_and_409_carries_existing_id(alex):
    async with http_mcp(alex) as client:
        todo = await make_list(client)
        a = ok(
            await call(
                client, "slate_item", "create", list_id=todo["id"], title="A", external_ref="p:1"
            )
        )
        ok(await call(client, "slate_item", "create", list_id=todo["id"], title="B"))
        found = ok(await call(client, "slate_item", "list", external_ref="p:1"))
        assert [i["id"] for i in found] == [a["id"]]
        again = err(
            await call(
                client, "slate_item", "create", list_id=todo["id"], title="A2", external_ref="p:1"
            )
        )
        assert a["id"] in again
