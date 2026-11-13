"""`slate` command line: run the server, migrate, regenerate the agent docs, check webhook
targets before an upgrade."""

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
        "check-targets",
        help="list webhooks and automations that point at a private address (exit 1 if any)",
    )

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
    if args.cmd == "check-targets":
        return check_targets()
    return 1


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


if __name__ == "__main__":
    sys.exit(main())
