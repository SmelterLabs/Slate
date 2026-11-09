"""Board prefs live on the account, so every device shows the same board."""

API = "/api/v1"


def test_prefs_start_empty_and_come_back_on_me(client, alex):
    assert client.get(f"{API}/me", headers=alex).json()["prefs"] == {}

    r = client.patch(f"{API}/me/prefs", json={"theme": "dark"}, headers=alex)
    assert r.status_code == 200, r.text
    assert r.json() == {"theme": "dark"}
    assert client.get(f"{API}/me", headers=alex).json()["prefs"] == {"theme": "dark"}


def test_patch_merges_and_null_removes(client, alex):
    client.patch(f"{API}/me/prefs", json={"theme": "light"}, headers=alex)
    folds = {"everything": {"due:today": True, "none": False}}
    r = client.patch(f"{API}/me/prefs", json={"collapsed": folds}, headers=alex)
    assert r.json() == {"theme": "light", "collapsed": folds}

    r = client.patch(f"{API}/me/prefs", json={"theme": None}, headers=alex)
    assert r.json() == {"collapsed": folds}


def test_prefs_are_per_account(client, alex, make_account):
    _, agent = make_account("agent")
    client.patch(f"{API}/me/prefs", json={"theme": "dark"}, headers=alex)
    assert client.get(f"{API}/me", headers=agent).json()["prefs"] == {}


def test_change_is_an_event_with_the_new_prefs(client, alex):
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    client.patch(f"{API}/me/prefs", json={"theme": "dark"}, headers=alex)
    events = client.get(
        f"{API}/events", params={"type": "account.prefs_changed"}, headers=alex
    ).json()
    rows = events["events"] if isinstance(events, dict) else events
    assert rows[-1]["subject_id"] == me["id"]
    assert rows[-1]["actor_id"] == me["id"]
    assert rows[-1]["after"] == {"theme": "dark"}


def test_bad_prefs_are_refused(client, alex):
    assert client.patch(f"{API}/me/prefs", json={"theme": "pink"}, headers=alex).status_code == 422
    bad = {"collapsed": {"everything": {"x": "yes"}}}
    assert client.patch(f"{API}/me/prefs", json=bad, headers=alex).status_code == 422
    huge = {"notes": "x" * 40_000}
    assert client.patch(f"{API}/me/prefs", json=huge, headers=alex).status_code in (400, 422)


def test_read_token_cannot_change_prefs(client, make_account):
    _, reader = make_account("reader", scopes=("read",))
    assert (
        client.patch(f"{API}/me/prefs", json={"theme": "dark"}, headers=reader).status_code == 403
    )


def test_last_view_and_view_drafts_round_trip(client, alex):
    """The board remembers the last view tab per scope and its unsaved changes, per account."""
    prefs = {
        "last_view": {"list:abc": "view1"},
        "view_drafts": {"view1": {"layout": "table", "sort": [{"field": "due", "dir": "asc"}]}},
    }
    r = client.patch(f"{API}/me/prefs", json=prefs, headers=alex)
    assert r.status_code == 200, r.text
    assert client.get(f"{API}/me", headers=alex).json()["prefs"] == prefs
    assert client.patch(f"{API}/me/prefs", json={"view_drafts": None}, headers=alex).json() == {
        "last_view": {"list:abc": "view1"}
    }


def test_bad_remembered_views_are_refused(client, alex):
    for bad in (
        {"last_view": {"list:abc": 3}},
        {"last_view": ["view1"]},
        {"view_drafts": {"view1": "table"}},
    ):
        assert client.patch(f"{API}/me/prefs", json=bad, headers=alex).status_code == 422
