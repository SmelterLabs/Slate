"""The service layer: the only code that writes to the database.

Every mutation runs inside `core.apply_change()`, which records the event and publishes it.
REST routers and the MCP server call these functions; they never touch the session directly.
"""

from slate.service.core import Actor, apply_change

__all__ = ["Actor", "apply_change"]
