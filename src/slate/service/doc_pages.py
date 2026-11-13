"""Page text (part of `slate.service.docs`): templates, the body, page history, and filing a
task from a page.

A title or body save by the account that made the newest version, within `FOLD_MINUTES` of its
last save, folds into that version; anything else (another account, a pause, `new_version`, a
restore) starts a new one. The event log still has every save. Embeds are plain text so agents
read and write them without a special API: `[[task:<id>]]` on a line of its own renders as a
live task card on the board (inline, a chip), `[[doc:<id>]]` links another page.

Writes: `doc.created`, `doc.updated` (field `title`, `icon`, `body_md` or `version_no`),
`doc.version_restored`, `doc.mentioned` (when a save adds `@handle`s the page did not have,
`after` = `{doc_id, title, mentions, excerpt}`, so the inbox, check-in, push and webhooks hear
about it like a comment mention; only newly added handles count, and a push agent among them is
woken for a doc turn).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from slate.ids import new_id
from slate.models import Doc, DocFile, DocVersion, Item, utcnow
from slate.service import guests
from slate.service.comments import mentions_in
from slate.service.core import Actor, Change, apply_change
from slate.service.doc_tree import (
    KINDS,
    MAX_DEPTH,
    all_docs,
    depth,
    fresh_doc,
    get_doc,
    place,
    renumber,
    root_of,
    siblings,
    tree_order,
)
from slate.service.errors import Conflict, Invalid, NotFound

MAX_BODY = 500_000
FOLD_MINUTES = 10
EMBED_RE = re.compile(r"\[\[(task|doc):([0-9A-Za-z]{12})\]\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

# Starting points offered by "New doc" on the board and `template` on create.
TEMPLATES: dict[str, dict[str, str]] = {
    "blank": {"label": "Blank page", "title": "Untitled", "icon": "", "body_md": ""},
    "meeting": {
        "label": "Meeting notes",
        "title": "Meeting notes",
        "icon": "🗓️",
        "body_md": "## Attendees\n\n- \n\n## Agenda\n\n1. \n\n## Notes\n\n\n\n"
        "## Decisions\n\n- \n\n## Action items\n\n- [ ] \n",
    },
    "plan": {
        "label": "Project plan",
        "title": "Project plan",
        "icon": "🧭",
        "body_md": "## Goal\n\nWhat done looks like, in one paragraph.\n\n## Why\n\n\n\n"
        "## Milestones\n\n| Milestone | Owner | Due | Status |\n| --- | --- | --- | --- |\n"
        "|  |  |  |  |\n\n## Tasks\n\nSelect a line and choose **Create task** to file it.\n\n"
        "## Risks\n\n- \n\n## Notes\n\n",
    },
    "decision": {
        "label": "Decision record",
        "title": "Decision: ",
        "icon": "⚖️",
        "body_md": "## Context\n\n\n\n## Options\n\n1. \n2. \n\n## Decision\n\n\n\n"
        "## Consequences\n\n- \n",
    },
    "runbook": {
        "label": "Runbook",
        "title": "Runbook: ",
        "icon": "📘",
        "body_md": "## When to use this\n\n\n\n## Steps\n\n- [ ] \n- [ ] \n- [ ] \n\n"
        "## If it goes wrong\n\n\n\n## Contacts\n\n- \n",
    },
    "agent_log": {
        "label": "Agent work log",
        "title": "Work log",
        "icon": "🤖",
        "body_md": "Agents append dated entries here (`append_md`).\n\n## Log\n\n",
    },
}


def templates() -> list[dict[str, str]]:
    return [{"key": k, **v} for k, v in TEMPLATES.items()]


def _title(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise Invalid("title is empty")
    if len(text) > 200:
        raise Invalid("title is longer than 200 characters")
    return text


def _icon(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) > 16:
        raise Invalid("icon is one emoji (16 characters at most)")
    return text or None


def _body(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n")
    if len(text) > MAX_BODY:
        raise Invalid(f"body is longer than {MAX_BODY:,} characters")
    return text


def embeds_in(body: str) -> dict[str, list[str]]:
    tasks: list[str] = []
    docs: list[str] = []
    for kind, ref in EMBED_RE.findall(body):
        bucket = tasks if kind == "task" else docs
        if ref not in bucket:
            bucket.append(ref)
    return {"tasks": tasks, "docs": docs}


def outline(body: str) -> list[dict[str, Any]]:
    """Headings in order (outside fenced code): the page's table of contents."""
    out: list[dict[str, Any]] = []
    fenced = False
    for n, line in enumerate(body.split("\n")):
        if line.startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING_RE.match(line)
        if m and m.group(2):
            out.append({"level": len(m.group(1)), "text": m.group(2), "line": n})
    return out


