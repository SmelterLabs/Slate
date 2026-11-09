"""Regenerate the recorded-response fixtures in this directory.

    uv run python tests/fixtures/clickup/generate.py

The files mirror ClickUp API v2 response shapes (developer.clickup.com/reference) for a small
synthetic workspace of ~66 tasks, plus `mapping.json`, the `--mapping` the tests import with.
They are named by
`slate.importers.clickup_api.fixture_name`, exactly as `slate import clickup --record DIR` names a
live recording, so the tests read them with `--fixture`. Dates are relative to the tests' frozen
"now", 2026-10-23 10:00 America/Chicago.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

from slate.importers.clickup_api import fixture_name  # noqa: E402

TZ = ZoneInfo("America/Chicago")
TEAM = "90130000000"
ALEX = {"id": 10000001, "username": "Alex", "color": "#7b68ee", "initials": "A",
         "email": "alex@example.com", "profilePicture": None}  # fmt: skip
GUEST = {"id": 61234567, "username": "Guest", "color": "#e5484d", "initials": "G",
         "email": "guest@example.com", "profilePicture": None}  # fmt: skip
CREATED = "1760000000000"


def ms(d: date, at: time = time(4, 0)) -> str:
    """ClickUp stores a date-only due date at 04:00 local; a timed one at its time."""
    return str(int(datetime.combine(d, at, tzinfo=TZ).timestamp() * 1000))


def status(name: str, kind: str, i: int, color: str = "#87909e") -> dict:
    return {"id": f"sc_{name.replace(' ', '_')}", "status": name, "type": kind, "orderindex": i,
            "color": color}  # fmt: skip


def statuses(*specs: tuple[str, str]) -> list[dict]:
    return [status(n, k, i) for i, (n, k) in enumerate(specs)]


TODO_ST = statuses(("todo", "open"), ("in progress", "custom"), ("complete", "closed"))
READ_ST = statuses(("not read", "open"), ("idea", "custom"), ("read", "closed"))
IDEAS_ST = statuses(("to do", "open"), ("complete", "closed"))
BACKLOG_ST = statuses(
    ("inbox", "open"), ("idea", "custom"), ("ready", "custom"), ("shipping", "custom"),
    ("parked", "custom"), ("blocked", "custom"), ("done", "closed"),
)  # fmt: skip
PROJ_ST = statuses(("not started", "open"), ("in progress", "custom"), ("complete", "closed"))
LEARN_ST = statuses(("not started", "open"), ("complete", "closed"))

SPACE_TODO = {"id": "90131000001", "name": "Todo", "private": False, "archived": False,
              "statuses": TODO_ST, "multiple_assignees": True}  # fmt: skip
SPACE_AI = {"id": "90131000002", "name": "AI", "private": False, "archived": False,
            "statuses": PROJ_ST, "multiple_assignees": True}  # fmt: skip
FOLDER_BUILD = {"id": "90131000010", "name": "Build", "orderindex": 0, "override_statuses": False,
                "hidden": False, "space": {"id": SPACE_AI["id"], "name": "AI", "access": True},
                "task_count": "8", "statuses": [], "lists": []}  # fmt: skip


def lst(id_: str, name: str, space: dict, sts: list[dict] | None, folder: dict | None = None,
        count: int | None = None) -> dict:  # fmt: skip
    out = {
        "id": id_, "name": name, "orderindex": 0, "content": "", "status": None, "priority": None,
        "assignee": None, "task_count": str(count) if count is not None else None,
        "due_date": None, "start_date": None,
        "folder": ({"id": folder["id"], "name": folder["name"], "hidden": False, "access": True}
                   if folder else {"id": "0", "name": "hidden", "hidden": True, "access": True}),
        "space": {"id": space["id"], "name": space["name"], "access": True},
        "archived": False, "override_statuses": True, "permission_level": "create",
    }  # fmt: skip
    if sts is not None:
        out["statuses"] = sts
    return out


MAIN = lst("901300000104", "Tasks", SPACE_TODO, TODO_ST)
READ = lst("901300000105", "To read/watch", SPACE_TODO, READ_ST)
IDEAS = lst("901300000106", "Project Ideas", SPACE_TODO, None)  # statuses via GET /list/{id}
OLD = lst("901300000199", "Archive 2025", SPACE_TODO, TODO_ST, count=12)  # not mapped
BACKLOG = lst("901300000101", "Backlog", SPACE_AI, BACKLOG_ST, folder=FOLDER_BUILD)
PROJECTS = lst("901300000107", "Projects", SPACE_AI, PROJ_ST)
LEARNING = lst("901300000102", "Learning", SPACE_AI, LEARN_ST)
DESIGN = lst("901300000103", "Design", SPACE_AI, LEARN_ST)

_counter = 0
TASKS: dict[str, list[dict]] = {}
COMMENTS: dict[str, list[dict]] = {}


def task(
    lst_: dict,
    name: str,
    st: str = "",
    *,
    due: date | None = None,
    due_time: time | None = None,
    start: date | None = None,
    priority: str | None = None,
    tags: tuple[str, ...] = (),
    assignees: tuple[dict, ...] = (ALEX,),
    parent: dict | None = None,
    description: str = "",
) -> dict:
    global _counter
    _counter += 1
    tid = f"86a{_counter:06d}"
    sts = lst_.get("statuses") or IDEAS_ST
    chosen = next(s for s in sts if s["status"] == st) if st else sts[0]
    closed = chosen["type"] == "closed"
    prio_ids = {"urgent": "1", "high": "2", "normal": "3", "low": "4"}
    t = {
        "id": tid,
        "custom_id": None,
        "custom_item_id": 0,
        "name": name,
        "text_content": description,
        "description": description,
        "markdown_description": description,
        "status": chosen,
        "orderindex": f"{_counter}.00000000000000000000000000000000",
        "date_created": CREATED,
        "date_updated": CREATED,
        "date_closed": "1761000000000" if closed else None,
        "date_done": "1761000000000" if closed else None,
        "archived": False,
        "creator": ALEX,
        "assignees": list(assignees),
        "group_assignees": [],
        "watchers": [ALEX],
        "checklists": [],
        "tags": [
            {"name": n, "tag_fg": "#800000", "tag_bg": "#800000", "creator": ALEX["id"]}
            for n in tags
        ],  # fmt: skip
        "parent": parent["id"] if parent else None,
        "top_level_parent": parent["id"] if parent else None,
        "priority": (
            {
                "color": "#f50000",
                "id": prio_ids[priority],
                "orderindex": prio_ids[priority],
                "priority": priority,
            }
            if priority
            else None
        ),  # fmt: skip
        "due_date": ms(due, due_time or time(4, 0)) if due else None,
        "due_date_time": bool(due_time) if due else False,
        "start_date": ms(start) if start else None,
        "start_date_time": False,
        "points": None,
        "time_estimate": None,
        "custom_fields": [],
        "dependencies": [],
        "linked_tasks": [],
        "locations": [],
        "team_id": TEAM,
        "url": f"https://app.clickup.com/t/{tid}",
        "sharing": {"public": False},
        "permission_level": "create",
        "list": {"id": lst_["id"], "name": lst_["name"], "access": True},
        "project": lst_["folder"],
        "folder": lst_["folder"],
        "space": {"id": lst_["space"]["id"]},
    }
    TASKS.setdefault(lst_["id"], []).append(t)
    return t


def comment(t: dict, cid: str, who: dict, when: datetime, text: str, replies: int = 0) -> None:
    COMMENTS.setdefault(t["id"], []).insert(0, {  # newest first, like the API
        "id": cid, "comment": [{"text": text}], "comment_text": text, "user": who,
        "resolved": False, "assignee": None, "assigned_by": None, "reactions": [],
        "date": str(int(when.timestamp() * 1000)), "reply_count": str(replies),
    })  # fmt: skip


D = date
# --- Tasks: chores filed as dated copies -------------------------------------------------
for d in (D(2026, 10, 14), D(2026, 10, 28), D(2026, 11, 11), D(2026, 11, 25), D(2026, 12, 9),
          D(2026, 12, 23)):  # fmt: skip
    task(MAIN, f"Bulk trash pickup — {d:%b} {d.day}", due=d, tags=("personal", "human"),
         description="Put bulk items at the curb by 7am.")  # fmt: skip
for d in (D(2026, 10, 20), D(2027, 1, 20), D(2027, 4, 20)):
    task(MAIN, "Change AC Filter", due=d, priority="normal", tags=("personal",))
for d in (D(2026, 11, 15), D(2026, 12, 15), D(2027, 1, 15)):
    task(MAIN, "Water the plants", due=d, tags=("personal",))
for d in (D(2026, 10, 23), D(2026, 10, 30), D(2026, 11, 6), D(2026, 11, 13)):
    task(MAIN, "Weekly report (weekly recurring)", due=d, due_time=time(15, 0), priority="high",
         tags=("reports",), description="Send the week's summary to the team.")  # fmt: skip
for d in (D(2026, 11, 5), D(2026, 12, 5), D(2027, 1, 5)):
    task(MAIN, "Budget Check (monthly)", due=d, tags=("personal",),
         description="Compare the month's spending to the budget.")  # fmt: skip
for d in (D(2026, 11, 1), D(2027, 5, 1), D(2027, 11, 1)):
    task(MAIN, "Rotate API Keys", due=d, tags=("automation",))
for d in (D(2026, 11, 26), D(2026, 11, 27), D(2026, 12, 25), D(2027, 1, 1)):
    task(MAIN, f"[EXAMPLE] Expenses: file the receipts — {d:%b} {d.day}", due=d,
         tags=("example", "paperwork"))  # fmt: skip
invoice = task(MAIN, "[EXAMPLE] Send Q4 invoice summary", due=D(2026, 10, 30),
               assignees=(GUEST,), priority="high")  # fmt: skip

# --- Tasks: ordinary tasks ---------------------------------------------------------------------
dentist = task(MAIN, "Call dentist to reschedule", due=D(2026, 10, 27), priority="high",
               tags=("personal", "human"))  # fmt: skip
task(MAIN, "Renew car registration", due=D(2026, 10, 21), priority="urgent", tags=("personal",))
task(MAIN, "Pay the water bill", "in progress", due=D(2026, 10, 24), priority="normal")
task(MAIN, "Buy printer paper", tags=("personal",))
task(MAIN, "[monitor] Server disk at 81%", due=D(2026, 10, 23), due_time=time(9, 30),
     tags=("needs-alex",), assignees=(),
     description="**Monitor**: `/home` is 81% full.")  # fmt: skip
task(MAIN, "Book flu shots", "complete", due=D(2026, 10, 10), tags=("personal",))
task(MAIN, "Plan Thanksgiving menu", start=D(2026, 11, 15), due=D(2026, 11, 20),
     priority="low", tags=("personal", "social media"))  # fmt: skip
mesh = task(MAIN, "Mesh rollout", priority="normal", tags=("network",),
            description="Replace the old router with the mesh kit.")  # fmt: skip
task(MAIN, "Mesh 1 — install nodes", parent=mesh, due=D(2026, 10, 25))
mesh2 = task(MAIN, "Mesh 2 — configure backhaul", parent=mesh, due=D(2026, 10, 26))
task(MAIN, "Mesh 2a — check backhaul speed", parent=mesh2)
comment(dentist, "90010001", ALEX, datetime(2026, 10, 20, 9, 15, tzinfo=TZ),
        "They open at 8; ask for a Tuesday.")  # fmt: skip

# --- To read/watch -----------------------------------------------------------------------------
for i, (name, st, tg) in enumerate(
    [
        ("Article on agent evals", "not read", ("ai project",)),
        ("Intro to language models (video)", "not read", ("ai project",)),
        ("Cooking newsletter — October", "not read", ("cooking",)),
        ("Essay: the agent economy", "idea", ("ai project",)),
        ("Guide to building agents", "not read", ("ai project", "digest")),
        ("Bread baking handbook", "not read", ("cooking",)),
        ("Database write-ahead logs", "idea", ()),
        ("Essay on scaling", "read", ("ai project",)),
    ]
):
    task(READ, name, st, tags=tg, description=f"https://example.com/read/{i}")

# --- Project Ideas (its list object omits statuses) ---------------------------------------------
for name in ("Household inventory", "Voice notes to tasks", "Chore chart",
             "Receipt scanner"):  # fmt: skip
    task(IDEAS, name, tags=("ai project",))
# A closed twin: a list copied wholesale from another holds identical closed tasks. This
# closed copy matches "Book flu shots" exactly (name, due, status, description) and is skipped.
task(IDEAS, "Book flu shots", "complete", due=D(2026, 10, 10), tags=("personal",))

# --- Backlog ---------------------------------------------------------------------------------
backlog_rows = [
    ("Pipeline: retire the old runner", "parked", ("pipeline",)),
    ("Digest: nightly summary", "ready", ("digest",)),
    ("Webhook listener", "inbox", ("automation",)),
    ("Fix reconnect loop", "blocked", ("blocked", "needs-alex")),
    ("Slate importer", "shipping", ("ai project",)),
    ("Dashboard idea", "idea", ("network",)),
    ("Alert dedupe", "ready", ()),
    ("Old script cleanup", "done", ("automation",)),
]
backlog_tasks = [task(BACKLOG, n, st, tags=tg, priority="normal" if st == "ready" else None)
            for n, st, tg in backlog_rows]  # fmt: skip
comment(backlog_tasks[3], "90010002", ALEX, datetime(2026, 10, 19, 21, 4, tzinfo=TZ),
        "Seen again after the update.")  # fmt: skip
comment(backlog_tasks[3], "90010003", GUEST, datetime(2026, 10, 21, 8, 30, tzinfo=TZ),
        "Logs attached.", replies=1)  # fmt: skip

# --- Projects, Learning, Design ------------------------------------------------------------------
task(PROJECTS, "Slate", "in progress", tags=("ai project",))
task(PROJECTS, "Digest v2", tags=("digest",))
task(PROJECTS, "Recipe box", tags=("cooking",))
task(LEARNING, "Course: RL from human feedback", tags=("ai project",))
task(LEARNING, "Read: a databases textbook", "complete")
task(DESIGN, "Photo album layout", tags=("ai project",))
task(DESIGN, "Logo refresh", due=D(2026, 11, 2))


# The `--mapping` the tests import with (see slate.importers.clickup): list names to Slate
# destinations in board order, the one ClickUp user who has a Slate account, and the [EXAMPLE]
# mirror into Work → Mirrored.
MAPPING = {
    "lists": {
        "Tasks": ["Personal", "Todo"],
        "To read/watch": ["Personal", "Read / Watch"],
        "Project Ideas": ["AI", "Ideas"],
        "Backlog": ["AI", "Backlog"],
        "Projects": ["AI", "Projects"],
        "Learning": ["AI", "Projects"],
        "Design": ["AI", "Design"],
    },
    "assignees": {str(ALEX["id"]): "alex"},
    "audiences": {"Personal": "personal", "AI": "ai", "Work": "work"},
    "mirror": {"prefix": "[EXAMPLE]", "tag": "example", "section": "Work", "list": "Mirrored"},
}


# --- Write ---------------------------------------------------------------------------------------
def write(path: str, data: dict, params: dict | None = None) -> None:
    (HERE / fixture_name(path, params)).write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def main() -> None:
    for old in HERE.glob("*.json"):
        old.unlink()
    write(f"/team/{TEAM}/space", {"spaces": [SPACE_TODO, SPACE_AI]})
    write(f"/space/{SPACE_TODO['id']}/folder", {"folders": []})
    write(f"/space/{SPACE_TODO['id']}/list", {"lists": [MAIN, READ, IDEAS, OLD]})
    write(f"/space/{SPACE_AI['id']}/folder", {"folders": [FOLDER_BUILD]})
    write(f"/folder/{FOLDER_BUILD['id']}/list", {"lists": [BACKLOG]})
    write(f"/space/{SPACE_AI['id']}/list", {"lists": [PROJECTS, LEARNING, DESIGN]})
    write(f"/list/{IDEAS['id']}", {**IDEAS, "statuses": IDEAS_ST})
    for lst_ in (MAIN, READ, IDEAS, BACKLOG, PROJECTS, LEARNING, DESIGN):
        tasks = TASKS.get(lst_["id"], [])
        # Tasks comes back in two pages, to exercise paging.
        pages = [tasks[:20], tasks[20:]] if lst_ is MAIN else [tasks]
        for n, page in enumerate(pages):
            params = {"page": n, "list_ids[]": lst_["id"]}
            write(f"/team/{TEAM}/task", {"tasks": page, "last_page": n == len(pages) - 1}, params)
        for t in tasks:
            write(f"/task/{t['id']}/comment", {"comments": COMMENTS.get(t["id"], [])})
    (HERE / "mapping.json").write_text(
        json.dumps(MAPPING, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    total = sum(len(v) for v in TASKS.values())
    print(f"wrote fixtures for {total} tasks to {HERE}")


if __name__ == "__main__":
    main()
