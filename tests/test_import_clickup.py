"""The ClickUp importer, against recorded fixtures (tests/fixtures/clickup)."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from slate import cli
from slate.db import Base, make_engine
from slate.importers import clickup, collapse
from slate.importers.clickup_api import (
    FixtureSource,
    HttpSource,
    TokenBucket,
    fetch_comments,
    fetch_tasks,
    fixture_name,
)
from slate.models import (
    Comment,
    Event,
    Item,
    List,
    Recurrence,
    Section,
    Source,
    Status,
    StatusKind,
    Tag,
)
from slate.service import accounts, items, lists, recurrences

FIX = Path(__file__).parent / "fixtures" / "clickup"
MAPPING_FILE = FIX / "mapping.json"
MAPPING = clickup.load_mapping(MAPPING_FILE)
TEAM = "90130000000"
NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)  # Fri Oct 23, 10:00 in Chicago
TZ = ZoneInfo("America/Chicago")


@pytest.fixture
def plan():
    return clickup.build_plan(FixtureSource(FIX), TEAM, NOW, mapping=MAPPING)


@pytest.fixture
def alex(db):
    account, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    return account


@pytest.fixture
def applied(db, alex, plan):
    return clickup.apply_plan(db, "alex", plan)


def by_ref(db, ref: str) -> Item:
    return db.scalar(select(Item).where(Item.external_ref == f"clickup:{ref}"))


def by_title(db, title: str) -> list[Item]:
    return list(db.scalars(select(Item).where(Item.title == title)))


def list_named(db, name: str) -> List:
    return db.scalar(select(List).where(List.name == name))


def count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


# --- Collapse heuristic ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "rule"),
    [
        ([date(2026, 10, 2), date(2026, 10, 9), date(2026, 10, 16)], "FREQ=WEEKLY;BYDAY=FR"),
        (
            [date(2026, 10, 14), date(2026, 10, 28), date(2026, 11, 11), date(2026, 11, 25)],
            "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE",
        ),
        (
            [date(2026, 11, 5), date(2026, 12, 5), date(2027, 1, 5), date(2027, 2, 5)],
            "FREQ=MONTHLY;BYMONTHDAY=5",
        ),
        (
            [date(2026, 10, 20), date(2027, 1, 20), date(2027, 4, 20)],
            "FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=20",
        ),
        (
            [date(2026, 11, 1), date(2027, 5, 1), date(2027, 11, 1)],
            "FREQ=MONTHLY;INTERVAL=6;BYMONTHDAY=1",
        ),
        (
            [date(2026, 3, 1), date(2027, 3, 1), date(2028, 3, 1)],
            "FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=1",
        ),
    ],
)
def test_rule_inference_per_gap_pattern(days, rule):
    assert collapse.infer_rrule(days).rrule == rule
    assert collapse.infer_rrule(list(reversed(days))).rrule == rule  # order does not matter


@pytest.mark.parametrize(
    "days",
    [
        # four dates with gaps 1, 28, 7
        [date(2026, 11, 26), date(2026, 11, 27), date(2026, 12, 25), date(2027, 1, 1)],
        # weekly with a hole
        [date(2026, 10, 2), date(2026, 10, 9), date(2026, 10, 23)],
        # monthly but drifting day of month
        [date(2026, 11, 5), date(2026, 12, 7), date(2027, 1, 5)],
        # two on the same day
        [date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 9)],
        [date(2026, 10, 2)],
    ],
)
def test_irregular_dates_have_no_rule(days):
    assert collapse.infer_rrule(days).rrule is None


def test_family_key_groups_dated_copies_and_matches_the_template():
    names = ["Bulk trash pickup — Nov 11", "Bulk trash pickup — Wed Nov 25", "bulk trash pickup"]
    keys = {collapse.family_key(n) for n in names}
    assert keys == {"bulk trash pickup"}
    assert collapse.family_key("Bulk trash pickup — {date}") == "bulk trash pickup"
    assert collapse.family_key("Weekly report (weekly recurring)") == "weekly report"
    assert collapse.family_key("Budget Check (monthly)") == "budget check"
    assert collapse.family_key("Report 2026-11-01") == collapse.family_key("Report 12/01/26")
    assert collapse.title_template(names[:2]) == "Bulk trash pickup — {date}"
    assert collapse.title_template(["Weekly report (weekly recurring)"]) == "Weekly report"


def test_rrule_in_plain_words():
    assert collapse.describe("FREQ=WEEKLY;INTERVAL=2;BYDAY=WE") == "every 2 weeks on Wednesday"
    assert collapse.describe("FREQ=WEEKLY;BYDAY=FR") == "every week on Friday"
    assert collapse.describe("FREQ=MONTHLY;BYMONTHDAY=1") == "every month on the 1st"
    assert (
        collapse.describe("FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=22") == "every 3 months on the 22nd"
    )
    assert (
        collapse.describe("FREQ=MONTHLY;INTERVAL=6;BYMONTHDAY=13") == "every 6 months on the 13th"
    )
    assert collapse.describe("FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=1") == "every year on Mar 1"


# --- The plan and the dry-run report -----------------------------------------------------------


def test_plan_collapses_the_known_families(plan):
    rules = {r.title_template: r for r in plan.rules}
    assert set(rules) == {
        "Bulk trash pickup — {date}",
        "Change AC Filter",
        "Water the plants",
        "Weekly report",
        "Budget Check",
        "Rotate API Keys",
    }
    trash = rules["Bulk trash pickup — {date}"]
    assert trash.rrule == "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE"
    assert trash.dtstart == date(2026, 11, 11)  # the first copy the rule now makes
    assert (len(trash.members), len(trash.imported), len(trash.dropped)) == (6, 2, 4)
    assert rules["Weekly report"].rrule == "FREQ=WEEKLY;BYDAY=FR"
    assert rules["Budget Check"].rrule == "FREQ=MONTHLY;BYMONTHDAY=5"
    assert rules["Change AC Filter"].rrule == "FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=20"
    assert rules["Rotate API Keys"].rrule == "FREQ=MONTHLY;INTERVAL=6;BYMONTHDAY=1"
    assert rules["Water the plants"].imported == []  # every copy is beyond the window
    assert all(r.dest == ("Personal", "Todo") for r in plan.rules)
    assert len(plan.dropped) == 14


def test_irregular_dates_are_not_collapsed(plan):
    assert [ir.title for ir in plan.irregular] == ["[EXAMPLE] Expenses: file the receipts"]
    assert plan.irregular[0].gaps == (1, 28, 7)
    expenses = [pi for pi in plan.items if "Expenses" in pi.title]
    assert len(expenses) == 4 and all(pi.dest == ("Work", "Mirrored") for pi in expenses)


def test_dry_run_report(plan):
    report = clickup.render_report(plan)
    assert "DRY RUN — nothing written" in report
    assert "Read 65 tasks (4 closed) from 7 lists; 3 comments" in report
    for line in [
        "Personal → Todo              19 items (1 closed → archived, 3 subtasks, 4 overdue)",
        "Personal → Read / Watch       8 items (1 closed → archived)",
        "AI → Ideas                    4 items",
        "AI → Backlog                  8 items (1 closed → archived)",
        "AI → Projects                 5 items (1 closed → archived)",
        "AI → Design                   2 items",
        "Work → Mirrored               5 items",
        "Total                        51 items",
        "statuses: inbox, idea, ready, shipping [active], parked, blocked, done [done]",
    ]:
        assert line in report, line
    assert "Recurrence rules (6)" in report
    for words in [
        "every 2 weeks on Wednesday, starting Wed Nov 11  [FREQ=WEEKLY;INTERVAL=2;BYDAY=WE]",
        "every 3 months on the 20th, starting Wed Jan 20  [FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=20]",
        "every month on the 15th, starting Sun Nov 15  [FREQ=MONTHLY;BYMONTHDAY=15]",
        "every week on Friday, starting Fri Nov 13  [FREQ=WEEKLY;BYDAY=FR]",
        "every month on the 5th, starting Sat Dec 5  [FREQ=MONTHLY;BYMONTHDAY=5]",
        "every 6 months on the 1st, starting Sat May 1  [FREQ=MONTHLY;INTERVAL=6;BYMONTHDAY=1]",
    ]:
        assert words in report, words
    assert "replaces 6 ClickUp tasks: 2 imported (1 overdue), 4 dropped" in report
    assert "“[EXAMPLE] Expenses: file the receipts” — 4 tasks, gaps 1, 28, 7 days" in report
    assert "Dropped — recurrence copies due after Fri Nov 6 (14)" in report
    assert "Bulk trash pickup — Dec 23 · due Wed Dec 23   → rule “Bulk trash pickup — {date}”" in (
        report
    )
    # Subtasks nest four deep, so a ClickUp sub-subtask keeps its parent: nothing flattened.
    assert "→ now under “Mesh rollout”" not in report
    assert "[EXAMPLE] Send Q4 invoice summary · due Fri Oct 30   (was: Guest)" in report
    assert "“Archive 2025” in Todo (list 901300000199, 12 tasks)" in report
    assert "1 comment(s) have threaded replies" in report


def test_dry_run_cli_writes_nothing(tmp_path, capsys):
    db_file = tmp_path / "slate.db"
    code = cli.main(
        [
            "import",
            "clickup",
            "--team",
            TEAM,
            "--fixture",
            str(FIX),
            "--mapping",
            str(MAPPING_FILE),
            "--dry-run",
            "--db",
            str(db_file),
            "--now",
            "2026-10-23T10:00:00-05:00",
        ]  # fmt: skip
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Recurrence rules (6)" in out and "Nothing was written" in out
    assert not db_file.exists()


# --- Apply ---------------------------------------------------------------------------------------


def test_apply_creates_sections_lists_and_statuses(db, applied):
    sections = {s.name: s.audience.value for s in db.scalars(select(Section))}
    assert sections == {"Personal": "personal", "AI": "ai", "Work": "work"}
    layout = {
        (db.get(Section, lst.section_id).name, lst.name)
        for lst in lists.list_lists(db, include_archived=True)
    }
    # Only destinations the mapping names are created.
    assert layout <= set(MAPPING.destinations)
    assert layout >= {
        ("Personal", "Todo"),
        ("Personal", "Read / Watch"),
        ("AI", "Ideas"),
        ("AI", "Backlog"),
        ("AI", "Projects"),
        ("AI", "Design"),
        ("Work", "Mirrored"),
    }
    backlog = list_named(db, "Backlog")
    sts = [(s.name, s.kind) for s in lists.statuses_of(db, backlog.id)]
    assert sts == [
        ("inbox", StatusKind.open),
        ("idea", StatusKind.open),
        ("ready", StatusKind.open),
        ("shipping", StatusKind.active),
        ("parked", StatusKind.open),
        ("blocked", StatusKind.open),
        ("done", StatusKind.done),
    ]
    todo = list_named(db, "Todo")
    assert db.get(Status, todo.default_status_id).name == "todo"
    ideas = [s.name for s in lists.statuses_of(db, list_named(db, "Ideas").id)]
    assert ideas == ["to do", "complete"]  # came from GET /list/{id}
    assert (applied.sections_created, applied.lists_created) == (3, 7)


def test_apply_creates_items_tags_comments_and_rules(db, applied):
    assert applied.created == count(db, Item) == 51
    assert all(i.external_ref.startswith("clickup:") for i in db.scalars(select(Item)))
    per_list = dict(
        db.execute(select(List.name, func.count()).join(Item).group_by(List.name)).all()
    )
    assert per_list == {
        "Todo": 19,
        "Read / Watch": 8,
        "Ideas": 4,
        "Backlog": 8,
        "Projects": 5,
        "Design": 2,
        "Mirrored": 5,
    }

    dentist = by_title(db, "Call dentist to reschedule")[0]
    view = items.views(db, [dentist])[0]
    assert view["priority"] == "high"
    assert view["tags"] == ["human", "personal"]
    assert [a["handle"] for a in view["assignees"]] == ["alex"]
    assert view["status"]["name"] == "todo"
    assert dentist.body_md.endswith(
        f"Imported from ClickUp https://app.clickup.com/t/{dentist.external_ref[8:]} on 2026-10-23"
    )
    assert {t.name for t in db.scalars(select(Tag))} >= {
        "personal", "human", "ai project", "digest", "example", "paperwork", "automation",
        "pipeline", "network", "needs-alex", "reports", "blocked", "cooking", "social media",
    }  # fmt: skip

    notes = items.comments_of(db, dentist.id)
    assert len(notes) == 1
    assert notes[0].body_md.startswith(
        "**ClickUp comment** by Alex · 2026-10-20 09:15 CDT <!-- clickup-comment:90010001 -->"
    )
    assert notes[0].body_md.endswith("They open at 8; ask for a Tuesday.")
    reconnect = by_title(db, "Fix reconnect loop")[0]
    bodies = [c.body_md for c in items.comments_of(db, reconnect.id)]
    assert "by Alex · 2026-10-19 21:04 CDT" in bodies[0]  # oldest first
    assert "by Guest · 2026-10-21 08:30 CDT" in bodies[1]
    assert count(db, Comment) == applied.comments_added == 3

    rules = {r.title_template: r for r in db.scalars(select(Recurrence))}
    assert len(rules) == applied.rules_created == 6
    trash = rules["Bulk trash pickup — {date}"]
    assert trash.rrule == "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE"
    assert trash.list_id == list_named(db, "Todo").id
    assert trash.dtstart.astimezone(TZ).date() == date(2026, 11, 11)
    assert trash.tags_json == ["human", "personal"]
    assert trash.body_template_md == "Put bulk items at the curb by 7am."
    assert rules["Weekly report"].priority.value == "high"


def test_only_the_window_of_each_family_is_imported(db, applied):
    trash = sorted(
        (i for i in db.scalars(select(Item)) if i.title.startswith("Bulk trash")),
        key=lambda i: i.due_at,
    )
    assert [i.title for i in trash] == ["Bulk trash pickup — Oct 14", "Bulk trash pickup — Oct 28"]
    assert trash[0].due_at < NOW  # past due: imported as an ordinary overdue item
    assert by_title(db, "Water the plants") == []
    assert len(by_title(db, "Weekly report (weekly recurring)")) == 3
    assert len([i for i in db.scalars(select(Item)) if "Expenses" in i.title]) == 4


def test_rule_takes_over_after_the_imported_window(db, applied):
    """The scheduler, run day by day for a month, makes each later pickup exactly once and never
    duplicates an imported one."""
    trash = db.scalar(select(Recurrence).where(Recurrence.title_template.like("Bulk trash%")))
    for day in range(0, 35):
        recurrences.materialize(db, NOW + timedelta(days=day))
    spawned = sorted(
        i.due_at.astimezone(TZ).date()
        for i in db.scalars(select(Item).where(Item.recurrence_id == trash.id))
    )
    assert spawned == [date(2026, 11, 11), date(2026, 11, 25), date(2026, 12, 9)]


def test_prefixed_tasks_go_to_mirrored_tagged_example(db, applied):
    mirrored = list_named(db, "Mirrored")
    assert db.get(Section, mirrored.section_id).name == "Work"
    mirrored_items = [i for i in db.scalars(select(Item)) if i.title.startswith("[EXAMPLE]")]
    assert len(mirrored_items) == 5 and all(i.list_id == mirrored.id for i in mirrored_items)
    assert all("example" in items.views(db, [i])[0]["tags"] for i in mirrored_items)
    invoice = by_title(db, "[EXAMPLE] Send Q4 invoice summary")[0]
    assert items.views(db, [invoice])[0]["assignees"] == []  # the guest has no Slate account


def test_deep_subtask_keeps_its_parent(db, applied):
    """Subtasks nest four deep, so a ClickUp sub-subtask stays under its own parent."""
    mesh = by_title(db, "Mesh rollout")[0]
    kids = {i.title: i for i in items.subtasks_of(db, mesh.id)}
    assert set(kids) == {"Mesh 1 — install nodes", "Mesh 2 — configure backhaul"}
    mesh2 = kids["Mesh 2 — configure backhaul"]
    [deep] = items.subtasks_of(db, mesh2.id)
    assert deep.title == "Mesh 2a — check backhaul speed" and deep.parent_id == mesh2.id
    assert "Was a subtask of" not in deep.body_md


def test_closed_tasks_are_archived_in_a_done_status(db, applied):
    for title in ("Book flu shots", "Old script cleanup", "Essay on scaling"):
        item = by_title(db, title)[0]
        assert item.archived_at is not None, title
        assert db.get(Status, item.status_id).kind == StatusKind.done
        assert item.completed_at is not None
    assert applied.archived == 4
    open_ = by_title(db, "Renew car registration")[0]
    assert open_.archived_at is None and open_.completed_at is None


def test_dates_and_priorities(db, applied):
    # date-only due (stored 04:00 local by ClickUp) -> Slate's date rule, 17:00 Chicago
    reg = by_title(db, "Renew car registration")[0]
    assert reg.due_at == datetime(2026, 10, 21, 22, 0, tzinfo=UTC)
    assert reg.priority.value == "urgent"
    # a timed due keeps its time: 09:30 CDT
    disk = by_title(db, "[monitor] Server disk at 81%")[0]
    assert disk.due_at == datetime(2026, 10, 23, 14, 30, tzinfo=UTC)
    assert disk.priority is None
    assert items.views(db, [disk])[0]["assignees"] == []
    menu = by_title(db, "Plan Thanksgiving menu")[0]
    assert menu.start_at == datetime(2026, 11, 15, 23, 0, tzinfo=UTC)  # CST by then
    assert menu.priority.value == "low"


def test_every_import_event_is_alex_via_system(db, alex, applied):
    events = list(db.scalars(select(Event).where(Event.session_ref == "import:clickup")))
    assert len(events) == applied.events > 0
    assert {(e.actor_id, e.source) for e in events} == {(alex.id, Source.system)}


def test_second_apply_writes_nothing(db, alex, plan, applied):
    tables = (Item, Comment, Recurrence, Status, Tag, Event, List, Section)
    before = {m: count(db, m) for m in tables}
    again = clickup.apply_plan(
        db, "alex", clickup.build_plan(FixtureSource(FIX), TEAM, NOW, mapping=MAPPING)
    )
    assert {m: count(db, m) for m in tables} == before
    assert (again.created, again.updated, again.unchanged) == (0, 0, 51)
    assert again.comments_added == again.rules_created == again.archived == 0
    assert len(again.rules_existing) == 6
    assert again.events == 0
    assert "rules created 0, already there 6" in clickup.render_report(plan, again)


def test_rerun_updates_a_changed_task_in_place(db, alex, applied, tmp_path):
    fixtures = tmp_path / "fx"
    shutil.copytree(FIX, fixtures)
    page = fixtures / fixture_name(f"/team/{TEAM}/task", {"page": 1, "list_ids[]": "901300000104"})
    data = json.loads(page.read_text(encoding="utf-8"))
    target = next(t for t in data["tasks"] if t["name"] == "Renew car registration")
    target["name"] = "Renew car registration (online)"
    target["priority"] = None
    page.write_text(json.dumps(data), encoding="utf-8")

    result = clickup.apply_plan(
        db, "alex", clickup.build_plan(FixtureSource(fixtures), TEAM, NOW, mapping=MAPPING)
    )
    assert (result.created, result.updated) == (0, 1)
    item = by_ref(db, target["id"])
    assert item.title == "Renew car registration (online)" and item.priority is None
    assert count(db, Item) == 51


def test_rerun_two_weeks_later_leaves_rule_dates_to_the_rule(db, alex, applied):
    """By Nov 2 the window reaches Nov 16: the Nov 11 pickup, Nov 13 report and Nov 15 watering are
    inside it, but the existing rules already make them, so the ClickUp copies stay out."""
    later = NOW + timedelta(days=10)
    result = clickup.apply_plan(
        db, "alex", clickup.build_plan(FixtureSource(FIX), TEAM, later, mapping=MAPPING)
    )
    assert result.rules_created == 0
    assert result.created == 0
    plan = clickup.build_plan(FixtureSource(FIX), TEAM, later, mapping=MAPPING, with_comments=False)
    names = {pi.ref: pi.title for pi in plan.items}
    assert sorted(names[r] for r in result.owned_by_rule) == [
        "Bulk trash pickup — Nov 11",
        "Water the plants",
        "Weekly report (weekly recurring)",
    ]
    trash = [i for i in db.scalars(select(Item)) if i.title.startswith("Bulk trash")]
    assert len(trash) == 2


def test_apply_needs_the_acting_account(db, plan):
    with pytest.raises(Exception, match="no Slate account 'alex'"):
        clickup.apply_plan(db, "alex", plan)
    assert count(db, Item) == 0


def test_apply_cli_on_a_database_file(tmp_path, capsys):
    db_file = tmp_path / "slate.db"
    engine = make_engine(f"sqlite+pysqlite:///{db_file.as_posix()}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        accounts.bootstrap(session, handle="alex", display_name="Alex")
    args = ["import", "clickup", "--team", TEAM, "--fixture", str(FIX), "--db", str(db_file),
            "--mapping", str(MAPPING_FILE), "--as", "alex",
            "--now", "2026-10-23T10:00:00-05:00"]  # fmt: skip
    assert cli.main([*args, "--apply"]) == 0
    out = capsys.readouterr().out
    assert "ClickUp import (APPLIED)" in out
    assert "items created 51, updated 0, unchanged 0, archived 4" in out
    with sessionmaker(bind=engine)() as session:
        assert count(session, Item) == 51
    assert cli.main([*args, "--apply"]) == 0
    assert "items created 0, updated 0, unchanged 51" in capsys.readouterr().out
    engine.dispose()


def test_cli_errors_exit_2(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CLICKUP_TOKEN", raising=False)
    assert cli.main(["import", "clickup", "--team", TEAM]) == 2
    assert "CLICKUP_TOKEN is not set" in capsys.readouterr().err
    assert cli.main(["import", "clickup", "--team", TEAM, "--fixture", str(tmp_path / "x")]) == 2
    missing = tmp_path / "none.db"
    args = [
        "import",
        "clickup",
        "--team",
        TEAM,
        "--fixture",
        str(FIX),
        "--mapping",
        str(MAPPING_FILE),
    ]
    args += ["--apply"]
    assert cli.main([*args, "--db", str(missing)]) == 2
    assert "slate migrate" in capsys.readouterr().err


# --- The ClickUp side: paging, comments, rate limit, recording ---------------------------------


class FakeSource:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        return self.answers(path, dict(params or {}))


def test_task_paging_stops_on_last_page_or_a_short_page():
    pages = {0: [{"id": str(i)} for i in range(100)], 1: [{"id": "x"}]}
    src = FakeSource(lambda p, q: {"tasks": pages[q["page"]]})  # no last_page field
    assert len(fetch_tasks(src, TEAM, "L1")) == 101
    assert [c[1]["page"] for c in src.calls] == [0, 1]
    q = src.calls[0][1]
    assert (q["include_closed"], q["subtasks"], q["list_ids[]"]) == ("true", "true", "L1")
    flagged = FakeSource(lambda p, q: {"tasks": [{"id": "a"}], "last_page": q["page"] == 2})
    assert len(fetch_tasks(flagged, TEAM, "L1")) == 3


def test_comment_paging_walks_back_with_start_and_start_id():
    all_comments = [
        {"id": f"c{i}", "date": str(1_760_000_000_000 + i * 1000), "comment_text": f"n{i}"}
        for i in range(30)
    ]
    newest_first = sorted(all_comments, key=lambda c: -int(c["date"]))

    def answer(path, q):
        if "start_id" not in q:
            return {"comments": newest_first[:25]}
        idx = next(i for i, c in enumerate(newest_first) if c["id"] == q["start_id"])
        assert q["start"] == newest_first[idx]["date"]
        return {"comments": newest_first[idx + 1 : idx + 26]}

    src = FakeSource(answer)
    got = fetch_comments(src, "T1")
    assert [c["id"] for c in got] == [f"c{i}" for i in range(30)]  # oldest first, no repeats
    assert len(src.calls) == 2


def test_token_bucket_allows_100_a_minute():
    clock = [0.0]
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        clock[0] += s

    bucket = TokenBucket(100, 60.0, clock=lambda: clock[0], sleep=sleep)
    for _ in range(100):
        bucket.take()
    assert slept == []
    bucket.take()
    assert slept and abs(sum(slept) - 0.6) < 1e-6  # one token refills in 60/100 s
    for _ in range(99):
        bucket.take()
    assert abs(clock[0] - 60.0) < 1e-6  # 200 requests took a minute


def test_http_source_retries_429_records_and_replays(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"X-RateLimit-Reset": "0"})
        return httpx.Response(200, json={"spaces": [{"id": "1", "name": "Todo"}]})

    client = httpx.Client(
        base_url="https://api.clickup.com/api/v2", transport=httpx.MockTransport(handler)
    )
    slept: list[float] = []
    src = HttpSource("pk_test", client=client, record_dir=tmp_path, sleep=slept.append)
    data = src.get(f"/team/{TEAM}/space", {"archived": "false"})
    assert data["spaces"][0]["name"] == "Todo"
    assert len(calls) == 2 and slept == [1.0]
    assert calls[1].headers["Authorization"] == "pk_test"
    assert calls[1].url.params["archived"] == "false"
    recorded = tmp_path / f"team_{TEAM}_space.json"
    assert recorded.exists() and "pk_test" not in recorded.read_text(encoding="utf-8")
    assert FixtureSource(tmp_path).get(f"/team/{TEAM}/space") == data


def test_fixture_source_refuses_a_missing_recording(tmp_path):
    with pytest.raises(Exception, match="no recorded response task_X_comment.json"):
        FixtureSource(tmp_path).get("/task/X/comment")


def test_priority_and_status_mapping():
    assert clickup._priority(None) is None
    assert clickup._priority({"id": "1", "priority": "urgent"}) == "urgent"
    assert clickup._priority(2) == "high"
    assert clickup._priority("4") == "low"
    assert clickup._priority("someday") is None
    assert clickup._status_kind({"status": "complete", "type": "closed"}) == "done"
    assert clickup._status_kind({"status": "In Progress", "type": "custom"}) == "active"
    assert clickup._status_kind({"status": "shipping", "type": "custom"}) == "active"
    assert clickup._status_kind({"status": "blocked", "type": "custom"}) == "open"


def test_infer_second_and_fourth_wednesday():
    """A bulk trash pickup on the 2nd and 4th Wednesday, so gaps alternate 14/14/21."""
    from datetime import date

    days = [
        date(2026, 10, 14),
        date(2026, 10, 28),
        date(2026, 11, 11),
        date(2026, 11, 25),
        date(2026, 12, 9),
        date(2026, 12, 23),
        date(2027, 1, 13),
        date(2027, 1, 27),
    ]
    inf = collapse.infer_rrule(days)
    assert inf.rrule == "FREQ=MONTHLY;BYDAY=WE;BYSETPOS=2,4"
    assert collapse.describe(inf.rrule) == "every month on the 2nd and 4th Wednesday"
    assert collapse.next_after(inf.rrule, days[0], date(2027, 1, 27)) == date(2027, 2, 10)


def test_all_closed_family_is_lapsed_not_a_rule(plan):
    """A regular family whose copies are all closed is reported as lapsed; no rule is created.
    Reviving a lapsed chore is an explicit decision after the import."""
    closed = {"closed", "done"}
    assert plan.rules, "fixture has at least one live family"
    for pr in plan.rules:
        assert any((t.get("status") or {}).get("type") not in closed for t in pr.members)
    for pr in plan.lapsed:
        assert all((t.get("status") or {}).get("type") in closed for t in pr.members)


def test_closed_twin_in_another_list_is_skipped(plan):
    """ "Book flu shots" exists closed in Tasks and, identically, in Project Ideas; the
    Project Ideas copy is a twin and is not imported."""
    refs = [pi for pi in plan.items if pi.title == "Book flu shots"]
    assert len(refs) == 1 and refs[0].dest == ("Personal", "Todo")
    assert len(plan.twins) == 1
    skipped, kept = plan.twins[0]
    assert skipped["list"]["name"] == "Project Ideas" and kept["list"]["name"] == "Tasks"