_FACTS_OPEN_RE = re.compile(r"^\s*```\s*facts\s*$", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")


def parse_facts(body: str) -> list[dict[str, str]]:
    """The page's reference facts: a fenced ```facts block at the very start of the body (blank
    lines before it allowed), one `key: value` per line. Keys and values are trimmed plain text;
    a value may be empty; a line without a key is skipped; duplicate keys keep their order
    (nothing is merged). Anywhere else in the page a ```facts block is ordinary code. The
    block stays in `body_md`, so agents write it with the usual `update`, search already
    finds a value, and the editor keeps it editable as text."""
    lines = body.split("\n")
    n = 0
    while n < len(lines) and not lines[n].strip():
        n += 1
    if n >= len(lines) or not _FACTS_OPEN_RE.match(lines[n]):
        return []
    out: list[dict[str, str]] = []
    for line in lines[n + 1 :]:
        if _FENCE_CLOSE_RE.match(line):
            return out
        key, sep, value = line.partition(":")
        if sep and key.strip():
            out.append({"key": key.strip(), "value": value.strip()})
    return []  # never closed: not a facts block


def body_ref(body: str, version_no: int) -> dict[str, int]:
    """What a page-save event records for a body: the page-history version that holds the text
    and its length, never the text itself (a page is up to MAX_BODY characters and is saved
    every few seconds while someone types; `GET /docs/{id}/versions/{n}` has the text). Saves
    by one author within FOLD_MINUTES fold into one version, which keeps the newest text."""
    return {"version_no": version_no, "chars": len(body)}


def versions_of(session: Session, doc_id: str) -> list[DocVersion]:
    get_doc(session, doc_id)
    return list(
        session.scalars(
            select(DocVersion)
            .where(DocVersion.doc_id == doc_id)
            .order_by(DocVersion.version_no.desc())
        )
    )


def get_version(session: Session, doc_id: str, version_no: int) -> DocVersion:
    v = session.scalar(
        select(DocVersion).where(DocVersion.doc_id == doc_id, DocVersion.version_no == version_no)
    )
    if v is None:
        get_doc(session, doc_id)
        raise NotFound(f"doc {doc_id} has no version {version_no}")
    return v


def version_out(v: DocVersion, *, body: bool = True) -> dict[str, Any]:
    out = {
        "id": v.id,
        "doc_id": v.doc_id,
        "version_no": v.version_no,
        "title": v.title,
        "author_id": v.author_id,
        "created_at": v.created_at,
        "updated_at": v.updated_at,
        "note": v.note,
        "chars": len(v.body_md),
    }
    if body:
        out["body_md"] = v.body_md
    return out


def diff(
    session: Session, doc_id: str, from_version: int, to_version: int | None = None
) -> dict[str, Any]:
    """A unified diff of the body from one version to another (default: the current text),
    plus counts. Agents read what changed in a page without fetching both bodies."""
    doc = get_doc(session, doc_id)
    old = get_version(session, doc_id, from_version)
    if to_version is None:
        new_title, new_body, to_no = doc.title, doc.body_md, doc.version_no
    else:
        new = get_version(session, doc_id, to_version)
        new_title, new_body, to_no = new.title, new.body_md, new.version_no
    text, added, removed = unified(old.body_md, new_body, f"v{old.version_no}", f"v{to_no}")
    return {
        "doc_id": doc_id,
        "from_version": old.version_no,
        "to_version": to_no,
        "title_before": old.title,
        "title_after": new_title,
        "added": added,
        "removed": removed,
        "diff": text,
    }


def unified(before: str, after: str, from_label: str, to_label: str) -> tuple[str, int, int]:
    """A line-by-line unified diff and its added and removed line counts. Page versions and
    file revisions (`doc_compare`) are compared by the same rule."""
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=from_label,
            tofile=to_label,
            lineterm="",
        )
    )
    added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    return "\n".join(lines), added, removed


