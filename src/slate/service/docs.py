"""Docs and files (the product's documents): the public surface of the docs modules.

The work lives in modules split by what they do, and this module re-exports their public names
so `from slate.service import docs` keeps working:

- `doc_tree`: the page tree and moving, archiving and deleting pages.
- `doc_pages`: page text, templates, page history and filing a task from a page.
- `doc_files`: files uploaded into a page, file-doc revisions and the date in a file's name.
- `doc_listing`: search and filters, tags, pins and drawers.
- `doc_shape`: the full `doc_out` payload and the `doc_links` rows that back it.

Rules shared by all of them:

- A doc is a page (Markdown), a file (uploaded bytes) or a folder (a container in a drawer).
  All three sit in one tree. Only a top doc carries a location (`list_id`, and `section_id` for
  quick filtering; neither = the workspace); docs under it inherit its location.
- Filing never creates children (AGENTS.md rule 1): `create_doc` makes exactly one doc.
- A page's links to tasks and other pages (`[[task:<id>]]`, `[[doc:<id>]]`) are stored in
  `doc_links` and kept current by the change that changes the body.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from slate.models import Doc, Item
from slate.service.core import Actor, apply_change
from slate.service.doc_files import (  # noqa: F401  (re-exported public surface)
    MAX_FILE_BYTES,
    MAX_REVISION_BYTES,
    _revision_row,
    _store_revision,
    add_file,
    add_revision,
    created_from_name,
    current_revisions,
    date_in_name,
    file_out,
    get_file,
    get_revision,
    pin_revision,
    remove_file,
    restore_revision,
    revision_out,
    revisions_of,
    unpin_revision,
)
from slate.service.doc_listing import (  # noqa: F401
    doc_summary,
    drawer_listing,
    in_scope,
    list_docs,
    pin_to_top,
    pinned_first,
    snippet,
    summaries,
    tags_of,
    to_drawer,
    unpin_from_top,
)
from slate.service.doc_pages import (  # noqa: F401
    EMBED_RE,
    FOLD_MINUTES,
    MAX_BODY,
    body_ref,
    create_doc,
    create_task,
    diff,
    duplicate_doc,
    embeds_in,
    get_version,
    outline,
    parse_facts,
    replace_section,
    restore_version,
    templates,
    unified,
    update_doc,
    version_out,
    versions_of,
)
from slate.service.doc_shape import doc_out  # noqa: F401
from slate.service.doc_tree import (  # noqa: F401
    MAX_DEPTH,
    _descendants,
    archive_doc,
    delete_doc,
    fresh_doc,
    get_doc,
    move_doc,
    root_of,
    unarchive_doc,
)


def promote_attachment(
    session: Session,
    actor: Actor,
    attachment_id: str,
    *,
    title: str | None = None,
    parent_id: str | None = None,
    list_id: str | None = None,
    section_id: str | None = None,
    note: str | None = None,
) -> Doc:
    """Move a task's attachment into the library: a new file doc with the bytes as revision 1,
    the attachment removed from the task, and a comment on the task saying where it went."""
    from slate.service import attachments as attachments_svc
    from slate.service.comments import add_comment
    from slate.service.doc_files import _note

    att, data = attachments_svc.read_bytes(session, attachment_id)
    item = session.get(Item, att.item_id)
    text = _note(note or (f"From task {item.title}" if item else "From a task"))
    # The file is read before the write lock; the four steps are then one change, so a step
    # the caller may not take (removing the attachment needs edit on the task's list) leaves
    # nothing behind.
    rev = _revision_row(actor, "", att.filename, data, att.content_type, 1, text)
    with apply_change(session, actor):
        doc = create_doc(
            session,
            actor,
            kind="file",
            title=title or att.filename,
            parent_id=parent_id,
            list_id=list_id,
            section_id=section_id if parent_id is None and list_id is None else None,
        )
        rev.doc_id = doc.id
        _store_revision(session, actor, doc, rev)
        attachments_svc.remove_attachment(session, actor, att.id)
        add_comment(
            session,
            actor,
            att.item_id,
            body_md=f"Moved **{att.filename}** to Docs & Files: [[doc:{doc.id}]]",
            keep_open=True,
        )
    return get_doc(session, doc.id)
