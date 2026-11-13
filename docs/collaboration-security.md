# Collaboration security: digest snapshots and DOCX extraction

## Current-access digest contract

`account.digest` is account-subject history containing nested task snapshots; ownership of
that account event is not authorization for its nested tasks. Scheduler digest generation
computes recipient access before reading Today/assigned tasks. Retained assignments
cannot recover revoked project access. Scoped request sessions keep their token
restrictions.

For restricted readers, `access.digest_snapshot` checks every snapshot ID against the
current Item/list mapping with explicit SQL (not an identity-map lookup). Missing, deleted,
moved-out-of-scope and unauthorized references are excluded. It returns only the known
snapshot fields and recomputes each group's count from its retained, deduplicated entries.
Historical digests store at most ten snapshots per group, so these safe counts are counts of
retained snapshot entries, **not reconstructed full historical totals**. An authorized partial
digest remains useful; an empty pending push is canceled. Privileged unrestricted readers
see the original history.

Projection is read-only: stored events and actors are never edited and no event is written.
The shared serializer covers Inbox, check-in and REST/SSE replay; Audit JSON/CSV uses the
same snapshot helper. Live SSE projects a new recipient-specific dictionary, never mutating
the bus payload. Push reprojects on delivery/retry after fresh recipient authorization.
Queued webhook bodies are immutable: boolean-only webhook gates suppress an unsafe or
now-empty digest instead of sending the original bytes. Export contains documents
and tasks, not digest events; its current-access gate is checked with the other surfaces in
`tests/test_collaboration_security_final.py`.

The other account-subject notification/bookkeeping events
(`inbox.read/cleared`, `channel.read`, account preferences) carry cursors, IDs or preferences
rather than nested task-title/count snapshots; push receipts are bookkeeping rows
(`push_deliveries`, no event), holding only ids and retry state.
Task reminders are item-scoped and have their own exact-read contract. Shared-chat
task-reference push rendering checks the referenced task independently.
This is not a general redactor for user-written text, and notifications already delivered
cannot be recalled.

## Extraction cap

One setting bounds every parser that reads inside an upload: `SLATE_DOC_EXTRACT_MAX_BYTES`
(`settings.doc_extract_max_bytes`, default 64 MiB), counted in **inflated** bytes: a DOCX or
XLSX part after unzipping, a PDF's content streams after FlateDecode. A small workspace uploads few
spreadsheets and PDFs and should meet no friction when it does, so the cap never blocks an upload:
over it, the original is stored and viewable (the board renders xlsx/docx/pdf client-side) and
the revision is simply not indexed (`has_text` false, the same contract as an unreadable
file). Revisions say so (`has_text`, "not searchable" in the history panel) and compare
answers `kind: none` with a `message` naming the unindexed revision instead of parsing.

- **XLSX** (`_check_archive`, before `openpyxl.load_workbook`, which loads
  `xl/sharedStrings.xml` whole even read-only; the row/column limits apply only after that): every
  zip member's declared `file_size` and their total must fit the cap; a member over 4 MiB that
  inflates 200x or more from its `compress_size` is a bomb, not a workbook (real OOXML deflates
  10-50x); then each member is inflated in 64 KiB chunks under the same running budget, because
  a central directory can lie. A hand-built 1 MB workbook whose shared strings inflate to
  300 MB (146 s, +1.5 GB RSS when opened) is refused from the headers in milliseconds, under
  4 MB traced. Sheets are capped at `MAX_TABLE_SHEETS` 64.
- **DOCX** uses a streaming reader; its part limit is the same setting.
- **PDF** (`_pdf_text`): before a page is opened, its `/Contents` streams and form XObjects are
  measured with `zlib.decompressobj().decompress(raw, remaining + 1)`, so a stream past the
  budget costs at most the budget to find out; pages past it are not opened, and the `MAX_TEXT`
  character cap still applies. Images and nested forms inside forms are not measured (pypdf's
  text extraction does not inflate images).
- **Compare never parses on read.** Upload stores a CSV/TSV/XLSX's tables as JSON
  (`doc_files.tables_json`) beside the text; `compare` reads only the
  two stored columns. A revision with `tables_json` or `text` NULL (over the cap or unreadable)
  compares as "not indexed"; uploading the file again as a new revision indexes it.
- **Attachments inside the share work for guests** (`access.check_object`): the blob row has
  no `item_id`, so it is checked through its attachment's task instead of hitting the guest
  catch-all; outside the share an upload is still refused (`tests/test_guest_scope.py`).
- **Chat files follow their message** (`chat_files.read_bytes`): a file is read through the
  caller's read filter on its message, so a guest reads the files of the messages it can read
  and no others (a members-only message's file is a 404), and an unsent upload is its
  uploader's alone (`tests/test_chat_files.py`).
- **Upload bodies are counted, not trusted** (`api/uploads.read_body`): attachments, chat files
  and both doc upload routes stream `request.stream()` with a running count and stop at the cap with a 422,
  so a chunked request (no `Content-Length`) cannot skip the pre-check and be buffered whole; a
  non-numeric length is a 422, not a 500.

Tests: `tests/test_collaboration_security_final.py` (xlsx bomb refused before openpyxl, in under
a second and under 4 MB traced; absurd ratio; a small workbook still indexes; PDF budget),
`tests/test_doc_compare.py` (compare reads stored tables with every parser monkeypatched to
fail; the not-indexed message; an over-cap upload succeeds, opens, is unindexed),
`tests/test_uploads.py` (chunked bodies through httpx).

## DOCX resource contract

`doc_compare._docx_text` reads only `word/document.xml`, in 64 KiB chunks, under the
extraction cap above. A custom XML target builds no tree and never rescans descendants.
Text and paragraph separators consume a running `MAX_TEXT` budget **before** StringIO writes;
there is no full-output join followed by truncation. Parsing continues after the output cap
so a malformed tail cannot bypass rejection. Nested paragraphs/text elements and nesting
over 128 elements are rejected; DTD/entity declarations are rejected. Normal paragraph
order, table paragraphs and split runs are retained. Invalid uploads stay unindexed via the
`extract_text` failure contract, without affecting the uploaded original.

The nested-paragraph test in `tests/test_collaboration_security_final.py` uses a
1,000-deep / 10,000-character input and requires rejection with
less than 2 MB traced peak allocation. A valid 1 MB XML stream with a 1,000-character output
budget must remain below 2.5 MB traced peak. Additional probes cover depth, nested text,
paragraph separators, and invalid tails after the text budget is exhausted.

## Test coverage

The tests use synthetic in-memory databases and fake push/webhook senders.