def _version(session: Session, doc: Doc) -> DocVersion | None:
    return session.scalar(
        select(DocVersion).where(
            DocVersion.doc_id == doc.id, DocVersion.version_no == doc.version_no
        )
    )


def _checkpoint(
    session: Session,
    cx: Change,
    doc: Doc,
    latest: DocVersion | None,
    *,
    new: bool,
    note: str | None = None,
) -> DocVersion:
    """Record the doc's current title and body in page history: fold into `latest` (the newest
    version, read before the change began: a query here would flush uncovered rows) when it is
    the same author's open sitting, else start a version. Returns the row for the caller's
    event to cover."""
    now = utcnow()
    if (
        not new
        and latest is not None
        and latest.author_id == cx.actor.account_id
        and latest.note is None
        and now - latest.updated_at < timedelta(minutes=FOLD_MINUTES)
    ):
        latest.title = doc.title
        latest.body_md = doc.body_md
        latest.updated_at = now
        return latest
    doc.version_no = (latest.version_no if latest else doc.version_no) + (1 if latest else 0)
    v = DocVersion(
        id=new_id(),
        doc_id=doc.id,
        version_no=doc.version_no,
        title=doc.title,
        body_md=doc.body_md,
        author_id=cx.actor.account_id,
        created_at=now,
        updated_at=now,
        note=note,
    )
    session.add(v)  # no query may run before the caller's event covers it
    return v


def replace_section(body: str, heading: str, content: str) -> str:
    """Replace what sits under `heading` (any level, case-insensitive) up to the next heading of
    the same or a higher level. A missing heading is added at the end as `## heading`."""
    lines = body.split("\n")
    want = " ".join(heading.lstrip("#").split()).lower()
    fenced = False
    for n, line in enumerate(lines):
        if line.startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING_RE.match(line)
        if m and " ".join(m.group(2).split()).lower() == want:
            level = len(m.group(1))
            end = len(lines)
            inner = False
            for k in range(n + 1, len(lines)):
                if lines[k].startswith("```"):
                    inner = not inner
                    continue
                mk = None if inner else _HEADING_RE.match(lines[k])
                if mk and len(mk.group(1)) <= level:
                    end = k
                    break
            block = ["", *content.strip("\n").split("\n"), ""] if content.strip() else [""]
            return "\n".join([*lines[: n + 1], *block, *lines[end:]]).rstrip("\n") + "\n"
    base = body.rstrip("\n")
    sep = "\n\n" if base else ""
    return f"{base}{sep}## {' '.join(heading.lstrip('#').split())}\n\n{content.strip()}\n"


def _append(body: str, text: str) -> str:
    base = body.rstrip("\n")
    return f"{base}\n\n{text.strip(chr(10))}\n" if base else f"{text.strip(chr(10))}\n"


# --- writes ---------------------------------------------------------------------------------


def _new_doc(actor: Actor, **cols: Any) -> tuple[Doc, DocVersion]:
    """A doc and its version 1, ids and times set so nothing needs a flush before the event."""
    now = utcnow()
    doc = Doc(
        id=new_id(),
        revision=1,
        version_no=1,
        created_by=actor.account_id,
        updated_by=actor.account_id,
        created_at=now,
        updated_at=now,
        **cols,
    )
    version = DocVersion(
        id=new_id(),
        doc_id=doc.id,
        version_no=1,
        title=doc.title,
        body_md=doc.body_md,
        author_id=actor.account_id,
        created_at=now,
        updated_at=now,
    )
    return doc, version


