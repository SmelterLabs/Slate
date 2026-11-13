# Private shared projects and guest sessions

What a guest of a private section gets, how the browser session is minted, extended and
re-issued, and where the security contracts are. The permission model itself is
`docs/permissions.md`; the invitation/session contract is `docs/shared-project-collaboration.md`;
digest privacy and extraction bounds are `docs/collaboration-security.md`.

## Private shared projects

See [shared-project collaboration](shared-project-collaboration.md) for the invitation/session
security contract, migration `d41b0c8e2a19`, section project home, scoped directory/event rules, and
sidebar behavior. Browser guests use expiring HttpOnly sessions; bearer tokens work alongside
them.

The guest invitation screen explains recovery for invalid/used/expired/revoked links separately
from transient connection, rate-limit and server errors. `invitationRecovery` uses
`ApiError.status`, not text matching. Missing invitation secrets are explained without submitting.
See `web/e2e/invitation-recovery.spec.ts`.

**Sessions follow the shares, end visibly, and can be extended or re-issued.**
`collaboration.accept` mints the browser token with `limit={"level": "edit"}` and no `section_ids`
pin, so the guest's reach is exactly the account's shares and a section shared later appears without
a new link (the live stream reloads on `share.*`). `GET /me` carries `session_expires_at`
for cookie sessions and re-sets the cookie's `max-age` on every load (the token's `expires_at` is
the authority; the cookie is only the browser's ceiling); the sidebar footer shows "Access ends
<date>" to guests. `service/sessions.py:notify_expiring` is a scheduler hook (Health key
`sessions`): three days before a session ends it records `session.expiring` for the guest and the
inviter (inbox reason `access`, pushed by default, both board and server reason tables), idempotent
through `tokens.expiry_notified_at` (migration `b7c2d9e4f1a3`), re-armed by an extension. Admin
routes `POST /invitations/{id}/extend` (`session.extended`, +30 days from now, refused once
cancelled or signed out) and `POST /invitations/{id}/reissue` (a fresh single-use secret for the
same guest and section, `after.reissued_from`; the old link stays valid) back the **Extend 30 days**
and **Copy a link for another device** buttons in `InvitationPanel.svelte`, whose rows show the
session's end. `lib/collaboration.ts:inviteSecretFrom` lets `board.signIn` accept a pasted
invitation link (the installed app has no address bar) through `POST /invitations/accept` in cookie
mode. Gotcha: `GET /invitations` rows and `/me` read the token row, so a sign-out shows as
`session_revoked_at`; the panel reloads on `invitation.*`, `session.extended` and `token.revoked`.
Tests: `tests/test_guest_sessions.py`, `web/src/lib/collaboration.test.ts`, the pasted-link path in
`web/e2e/collaboration.spec.ts`.

## Members-only chat (workspace agents on a project)

A **workspace agent** is any agent account that is not a guest
(`chat.workspace_agent`); a project's own agent is a guest. `Message.members_only` (migration
`e3b7c1a9d5f2`) is set at post time by `chat.members_only_post`: a
workspace agent's post, a non-guest's post that @mentions one, and every reply in a thread whose
root is members-only; an edit that adds such a mention sets it and nothing ever clears it.
Guests (`Access.role == guest`, people and agents) are filtered in `access.read_criteria`
(`_chat_visible(..., members)`, which also covers the Events table through its message-subject
clause) and in the live gate (`EventGate._members_only`, cached per message id, for every
`message.*` event). `ChatWake` rows of workspace agents are hidden from guests (no "is
working…" line), `collaboration.project` omits workspace agents from a guest's participants, and
`access._project_agents(guest=True)` gives a guest only guest agents' own chats (no door to a
workspace agent's `agent:<id>`), and `chatwake._reaches` stops a guest's `@mention` of a
workspace agent from waking it and a members-only post from waking a guest agent. Tests:
`tests/test_members_only_chat.py`. `members_only` is a chat flag only: card comments and items
are work, not conversation, and follow the rule below.

## Guests never see a workspace agent

Guests see no trace of a workspace agent. `service/guests.py` holds the
rule and its write-side refusals (Ask, reassign, assign, Run now: `Forbidden`; a guest's
mentions of a workspace agent are dropped before they notify or wake, in card comments, doc
threads, page bodies and chat; a guest's reply is never its turn). On a card assigned to a
workspace agent a guest changes neither what it asks nor when it runs: the write check
(`access.check_object` → `_refuse_steering`) refuses a guest's change to the title, body,
acceptance, due or start date, a checklist, or a reopen, with a message that names nobody, so
text a guest wrote never becomes the agent's instructions and a guest never opens its due gate. Read side, guests only:
`access._guest_criteria` hides Comment and DocComment rows by or addressed to a workspace agent,
its ItemAssignee rows, its events (plus the events of comments addressed to it), and an
`item.assigned` whose only change is a workspace agent; `items.views` drops its agent badge,
session links, waits and `created_by` (guest views are never cached: `item_cache._restricted`).

What a visible row says about a workspace agent is blanked in one place, `access.scrub`: its id
becomes null, and its id, its handle or an entry naming it drops out of a list.
`access.event_snapshots` applies it to every stored event's `before` and `after`, and
`core.event_payload` (the only way an event leaves the service layer; `EventOut` accepts no
stored row) blanks a workspace agent's `actor_id`, so history, `GET /events`, the audit log and
its CSV, the inbox, the activity feed, the check-in, a guest agent's work context and the export
all carry the same scrub. Views that hold account ids call it too: checklist entries, pages
(`created_by`, `updated_by`, versions, revisions), the export's raw rows (whose tags are
`tags.list_tags`, and whose links leave out an end the reader cannot see), and the Box view's
roster (`access.participant_ids`). The live gate's `EventGate._for_guest` uses the same
`_scrubbed` on what the bus carries, drops its talk events and events of hidden comments, and
blanks its `actor_id`. `tests/test_guest_boundary.py` calls every GET route as a guest person
and as a guest agent and fails when a workspace agent's handle or id appears, so a new route
cannot forget the rule. Other tests: `tests/test_members_only_chat.py`.

## Digest privacy and extraction bounds

Digest privacy and hostile DOCX bounds: [security contract](collaboration-security.md).
Historical digest snapshots are projected for the current reader without changing stored
events; scoped counts describe retained snapshot entries, not unverifiable old totals.
DOCX extraction is a streaming, budgeted XML target, not repeated descendant traversal.
