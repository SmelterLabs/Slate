"""Package the runner's assets; sdists retain the exact same bytes for offline rebuilds.

The reference adapters come from `integrations/` (optional by AGENTS.md rule 5): when that
folder is gone and no source distribution carries them, the package is built without them, and
`slate-connect` says so if one is chosen. `run_one_example.py` lives here and is always included.
"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

ADAPTERS = {
    "hermes/run_task.py": "../../integrations/hermes/run_task.py",
    "hermes/run_task_cli.py": "../../integrations/hermes/run_task_cli.py",
    "runner-example/run_task.py": "../../integrations/runner-example/run_task.py",
}
REQUIRED = {"run_one_example.py": "run_one_example.py"}


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        for destination, relative in {**ADAPTERS, **REQUIRED}.items():
            resource = "slate_runner_assets/" + destination
            source = root / relative
            if not source.is_file():  # rebuilding a self-contained source distribution
                source = root / resource
            if not source.is_file():
                if destination in ADAPTERS:
                    continue  # built without integrations/: no bundled adapter
                raise FileNotFoundError(f"Required runner resource is missing: {resource}")
            build_data["force_include"][str(source.resolve())] = resource
