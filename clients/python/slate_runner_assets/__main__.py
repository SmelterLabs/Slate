"""Export packaged examples and adapters to a new user-selected directory."""

import argparse

# Python >=3.11 is required by this package; pre-3.7 compatibility is not supported.
import importlib.resources  # nosemgrep: python.lang.compatibility.python37.python37-compatibility-importlib2  # noqa: E501
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory (must not exist)")
    args = parser.parse_args(argv)
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"Refusing to overwrite existing directory: {args.output}")

    def export(source, target):
        for entry in source.iterdir():
            if entry.name.startswith("__") or entry.name.endswith(".pyc"):
                continue
            destination = target / entry.name
            if entry.is_dir():
                destination.mkdir()
                export(entry, destination)
            else:
                destination.write_bytes(entry.read_bytes())

    export(importlib.resources.files("slate_runner_assets"), args.output)
    print(f"Exported starter assets to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
