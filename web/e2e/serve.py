"""Run the e2e server at SLATE_E2E_NOW while keeping asyncio timers real."""

import os
import sys

import uvicorn
from freezegun import freeze_time

from slate.app import app

# Keep route annotations real for FastAPI's lazy resolution. Event IDs also need real time
# because the separate scheduler tick process writes to the same stream.
with freeze_time(os.environ["SLATE_E2E_NOW"], ignore=["slate.api", "slate.ids"], real_asyncio=True):
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]))
