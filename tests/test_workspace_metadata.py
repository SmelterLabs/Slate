"""Workspace metadata is identical through every entry point."""

from slate import mcp_server, mcp_tools
from slate.settings import settings
from test_mcp import call, http_mcp, ok

API = "/api/v1"


async def test_self_metadata_parity_with_configured_workspace(client, alex, monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Asia/Tokyo")
    monkeypatch.setattr(settings, "link_schemes", ["https", "http", "myapp"])
    prefs = {"theme": "light", "custom": {"nested": ["preserved"]}}
    assert client.patch(f"{API}/me/prefs", json=prefs, headers=alex).status_code == 200
    rest = client.get(f"{API}/me", headers=alex).json()
    async with http_mcp(alex) as mcp:
        local = ok(await call(mcp, "slate_account", "me"))
    # The wire intentionally omits nulls for every tool; local bindings must be exact.
    assert local == mcp_server.compact(rest)
    backend = mcp_server.LocalBackend(alex["Authorization"].removeprefix("Bearer "))
    spec = mcp_tools.TOOLS["slate_account"]
    assert await backend.run(spec, "me", spec.actions["me"].params(), None) == rest
    assert rest["prefs"] == prefs
    assert rest["link_schemes"] == ["https", "http", "myapp"]
    assert rest["timezone"] == "Asia/Tokyo"
