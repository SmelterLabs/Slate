"""Run the scheduler's chat-turn expiry an hour ahead against the e2e database (the e2e server
runs with the scheduler off), so every taken chat turn's lease has run out.

Run with SLATE_DATABASE_PATH pointing at the e2e database. Prints how many turns it ended.
"""

from datetime import timedelta

from slate import db
from slate.models import utcnow
from slate.service import chatwake

with db.SessionLocal() as session:
    print(chatwake.expire(session, utcnow() + timedelta(hours=1)))