def _record_mentions(session: Session, cx: Change, doc: Doc, before: str, after: str) -> None:
    """`doc.mentioned` for the handles `after` @mentions that `before` did not."""
    old = set(mentions_in(session, before))
    added = guests.reachable_handles(
        session, cx.actor.account_id, [h for h in mentions_in(session, after) if h not in old]
    )
    if not added:
        return
    line = next((ln for ln in after.splitlines() if f"@{added[0]}" in ln.lower()), "")
    excerpt = " ".join(line.split())[:200]
    cx.record(
        "doc.mentioned",
        subject_id=doc.id,
        after={
            "doc_id": doc.id,
            "title": doc.title,
            "mentions": added,
            "excerpt": excerpt,
        },
    )
    from slate.service import docwake  # noqa: PLC0415 (docwake reads this module)

    docwake.on_mentioned(cx, session, doc, added, excerpt=excerpt)


def _insert(
    session: Session, cx: Change, doc: Doc, version: DocVersion, covers: Sequence[Any]
) -> Doc:
    cx.create(doc, "doc.created", covers=[version, *covers])
    session.add(version)
    return doc


def create_doc(
    session: Session,
    actor: Actor,
    *,
    title: str | None = None,
    body_md: str | None = None,
    icon: str | None = None,
    parent_id: str | None = None,
    list_id: str | None = None,
    section_id: str | None = None,
    template: str | None = None,
    before_id: str | None = None,
    kind: str = "page",
    tags: Sequence[str] | None = None,
) -> Doc:
    """One page. Under `parent_id` it is a subpage (no location of its own); otherwise it sits
    in `list_id`, `section_id` or the workspace. `template` fills title, icon and body that
    the caller did not give. It goes last among its siblings, or before `before_id`.
    `kind = file` makes a file doc, empty until `add_revision`; its body is the description."""
    from slate.service.doc_files import created_from_name
    from slate.service.doc_listing import _set_tags
    from slate.service.doc_shape import sync_links

    if kind not in KINDS:
        raise Invalid(f"kind must be one of {', '.join(KINDS)}")
    if kind != "page" and template not in (None, "blank"):
        raise Invalid(f"a {kind} takes no template")
    tpl = TEMPLATES.get(template or "blank")
    if tpl is None:
        raise Invalid(f"unknown template '{template}'; one of {', '.join(TEMPLATES)}")
    docs = all_docs(session)
    by_id = {d.id: d for d in docs}
    if parent_id is not None:
        parent = by_id.get(parent_id)
        if parent is None:
            raise Invalid(f"parent doc {parent_id} does not exist")
        if list_id is not None or section_id is not None:
            raise Invalid("a subpage lives where its top page lives; give parent_id or a place")
        if depth(by_id, parent) >= MAX_DEPTH:
            raise Invalid(f"pages nest {MAX_DEPTH} deep at most")
        sec, lst = None, None
    else:
        sec, lst = place(session, list_id, section_id)
    group = siblings(docs, parent_id, sec, lst)
    doc, version = _new_doc(
        actor,
        kind=kind,
        title=_title(title if title is not None else tpl["title"]),
        icon=_icon(icon if icon is not None else tpl["icon"]),
        body_md=_body(body_md if body_md is not None else tpl["body_md"]),
        parent_id=parent_id,
        section_id=sec,
        list_id=lst,
        position=len(group),
    )
    if kind == "file":
        dated = created_from_name(doc.title)
        if dated is not None:
            doc.created_at = dated  # the date in the name is the file's date
    at = next((n for n, d in enumerate(group) if d.id == before_id), len(group))
    with apply_change(session, actor) as cx:
        moved = renumber([*group[:at], doc, *group[at:]])
        _insert(session, cx, doc, version, [m for m in moved if m is not doc])
        _record_mentions(session, cx, doc, "", doc.body_md)
        sync_links(cx, doc)
        if tags:
            session.flush()  # the doc row exists before its tags point at it
            _set_tags(cx, doc, tags)
    return doc


