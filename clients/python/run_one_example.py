"""An example --exec command for slate_runner.py: reads the task, says where it is working,
does its "work", and prints a result line. Copy it and replace the middle with your agent.

    python slate_runner.py --url https://slate.example.com --exec "python run_one_example.py"

Env RUN_ONE_FAIL=1 makes it fail (exit 1), to see a blocked finish.
"""

import json
import os
import sys

task = json.load(sys.stdin)  # the /work/next answer: item, lease, context
item = task["item"]

# Progress: any JSON line without "outcome" updates the card at once (note, progress, url).
print(json.dumps({"note": "Looking at it", "progress": 10, "url": os.environ["SLATE_ITEM_URL"]}))
sys.stdout.flush()

if os.environ.get("RUN_ONE_FAIL"):
    print("could not reach the printer", file=sys.stderr)
    sys.exit(1)

# ... the real work goes here ...

# The result: the last JSON line with "outcome" (done, review, blocked or release).
print(json.dumps({"outcome": "done", "note": f"Did: {item['title']}"}))
