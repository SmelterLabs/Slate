"""Write Docker revision metadata using the server's existing identity rules."""

import argparse
import json
import os
import runpy
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rev", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args()
    root = args.source.resolve()
    reader = runpy.run_path(str(root / "src/slate/build.py"))["read_build"]
    # Never pick up an unrelated parent repository when building an extracted archive.
    build = reader(
        env={"SLATE_REV": args.rev, "SLATE_COMMIT": args.commit},
        file=Path(os.devnull),  # do not recycle stale generated checkout metadata
        use_git=(root / ".git").exists(),
    )
    if args.commit and not args.rev:
        build = {"rev": "dev", "commit": args.commit}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
