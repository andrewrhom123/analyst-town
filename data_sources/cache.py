"""DataCache helpers shared by every fetcher."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.db import session_scope
from database.models import DataCache

logger = logging.getLogger(__name__)


def _age_seconds(fetched: datetime) -> float:
    if fetched.tzinfo is None:  # SQLite drops tzinfo; we always store UTC
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched).total_seconds()


def get_cached(data_type: str, key: str, ttl_seconds: int | None) -> dict | None:
    """Return cached data if younger than ttl_seconds (None = any age)."""
    with session_scope() as s:
        row = s.scalar(select(DataCache).where(DataCache.data_type == data_type, DataCache.ticker == key))
        if row is None:
            return None
        if ttl_seconds is not None and _age_seconds(row.fetch_date) > ttl_seconds:
            return None
        return row.raw_data


def set_cached(data_type: str, key: str, data: dict) -> None:
    now = datetime.now(timezone.utc)
    try:
        with session_scope() as s:
            row = s.scalar(select(DataCache).where(DataCache.data_type == data_type, DataCache.ticker == key))
            if row is None:
                s.add(DataCache(data_type=data_type, ticker=key, raw_data=data, fetch_date=now))
            else:
                row.raw_data = data
                row.fetch_date = now
    except IntegrityError:
        # Another thread inserted the same key first; its copy is just as fresh.
        logger.debug("cache race on %s/%s", data_type, key)


def cached_fetch(data_type: str, key: str, ttl_seconds: int, fetch) -> dict:
    """Serve from cache when fresh; otherwise fetch and store. On fetch failure fall back to stale data."""
    fresh = get_cached(data_type, key, ttl_seconds)
    if fresh is not None:
        return fresh
    try:
        data = fetch()
    except Exception as exc:
        stale = get_cached(data_type, key, None)
        if stale is not None:
            logger.warning("%s fetch for %s failed (%s); using stale cache", data_type, key, exc)
            return {**stale, "_stale": True}
        raise
    set_cached(data_type, key, data)
    return data
