"""Service-layer errors. The API maps each to an HTTP status in `slate.api.errors`."""

from __future__ import annotations


class ServiceError(Exception):
    """Base class; `str(err)` is safe to show to the caller."""


class NotFound(ServiceError):
    pass


class Conflict(ServiceError):
    pass


class ExternalRefTaken(Conflict):
    """`external_ref` is unique: another item already carries it. The API adds `existing_id`
    and `existing_state` (open, closed, archived, trashed) to the 409 body so a filer finds
    the card it already made, and reopens it rather than filing a new one."""

    ADVICE = {
        "closed": "which is closed: reopen",
        "archived": "which is archived: unarchive and reopen",
        "trashed": "which is in the trash: restore and reopen",
    }

    def __init__(self, ref: str, existing_id: str, existing_state: str = "open") -> None:
        advice = self.ADVICE.get(existing_state)
        tail = f", {advice} that card instead of filing a new one" if advice else ""
        super().__init__(f"external_ref already used by {existing_id}{tail}")
        self.ref = ref
        self.existing_id = existing_id
        self.existing_state = existing_state


class Invalid(ServiceError):
    pass


class Forbidden(ServiceError):
    pass


class Gone(ServiceError):
    pass


class Unavailable(ServiceError):
    """A feature is switched off by configuration (e.g. webhooks without SLATE_SECRET_KEY)."""