UPDATABLE = {
    "title",
    "icon",
    "body_md",
    "append_md",
    "replace_section",
    "base_revision",
    "new_version",
    "note",
    "tags",
}


def update_doc(session: Session, actor: Actor, doc_id: str, changes: Mapping[str, Any]) -> Doc:
    """Change a page's title, icon or body.

    `body_md` replaces the body; `replace_section` `{heading, body_md}` rewrites what sits under
    one heading (adding it when missing); `append_md` adds a paragraph at the end.
    `base_revision`: the revision the caller edited; a mismatch is a 409 and nothing changes.
    `new_version: true` (or a `note`) closes the current page-history version, named by `note`.
    """
    from slate.service.doc_listing import _set_tags
    from slate.service.doc_shape import sync_links

    unknown = set(changes) - UPDATABLE
    if unknown:
        raise Invalid(f"cannot update {sorted(unknown)}")
    get_doc(session, doc_id)
    spec = changes.get("replace_section")
    heading = str(spec.get("heading") or "").strip() if spec else ""
    if spec and not heading:
        raise Invalid("replace_section needs a heading")
    note = str(changes.get("note") or "").strip()[:200] or None
    seal = bool(changes.get("new_version")) or note is not None
    tag_names = changes.get("tags")
    from slate.service import docs as docs_svc

    with docs_svc.apply_change(session, actor) as cx:
        # The check and the new body are worked out from the doc as it stands under the write
        # lock, so a save that committed since this request began is never overwritten unseen:
        # a stale `base_revision` is a 409 and two `append_md` calls both land.
        doc = fresh_doc(session, doc_id)
        latest = _version(session, doc)
        base = changes.get("base_revision")
        if base is not None and base != doc.revision:
            raise Conflict(
                f"doc changed since revision {base} (now {doc.revision}); reload it and reapply "
                "your edit"
            )
        wanted: dict[str, Any] = {}
        if changes.get("title") is not None:
            wanted["title"] = _title(changes["title"])
        if "icon" in changes:
            wanted["icon"] = _icon(changes["icon"])
        body = doc.body_md
        touched = False
        if changes.get("body_md") is not None:
            body, touched = _body(changes["body_md"]), True
        if spec:
            body, touched = replace_section(body, heading, str(spec.get("body_md") or "")), True
        if changes.get("append_md"):
            body, touched = _append(body, str(changes["append_md"])), True
        if touched:
            wanted["body_md"] = _body(body)
        diffs = {k: v for k, v in wanted.items() if getattr(doc, k) != v}
        if not diffs and not seal:
            if tag_names is not None:
                _set_tags(cx, doc, list(tag_names))
            return doc
        if tag_names is not None:
            _set_tags(cx, doc, list(tag_names))
        befores = {k: getattr(doc, k) for k in diffs}
        before_version = doc.version_no
        for k, v in diffs.items():
            setattr(doc, k, v)
        covers: list[Any] = [doc]
        if "title" in diffs or "body_md" in diffs or seal:
            if "title" in diffs or "body_md" in diffs:
                doc.revision += 1
            v = _checkpoint(session, cx, doc, latest, new=False)
            if seal:
                # Close this sitting: the next save starts a fresh version.
                v.note = note or v.note or "Saved version"
            covers.append(v)
        doc.updated_by = actor.account_id
        doc.updated_at = utcnow()
        for k in diffs:
            body = k == "body_md"
            cx.record(
                "doc.updated",
                subject_id=doc.id,
                field=k,
                before=body_ref(befores[k], before_version) if body else befores[k],
                after=body_ref(diffs[k], doc.version_no) if body else diffs[k],
                covers=covers,
            )
        if "body_md" in diffs:
            _record_mentions(session, cx, doc, befores["body_md"], diffs["body_md"])
        sync_links(cx, doc)
        if not diffs:
            cx.record(
                "doc.updated",
                subject_id=doc.id,
                field="version_no",
                after={"version_no": doc.version_no, "note": covers[-1].note},
                covers=covers,
            )
    return doc


