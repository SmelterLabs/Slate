# ClickUp importer

`slate import clickup` moves a ClickUp workspace into Slate once, on import day, and can be re-run safely while ClickUp is kept read-only.

## Modules

| Path | What it is |
|---|---|
| `src/slate/importers/clickup_api.py` | Reading ClickUp. `HttpSource` (live API v2, token bucket 100 req/min, retries 429/5xx, optional `record_dir`), `FixtureSource` (a directory of recorded responses), `fixture_name()` (request -> file name), and the walk: `fetch_lists`, `list_statuses`, `fetch_tasks`, `fetch_comments`. |
| `src/slate/importers/collapse.py` | The recurrence-collapse heuristic: `family_key`, `strip_dates`, `title_template`, `infer_rrule`, `next_after`, `describe` (RRULE in plain words). Pure functions, no database. |
| `src/slate/importers/clickup.py` | Mapping and the three steps: `build_plan()` (decides everything, writes nothing), `render_report()` (the dry-run text and the apply receipt), `apply_plan()` (writes through the service layer). |
| `src/slate/cli.py` | `slate import clickup` (`import_clickup()`). |
| `tests/test_import_clickup.py`, `tests/fixtures/clickup/` | Tests and the recorded-shape fixtures (regenerate with `uv run python tests/fixtures/clickup/generate.py`). |

The importer lives outside `slate/service/` and **never writes the database itself**: every write is a public service call (`create_section`, `create_list`, `set_statuses`, `create_item`, `update_item`, `add_comment`, `archive_item`, `create_recurrence`), so rule 3 holds and the walk test needs no new entries. The actor is the `--as` account with `source=system` and `session_ref=import:clickup`.

## Command

```
slate import clickup --team <workspace id> [--token-env CLICKUP_TOKEN] [--dry-run | --apply]
                     [--mapping FILE] [--db PATH] [--fixture DIR] [--record DIR] [--as HANDLE]
                     [--now ISO]
```

- Dry run is the default and never opens the database. `--mapping FILE` says where things go (below). `--apply` writes to `--db` (default `SLATE_DATABASE_PATH`); the file must already be migrated (`slate migrate`) and the `--as` account and every mapped assignee account must exist, else exit 2 before anything is written.
- `--fixture DIR` reads recorded responses instead of calling ClickUp (no token needed). `--record DIR` saves every live response under its fixture name, so a recorded dry run can be replayed by `--apply --fixture DIR` and applies exactly what was reviewed.
- `--now` pretends it is that time (the 14-day window and "overdue" are computed from it). For tests and rehearsals.
- Exit 0 on success, 2 on a usage, ClickUp or service error (message on stderr).

## Reading ClickUp

Written against the ClickUp API v2 pages `getfilteredteamtasks`, `gettaskcomments`, `getspaces`, `getfolders`, `getfolderlesslists` and `rate-limits` on developer.clickup.com.

1. `GET /team/{team}/space` → per space `GET /space/{id}/folder` → `GET /folder/{id}/list`, and `GET /space/{id}/list` (folderless).
2. For each **mapped** list only: statuses from the list object; if absent `GET /list/{id}`; if still absent the space's statuses.
3. Tasks: `GET /team/{team}/task?page=N&include_closed=true&subtasks=true&include_markdown_description=true&list_ids[]=<list>` from page 0 until `last_page: true` (or, if the field is missing, a page shorter than 100). Deduplicated by task id.
4. Comments for every task that will be imported (not for dropped ones): `GET /task/{id}/comment`, 25 newest first; older pages with `start` + `start_id` of the oldest so far. Threaded replies are not fetched (the report counts them).

Auth header is the raw personal token (`Authorization: pk_...`). The bucket allows a burst of 100 then one request every 0.6 s; a 429 waits until `X-RateLimit-Reset` (max 65 s), 5xx backs off exponentially, five retries. As a guide, 300 tasks take about 310 requests, roughly 3–4 minutes.

**Doc ambiguities handled both ways:** the filtered-tasks schema shows `priority` as an integer while Get Task shows an object `{id, priority, color}` (both accepted: `1..4` or the name); `last_page` is not in the documented schema (the short-page rule is the fallback); folder objects' `lists` are documented as strings, so lists are always fetched per folder.

