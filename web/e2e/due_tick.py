"""One scheduler due tick against the e2e database (the e2e server runs with the scheduler off).

Run with SLATE_DATABASE_PATH pointing at the e2e database. Emits the real item.due_soon /
item.due / item.overdue events into the events table; a board that reconnects replays them.
"""

from slate import db
from slate.models import utcnow
from slate.service import due

with db.SessionLocal() as session:
    print(due.run_due_checks(session, utcnow()))
