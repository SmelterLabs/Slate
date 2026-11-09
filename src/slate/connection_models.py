"""Generic local connection identity; independent of any work execution generation."""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from slate.db import Base
from slate.ids import new_id
from slate.models import UTCDateTime, utcnow


class AgentConnection(Base):
    __tablename__ = "agent_connections"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    issuer_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    issuer_token_id: Mapped[str] = mapped_column(ForeignKey("tokens.id"))
    token_id: Mapped[str | None] = mapped_column(ForeignKey("tokens.id"), unique=True)
    label: Mapped[str] = mapped_column(String(80))
    list_ids: Mapped[list] = mapped_column(JSON)
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    exchanged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    check: Mapped[str] = mapped_column(String(24), default="connected")
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    device_name: Mapped[str | None] = mapped_column(String(80))
    adapter: Mapped[str | None] = mapped_column(String(80))
    version: Mapped[str | None] = mapped_column(String(40))
