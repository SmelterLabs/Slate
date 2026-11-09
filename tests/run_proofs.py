"""Explicit test caller proof wallet, populated only by that caller's successful takes.

Never reads server internals or held listings. Negative fencing tests use raw requests.
"""


def remember(client, headers, taken):
    proofs = getattr(client, "execution_proofs", {})
    item, chat, doc = taken.get("item"), taken.get("chat"), taken.get("doc")
    if item:
        key, lease = item["id"], taken["lease"]
    elif chat:
        key, lease = (chat["channel"], chat.get("thread_id")), chat["lease"]
    elif doc:
        key, lease = ("doc", doc["doc_id"], doc.get("thread_id")), doc["lease"]
    else:
        return taken
    proofs[(headers["Authorization"], key)] = lease["run_id"]
    client.execution_proofs = proofs
    return taken


def run_fields(client, headers, key, **body):
    proof = getattr(client, "execution_proofs", {}).get((headers["Authorization"], key))
    return {"run_id": proof, **body}