## Mapping

Where things go is data, not code: a JSON mapping file passed with `--mapping FILE` (the format
is in the `slate.importers.clickup` module docstring; every key is optional).

- **Without a mapping** every ClickUp list is imported under its own name into a section named
  after its ClickUp space, nobody is assigned and nothing is mirrored.
- `lists` maps ClickUp list names (any case) and `list_ids` maps list ids (an id wins) to a
  Slate `[section, list]`. When either is given, lists matching neither are not imported and
  are listed under "Unmapped" with their task count, unless `map_unlisted` is true.
- `audiences` sets a section's audience (otherwise guessed from its name); `source_tagged`
  names destinations that merge several ClickUp lists and so tag each task with the list it
  came from.
- `mirror` moves every task whose name starts with a prefix (say `[Shared]`) into one shared list,
  tagged, whatever list it lived in.
- `collapse` (default true) turns fake-recurring copies into rules (below); `include_closed`
  (default true) imports closed tasks.

A destination list is created only if something goes into it.

- **Statuses:** each Slate list gets the union of its source lists' statuses plus any status seen on its tasks, unique by name (first wins), done-kind ones moved last. Kind: ClickUp type `closed`/`done` → `done`; a name in `clickup.ACTIVE_NAMES` (`in progress`, `shipping`) → `active`; else `open`. Default status = the first one. On a re-run, missing statuses are appended with `set_statuses()`; existing ones are never removed.
- **Fields:** name → title; `markdown_description` (else `description`, `text_content`) → `body_md` as-is, plus a footer `Imported from ClickUp <url> on <YYYY-MM-DD>`; `external_ref = clickup:<id>`; priority object/int → urgent/high/normal/low or null; tags → lowercase Slate tags.
- **Dates:** `due_date`/`start_date` are ms epoch → UTC. When the task says `due_date_time: false` (a date with no time), the date is read in the workspace timezone and set to 17:00 workspace time, Slate's date-only rule; otherwise the instant is kept.
- **Assignees:** the mapping's `assignees` maps ClickUp user ids to Slate account handles (`{"1234567": "alex"}`). Anyone else is dropped and listed under "Unassigned".
- **Subtasks:** `parent` → `parent_id` under the parent's Slate item (and in the parent's list, noted if that differs from where the subtask would have gone). A subtask of a subtask hangs off the top-level parent with a note in its body. A subtask whose parent is not imported becomes a top-level item with a note.
- **Comments:** added as comments by the acting account; the body starts `**ClickUp comment** by <user> · <YYYY-MM-DD HH:MM ZONE> <!-- clickup-comment:<id> -->` (workspace time and its zone abbreviation) because `add_comment()` cannot backdate `created_at`. The marker makes re-runs skip comments already imported.
- **Closed tasks** (type `closed` or `done`) are imported in their (done-kind) status, then archived, so history survives off the board. Archive runs after every item exists; archiving a parent also archives its subtasks (service rule).

## Recurrence collapse

Tasks filed as dated copies (dozens of "Water the plants — <date>" tasks, say) become one rule each.

