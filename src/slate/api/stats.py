"""/stats: how fast Slate is answering right now, and how big it is."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from slate import fastlane
from slate.api.auth import DbSession, ReadAuth
from slate.models import Account, Event, Item
from slate.service import core, health, item_cache
from slate.service.bus import bus
from slate.settings import settings

router = APIRouter(tags=["stats"])


class RouteLatency(BaseModel):
    method: str
    route: str
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


class WriteLock(BaseModel):
    samples: int
    wait_p50_ms: float | None = None
    wait_p95_ms: float | None = None
    wait_max_ms: float | None = None
    held_p50_ms: float | None = None
    held_p95_ms: float | None = None
    held_max_ms: float | None = None


class ItemCacheStats(BaseModel):
    items: int
    hits: int
    misses: int


class StatsOut(BaseModel):
    uptime_s: float
    items: int
    open_items: int
    events: int
    accounts: int
    live_streams: int
    db_bytes: int | None
    db_path: str = Field(description='The SQLite file (":memory:" in tests)')
    cursor: str
    samples_per_route: int
    routes: list[RouteLatency]
    write_lock: WriteLock
    item_cache: ItemCacheStats


@router.get("/stats", response_model=StatsOut)
def get_stats(auth: ReadAuth, session: DbSession) -> dict:
    """Request latency per route (time to first byte, last 1,000 requests of each route, since
    the process started), plus counts. Every API response also carries `Server-Timing`.
    `write_lock`: how long the last 2,000 changes waited for the single writer and held it.
    `item_cache`: whole-item JSON kept for `GET /items` and `/today`. Owners and admins, like
    `/health`: it names the database file and counts every account."""
    health.require_viewer(session, auth.account)
    lat = fastlane.latency()
    db_bytes = None
    path = str(settings.database_path)
    if path != ":memory:":
        db_bytes = sum(os.path.getsize(p) for p in (path, f"{path}-wal") if os.path.exists(p))
    return {
        "uptime_s": round(time.time() - lat["since"], 1),
        "items": session.scalar(select(func.count()).select_from(Item)) or 0,
        "open_items": session.scalar(
            select(func.count())
            .select_from(Item)
            .where(Item.completed_at.is_(None), Item.archived_at.is_(None))
        )
        or 0,
        "events": session.scalar(select(func.count()).select_from(Event)) or 0,
        "accounts": session.scalar(select(func.count()).select_from(Account)) or 0,
        "live_streams": bus.subscriber_count,
        "db_bytes": db_bytes,
        "db_path": path,
        "cursor": session.scalar(select(func.max(Event.id))) or "0",
        "samples_per_route": lat["samples_per_route"],
        "routes": lat["routes"],
        "write_lock": core.write_lock_stats(),
        "item_cache": item_cache.cache.stats(),
    }
