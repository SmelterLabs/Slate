"""The agent stream's `control` message: a new message or Stop for a run that is going now."""

from __future__ import annotations

from contextlib import AsyncExitStack

import httpx

from test_agent_stream import API, _agent, _item, _setup
from test_api_events import _bearer, _open, live  # noqa: F401 (live is a fixture)


async def test_a_message_and_stop_for_a_running_task_arrive_as_control(live):  # noqa: F811
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        admin = _bearer(live.admin_token)
        item = await _item(client, live.admin_token, todo["id"], "Do it", assignees=["agent"])
        idle = await _item(client, live.admin_token, todo["id"], "Later", assignees=["agent"])
        took = await client.post(
            f"{API}/work/next", json={"item_id": item["id"]}, headers=_bearer(tok)
        )
        assert took.json()["item"]["id"] == item["id"]
        stream = await _open(client, stack, f"{API}/checkin/stream", headers=_bearer(tok))
        assert await stream.ready() == []

        async def until(kind: str, type_: str) -> list:
            """The messages up to and including the first `kind` one for an event `type_`."""
            got = []
            for _ in range(8):
                got.append(await stream.event(timeout=3))
                if got[-1].event == kind and got[-1].data["type"] == type_:
                    return got
            raise AssertionError([(m.event, m.data.get("type")) for m in got])

        # An Ask on the task it is running: control. On one it is not running: a work hint only.
        r = await client.post(
            f"{API}/items/{item['id']}/comments",
            json={"body_md": "Also this", "assignee": "agent"},
            headers=admin,
        )
        assert r.status_code == 201, r.text
        got = await until("control", "comment.assigned")
        assert got[-1].data["item_id"] == item["id"]
        assert not [m for m in got if m.event == "work"]
        await client.post(
            f"{API}/items/{idle['id']}/comments",
            json={"body_md": "And this", "assignee": "agent"},
            headers=admin,
        )
        got = await until("work", "comment.assigned")
        assert got[-1].data["item_id"] == idle["id"]
        assert not [m for m in got if m.event == "control"]

        r = await client.post(f"{API}/items/{item['id']}/stop", headers=admin)
        assert r.status_code == 202, r.text
        got = await until("control", "item.stop_requested")
        assert got[-1].data["item_id"] == item["id"]
