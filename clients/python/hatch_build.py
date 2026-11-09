"""Package canonical adapters; sdists retain the exact same bytes for offline rebuilds."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        sources = {
            "hermes/run_task.py": "../../integrations/hermes/run_task.py",
            "hermes/run_task_cli.py": "../../integrations/hermes/run_task_cli.py",
            "runner-example/run_task.py": "../../integrations/runner-example/run_task.py",
            "run_one_example.py": "run_one_example.py",
        }
        for destination, relative in sources.items():
            resource = "slate_runner_assets/" + destination
            source = root / relative
            if not source.is_file():  # rebuilding a self-contained source distribution
                source = root / resource
            if not source.is_file():
                raise FileNotFoundError(f"Required runner resource is missing: {resource}")
            build_data["force_include"][str(source.resolve())] = resource
