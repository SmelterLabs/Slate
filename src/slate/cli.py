"""`slate` command line: run the server, migrate, import, regenerate the agent docs, check
webhook targets before an upgrade."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slate")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    sub.add_parser("migrate", help="apply database migrations")

    docs = sub.add_parser("docs", help="regenerate docs/agents/reference from the code")
    docs.add_argument("--check", action="store_true", help="exit 1 if the files are stale")

    sub.add_parser(
        "backfill-doc-text",
        help="extract searchable text for file revisions uploaded before compare existed",
    )
    sub.add_parser(
        "check-targets",
        help="list webhooks and automations that point at a private address (exit 1 if any)",
    )

    imp = sub.add_parser("import", help="import from another tool").add_subparsers(
        dest="source", required=True
    )
    cu = imp.add_parser("clickup", help="import a ClickUp workspace (dry run unless --apply)")
    cu.add_argument("--team", required=True, help="ClickUp workspace (team) id")
    cu.add_argument("--token-env", default="CLICKUP_TOKEN", help="env var holding the API token")
    mode = cu.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (the default)")
    mode.add_argument("--apply", action="store_true", help="write to the Slate database")
    cu.add_argument("--db", help="SQLite file (default: SLATE_DATABASE_PATH)")
    cu.add_argument("--fixture", help="read recorded ClickUp responses from this directory")
    cu.add_argument("--record", help="save every live ClickUp response into this directory")
    cu.add_argument(
        "--mapping",
        help="JSON file saying where ClickUp lists go and who is who "
        "(default: every list under its own name, nobody assigned)",
    )
    cu.add_argument(
        "--as",
        dest="handle",
        help="Slate account acting the import (default: the workspace owner)",
    )
    cu.add_argument("--now", help="pretend it is this ISO 8601 time (rehearsals and tests)")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("slate.app:app", host=args.host, port=args.port)
        return 0
    if args.cmd == "migrate":
        from pathlib import Path

        from alembic import command
        from alembic.config import Config

        package = Path(__file__).resolve().parent
        migrations = package / "migrations"
        if not migrations.is_dir():  # editable source checkout
            migrations = package.parents[1] / "alembic"
        config = Config()
        config.set_main_option("script_location", str(migrations).replace("%", "%%"))
        command.upgrade(config, "head")
        return 0
    if args.cmd == "docs":
        from slate import docgen

        return docgen.main(check=args.check)
    if args.cmd == "import" and args.source == "clickup":
        return import_clickup(args)
    if args.cmd == "check-targets":
        return check_targets()
    if args.cmd == "backfill-doc-text":
        return backfill_doc_text()
    return 1


def backfill_doc_text() -> int:
    """`slate backfill-doc-text`: read the text out of every file revision that has none yet
    (uploaded before revisions were compared), so search and PDF compare see inside them."""
    from slate.db import SessionLocal
    from slate.service import doc_compare

    total = 0
    with SessionLocal() as session:
        while True:
            n = doc_compare.backfill_text(session)
            total += n
            if n == 0:
                break
    print(f"extracted text for {total} revision(s)")
    return 0


def check_targets() -> int:
    """`slate check-targets`: which webhooks and automation webhook actions would stop
    delivering with `SLATE_WEBHOOK_PRIVATE_TARGETS` off (the default). Prints each one; exit
    1 when there is any, so a deploy script can stop."""
    from slate import db
    from slate.service import webhooks
    from slate.settings import settings

    with db.SessionLocal() as session:
        found = webhooks.private_targets(session)
    if not found:
        print("No webhook or automation points at a private address.")
        return 0
    state = "allowed" if settings.webhook_private_targets else "REFUSED"
    print(f"{len(found)} target(s) on a private address ({state} by this server's settings):")
    for row in found:
        print(f"  {row['kind']:<10} {row['id']}  {row['name']}: {row['url']}")
    if not settings.webhook_private_targets:
        print(
            "These will not deliver. For receivers on this host or network, set "
            "SLATE_WEBHOOK_PRIVATE_TARGETS=true in .env; otherwise point them at a public https "
            "address or disable them."
        )
    return 1


def import_clickup(args: argparse.Namespace) -> int:
    """`slate import clickup`: build the plan, print the report, write only with --apply."""
    import os
    from pathlib import Path

    from sqlalchemy import inspect
    from sqlalchemy.orm import sessionmaker

    from slate.dates import parse_when
    from slate.importers import clickup
    from slate.importers.clickup_api import ClickUpError, FixtureSource, HttpSource
    from slate.service.errors import ServiceError

    try:
        if args.fixture:
            source = FixtureSource(Path(args.fixture))
        else:
            token = os.environ.get(args.token_env)
            if not token:
                print(
                    f"error: ${args.token_env} is not set (the ClickUp API token)", file=sys.stderr
                )
                return 2
            record = Path(args.record) if args.record else None
            source = HttpSource(token, record_dir=record)
        now = parse_when(args.now) if args.now else None
        mapping = clickup.load_mapping(args.mapping) if args.mapping else None
        plan = clickup.build_plan(source, args.team, now, mapping=mapping)
    except (ClickUpError, ServiceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.apply:
        print(clickup.render_report(plan))
        return 0

    from slate.db import make_engine
    from slate.settings import settings

    path = Path(args.db) if args.db else settings.database_path
    if not path.exists():
        print(f"error: {path} does not exist; run `slate migrate` first", file=sys.stderr)
        return 2
    engine = make_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    if not inspect(engine).has_table("items"):
        print(f"error: {path} has no Slate schema; run `slate migrate` first", file=sys.stderr)
        return 2
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        try:
            handle = args.handle or clickup.owner_handle(session)
            result = clickup.apply_plan(session, handle, plan)
        except ServiceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    print(clickup.render_report(plan, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