1. **Candidates:** open, top-level tasks with a due date. Subtasks never form families (a numbered "Step 1–10" series would otherwise group).
2. **Grouping:** by destination list and `family_key(title)`: lowercase, date fragments removed ("Nov 11", "Wed Nov 11", "November 11th, 2026", "2026-11-11", "11/11/26"), recurrence markers removed ("(monthly)", "(weekly recurring)", "(recurring)"...), digits and punctuation removed. Weekday words are kept on purpose, so "Monday report" and "Friday report" stay apart.
3. **At least 3 members**, else nothing happens.
4. **Inference** from the gaps between the sorted local due dates: all 7 → `WEEKLY;BYDAY=<weekday>`; all 14 → `WEEKLY;INTERVAL=2;BYDAY=<weekday>`; all 28–31 → `MONTHLY;BYMONTHDAY=<commonest day>`; 89–92 → `MONTHLY;INTERVAL=3`; 181–184 → `MONTHLY;INTERVAL=6`; 365–366 → `YEARLY;BYMONTH;BYMONTHDAY`. Then the check that makes it safe: the RRULE, started on the first member date, must regenerate **exactly** the member dates up to the last. A drifting day of month, a missing week or two copies on one day fail it and the family is reported as irregular and imported as separate tasks (four tasks with the same name and gaps of 1, 28 and 7 days, for example).
5. **Window:** members due on or before today + 14 days (workspace time) are imported as ordinary items (the past-due ones are simply overdue); members after it are **dropped** and listed.
6. **The rule** (`create_recurrence`, default lead 14 days, `spawn_always`): `title_template` = the commonest date-stripped title, plus ` — {date}` if the titles were dated ("Water the plants — {date}" renders "Water the plants — Wed Nov 11"); body = the latest member description; priority = the commonest; tags and assignees = the union. **`dtstart` = the first dropped member's date** (or, if none was dropped, the rule's next date after the last member). So the scheduler makes exactly the dates the importer left out and never duplicates an imported copy: imported in-window copies are plain items with `external_ref`, not rule instances (no recurrence badge on them; they are the last ClickUp copies).

**Rejected:** linking the in-window copies to the rule (`recurrence_id` + `occurrence_at`). It needs the private `items._new_item/_insert` helpers from outside the service package, and the dtstart-after-the-window anchor gives the same board with only public calls.

## Idempotency

- Sections by slug, lists by section + slug: reused, never duplicated.
- Items by `external_ref`: an existing one gets `update_item()` with every mapped field (title, body, status, priority, dates, tags, assignees). Unchanged values record nothing; the body footer keeps the **original** import date, so an unchanged task writes no event. A re-run therefore overwrites edits made to imported fields in Slate since the last run (intended for the read-only period; do not re-run after people start editing in Slate unless that is wanted). Items are not moved between lists and subtask parents are not changed on update. Already-archived items stay archived; a reopened ClickUp task is not unarchived.
- Comments by the `clickup-comment:<id>` marker.
- Rules by list + `family_key(title_template)`; an existing (even ended) rule is never re-created. Because the window moves with the calendar, a later re-run would see copies that are now inside the window but which the existing rule already makes: any family copy due on or after the existing rule's `dtstart` that is not yet in Slate is skipped (`owned_by_rule` in the receipt).
- Each service call commits on its own, so a crash mid-apply leaves a partial import; re-running finishes it.
- Tested: a second apply on the same data writes zero rows of any kind (`test_second_apply_writes_nothing`).

## Recording fixtures

Live, on import day (needs the token; writes nothing to Slate):

```
slate import clickup --team <workspace id> --token-env CLICKUP_TOKEN --dry-run --record DIR
```

`DIR` then holds one JSON file per request, named by `fixture_name()`, e.g. `team_<team>_space.json`, `space_<id>_folder.json`, `folder_<id>_list.json`, `space_<id>_list.json`, `list_<id>.json`, `team_<team>_task__list_ids-<list>__page-<n>.json`, `task_<id>_comment.json` (`__start_id-<id>` for older pages). The token is a request header and is never written. Replay with `--fixture DIR`. A missing file is an error, never an empty answer.

The committed test fixtures are generated (`tests/fixtures/clickup/generate.py`): a synthetic workspace with several mapped lists plus an unmapped one, recurrence families of every inferred shape (biweekly, monthly, quarterly, six-monthly, weekly) and an irregular one, a two-level subtask chain, closed tasks in several lists, an unmapped assignee, a list whose statuses come from `GET /list/{id}`, a two-page task list, and comments with a threaded reply. Tests freeze "now" at 2026-10-23 15:00 UTC (10:00 in `America/Chicago`, their sample zone). To test against a real shape, record a dry run and copy the files in (scrub anything private first).

## Gotchas

- The import runs as a separate process from the server, so its events do not reach open boards over SSE until they reconnect (catch-up replays them from the table). Stop the service during `--apply` for that reason and to keep event ids in one process's order.
- The window and "overdue" depend on the day you run. Apply the same day as the reviewed dry run, or record and review again.
- Comment timestamps are shown in the comment body only; `comments.created_at` is the import time.