def restore_version(session: Session, actor: Actor, doc_id: str, version_no: int) -> Doc:
    """Put an older version's title and body back. Nothing is lost: the restore is itself a
    new version, and the text it replaced stays in the one before."""
    from slate.service.doc_shape import sync_links

    doc = get_doc(session, doc_id)
    old = get_version(session, doc_id, version_no)
    latest = _version(session, doc)
    before = {"title": doc.title, **body_ref(doc.body_md, doc.version_no)}
    with apply_change(session, actor) as cx:
        doc.title, doc.body_md = old.title, old.body_md
        doc.revision += 1
        doc.updated_by = actor.account_id
        doc.updated_at = utcnow()
        v = _checkpoint(session, cx, doc, latest, new=True, note=f"Restored version {version_no}")
        cx.record(
            "doc.version_restored",
            subject_id=doc.id,
            before=before,
            after={
                "title": doc.title,
                **body_ref(doc.body_md, doc.version_no),
                "restored_from": version_no,
            },
            covers=[doc, v],
        )
        sync_links(cx, doc)
    return doc


def duplicate_doc(
    session: Session,
    actor: Actor,
    doc_id: str,
    *,
    title: str | None = None,
    subpages: bool = True,
) -> Doc:
    """Copy a page right after the original, with its subpages unless `subpages` is false.
    Each copy starts its own history at version 1. Only the copied docs are read, and a copied
    file keeps its revision's extracted text and tables (the bytes are the same), so nothing is
    parsed while the write lock is held."""
    from slate.service.doc_files import _revision_row, current_revisions, revision_out
    from slate.service.doc_shape import sync_links

    src = get_doc(session, doc_id)
    docs = all_docs(session)
    group = siblings(docs, src.parent_id, src.section_id, src.list_id)
    at = next(n for n, d in enumerate(group) if d.id == src.id) + 1

    # The docs to copy: the source and, with `subpages`, its live subtree (each doc once).
    kids_of: dict[str, list[Doc]] = {}
    for d in docs:
        if d.parent_id and d.archived_at is None:
            kids_of.setdefault(d.parent_id, []).append(d)
    copied: dict[str, Doc] = {src.id: src}
    stack = [src.id] if subpages else []
    while stack:
        for child in kids_of.get(stack.pop(), []):
            if child.id not in copied:
                copied[child.id] = child
                stack.append(child.id)
    full = {d_id: session.get(Doc, d_id) for d_id in copied}
    for d in full.values():
        d.body_md  # noqa: B018 - load bodies before the change (no queries inside it)

    current = current_revisions(session, list(copied.values()))
    if current:  # bytes, text and tables of the copied revisions, in one query, before the change
        session.scalars(
            select(DocFile)
            .options(undefer(DocFile.data), undefer(DocFile.text), undefer(DocFile.tables_json))
            .where(DocFile.id.in_([f.id for f in current.values()]))
        ).all()

    def clone(s: Doc, parent_id: str | None, name: str, position: int) -> tuple[Doc, DocVersion]:
        return _new_doc(
            actor,
            kind=s.kind,
            title=_title(name),
            icon=s.icon,
            body_md=s.body_md,
            parent_id=parent_id,
            section_id=s.section_id if parent_id is None else None,
            list_id=s.list_id if parent_id is None else None,
            position=position,
        )

    copy, version = clone(src, src.parent_id, title or f"{src.title} (copy)", at)

    def copy_revision(cx: Change, from_id: str, to: Doc) -> list[DocFile]:
        f = current.get(from_id)
        if f is None:
            return []
        rev = _revision_row(
            cx.actor,
            to.id,
            f.filename,
            f.data,
            f.content_type,
            1,
            f"Copied from {src.title}"[:200],
            extracted=(f.text, f.tables_json),
        )
        session.add(rev)
        return [rev]

    with apply_change(session, actor) as cx:
        moved = renumber([*group[:at], copy, *group[at:]])
        _insert(session, cx, copy, version, [m for m in moved if m is not copy])
        for rev in copy_revision(cx, src.id, copy):
            cx.record(
                "doc.revision_added", subject_id=copy.id, after=revision_out(rev), covers=[rev]
            )
        if subpages:
            stack = [(src.id, copy.id)]
            while stack:
                from_id, to_id = stack.pop()
                kids = tree_order(kids_of.get(from_id, []))
                for n, child in enumerate(kids):
                    dup, dv = clone(full[child.id], to_id, child.title, n)
                    _insert(session, cx, dup, dv, [])
                    for rev in copy_revision(cx, child.id, dup):
                        cx.record(
                            "doc.revision_added",
                            subject_id=dup.id,
                            after=revision_out(rev),
                            covers=[rev],
                        )
                    stack.append((child.id, dup.id))
        sync_links(cx, copy)
        for made in list(session.new):
            if isinstance(made, Doc):
                sync_links(cx, made)
    return copy


