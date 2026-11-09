"""Real HTTP streams and simultaneous acceptance on an isolated file-backed server."""

import asyncio
from contextlib import AsyncExitStack

import httpx
import pytest

from test_api_events import API, _bearer, _open
from test_api_events import live as live_fixture

live = live_fixture


async def setup_project(client, live):
    admin = _bearer(live.admin_token)

    async def post(path, body):
        response = await client.post(API + path, json=body, headers=admin)
        assert response.status_code in (200, 201), response.text
        return response.json()

    section = await post("/sections", {"name": "Customer", "audience": "work"})
    await client.patch(f"{API}/sections/{section['id']}", json={"private": True}, headers=admin)
    shared = await post("/lists", {"name": "Work", "section_id": section["id"]})
    other = await post("/sections", {"name": "Secret section", "audience": "personal"})
    hidden = await post("/lists", {"name": "Secret list", "section_id": other["id"]})
    invite = await post(
        "/invitations", {"section_id": section["id"], "handle": "guest", "display_name": "Guest"}
    )
    return section, shared, hidden, invite


async def test_concurrent_acceptance_is_single_use(live):
    async with httpx.AsyncClient(base_url=live.url) as client:
        _, _, _, invite = await setup_project(client, live)

        async def consume():
            async with httpx.AsyncClient(base_url=live.url) as browser:
                return await browser.post(
                    API + "/invitations/accept",
                    json={"secret": invite["secret"]},
                    headers={"X-Slate-Client": "browser", "Origin": live.url},
                )

        results = await asyncio.gather(consume(), consume())
        assert sorted(r.status_code for r in results) == [200, 410]


async def test_cookie_sse_replay_live_hidden_content_and_revocation(live, caplog):
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        section, shared, hidden, invite = await setup_project(client, live)
        admin = _bearer(live.admin_token)
        browser = {"X-Slate-Client": "browser", "Origin": live.url}
        accepted = await client.post(
            API + "/invitations/accept", json={"secret": invite["secret"]}, headers=browser
        )
        assert accepted.status_code == 200, accepted.text
        for lst, title in [(shared, "Shared replay"), (hidden, "Secret replay")]:
            await client.post(
                API + "/items", json={"list_id": lst["id"], "title": title}, headers=admin
            )
        stream = await _open(client, stack, API + "/events/stream?since=0&type=item.created")
        replay = await stream.ready()
        assert len(replay) == 1 and replay[0].data["after"]["title"] == "Shared replay"
        await client.post(
            API + "/items", json={"list_id": hidden["id"], "title": "Secret live"}, headers=admin
        )
        created = await client.post(
            API + "/items", json={"list_id": shared["id"], "title": "Shared live"}, headers=admin
        )
        event = await stream.event(timeout=1)
        assert event.data["item_id"] == created.json()["id"]
        assert event.data["after"]["title"] == "Shared live"
        # A filtered stream still notices revocation (not only item.created events).
        assert (
            await client.delete(f"{API}/invitations/{invite['id']}", headers=admin)
        ).status_code == 204
        with pytest.raises(StopAsyncIteration):
            await stream.next(timeout=1)
        assert (await client.get(API + "/me")).status_code == 401
        assert (await client.get(API + "/events/stream")).status_code == 401
        assert invite["secret"] not in caplog.text
        assert client.cookies.get("slate_session") not in caplog.text


async def test_share_removal_closes_existing_cookie_stream(live):
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        section, _, _, invite = await setup_project(client, live)
        accepted = await client.post(
            API + "/invitations/accept",
            json={"secret": invite["secret"]},
            headers={"X-Slate-Client": "browser", "Origin": live.url},
        )
        assert accepted.status_code == 200
        stream = await _open(client, stack, API + "/events/stream")
        await stream.ready()
        result = await client.put(
            API + "/shares",
            headers=_bearer(live.admin_token),
            json={
                "account_id": accepted.json()["account_id"],
                "scope_kind": "section",
                "scope_id": section["id"],
                "level": None,
            },
        )
        assert result.status_code == 200
        with pytest.raises(StopAsyncIteration):
            await stream.next(timeout=1)
        assert (await client.get(API + "/sections")).json() == []
