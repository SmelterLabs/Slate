"""Chat shares its rules with the rest of Slate instead of keeping its own copies: Slate's own
account handle, the emoji rule for reactions, the `#<id>` reference pattern, the read limit."""

from __future__ import annotations

from slate.service import aiadd, chat, comments, due, watchers

API = "/api/v1"


def test_one_handle_for_slates_own_account():
    for module in (chat, aiadd, watchers):
        assert module.SYSTEM_HANDLE is due.SYSTEM_HANDLE, module.__name__


def test_chat_and_comments_check_reaction_emoji_the_same_way():
    assert chat.clean_emoji is comments.clean_emoji
    assert not hasattr(chat, "MAX_EMOJI")


def test_the_hash_reference_is_part_of_the_item_reference():
    body = "see #Abcdef123456 and https://x.example.com/?task=Zyxwvu654321 and a#Qwerty098765"
    assert chat.HASH_REF_RE.findall(body) == ["Abcdef123456"]
    assert chat.item_refs(body) == ["Abcdef123456", "Zyxwvu654321"]


def test_no_unused_channel_key_helper():
    assert not hasattr(chat, "channel_key")


def test_the_exposure_limit_is_the_page_limit(client, alex, todo):
    key = f"list:{todo['id']}"
    ids = [f"x{i:011d}" for i in range(chat.MAX_PAGE + 1)]
    r = client.post(f"{API}/channels/{key}/read", json={"message_ids": ids}, headers=alex)
    assert r.status_code == 422