def create_task(
    session: Session,
    actor: Actor,
    doc_id: str,
    *,
    title: str,
    list_id: str | None = None,
    replace: str | None = None,
    occurrence: int = 0,
    base_revision: int | None = None,
    **fields: Any,
) -> tuple[Item, Doc]:
    """File one task from a doc (ClickUp's "create task from selection"), in one change.

    The task goes to `list_id`, else the doc's list; its description ends with a link back to
    the page. With `replace`, that text (its `occurrence`-th appearance, counting from 0)
    becomes the task's `[[task:<id>]]` embed; without it the embed is added at the end.
    Exactly one item is created (AGENTS.md rule 1).
    """
    from slate.service import items as items_svc
    from slate.service.doc_shape import sync_links

    doc = get_doc(session, doc_id)
    target = list_id or root_of(session, doc).list_id
    if target is None:
        raise Invalid("list_id is required: this doc is not in a list")
    link = f"From doc [[doc:{doc.id}]]"
    desc = str(fields.pop("body_md", "") or "").strip()
    from slate.service import docs as docs_svc

    with docs_svc.apply_change(session, actor) as cx:
        # Checked against the doc as it stands under the write lock (see `update_doc`).
        doc = fresh_doc(session, doc_id)
        latest = _version(session, doc)
        if base_revision is not None and base_revision != doc.revision:
            raise Conflict(
                f"doc changed since revision {base_revision} (now {doc.revision}); reload it first"
            )
        body = doc.body_md
        at = -1
        if replace:
            for _ in range(occurrence + 1):
                at = body.find(replace, at + 1)
                if at < 0:
                    raise Invalid("the selected text is not in the doc (it may have changed)")
        item = items_svc.create_item(
            session,
            actor,
            list_id=target,
            title=title,
            body_md=f"{desc}\n\n{link}" if desc else link,
            **fields,
        )
        embed = f"[[task:{item.id}]]"
        new_body = (
            body[:at] + embed + body[at + len(replace) :] if replace else _append(body, embed)
        )
        before_version = doc.version_no
        doc.body_md = _body(new_body)
        doc.revision += 1
        doc.updated_by = actor.account_id
        doc.updated_at = utcnow()
        v = _checkpoint(session, cx, doc, latest, new=False)
        cx.record(
            "doc.updated",
            subject_id=doc.id,
            field="body_md",
            before=body_ref(body, before_version),
            after=body_ref(doc.body_md, doc.version_no),
            covers=[doc, v],
        )
        sync_links(cx, doc)
    return item, doc
