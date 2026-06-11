"""Statistics aggregation service."""

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.models.log_entry import LogEntry
from app.schemas.log_entry import DailyStats, ModelStats, StatsResponse
from app.utils.time import utcnow

# Allowed intervals for bucket granularity.
_ALLOWED_INTERVALS = frozenset({"5m", "1h", "1d", "1w", "1mo"})


def _bucket_key(dt: datetime, interval: str) -> str:
    """Truncate *dt* to the start of its *interval* bucket; return an ISO label.

    Rounds down:
      5m → YYYY-MM-DDTHH:MM:00Z (minute floored to multiple of 5)
      1h → YYYY-MM-DDTHH:00:00Z
      1d → YYYY-MM-DD
      1w → ISO week Monday (e.g. 2026-W23)
      1mo → YYYY-MM
    All calculations assume UTC datetimes.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    if interval == "5m":
        minute = (dt.minute // 5) * 5
        return dt.replace(minute=minute, second=0, microsecond=0).isoformat()
    elif interval == "1h":
        return dt.replace(minute=0, second=0, microsecond=0).isoformat()
    elif interval == "1d":
        return dt.strftime("%Y-%m-%d")
    elif interval == "1w":
        # Monday = 1
        monday = dt - timedelta(days=dt.weekday())
        return monday.strftime("%Y-%m-%d")
    elif interval == "1mo":
        return dt.strftime("%Y-%m")
    return dt.isoformat()


def _next_bucket(key: str, interval: str) -> str:
    """Given a bucket key, return the next bucket key after it."""
    if interval == "5m":
        dt = datetime.fromisoformat(key).replace(tzinfo=UTC)
        return _bucket_key(dt + timedelta(minutes=5), interval)
    elif interval == "1h":
        dt = datetime.fromisoformat(key).replace(tzinfo=UTC)
        return _bucket_key(dt + timedelta(hours=1), interval)
    elif interval == "1d":
        dt = datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=UTC)
        return _bucket_key(dt + timedelta(days=1), interval)
    elif interval == "1w":
        dt = datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=UTC)
        return _bucket_key(dt + timedelta(days=7), interval)
    elif interval == "1mo":
        y, m = map(int, key.split("-"))
        if m == 12:
            return f"{y+1}-01"
        return f"{y:04d}-{m+1:02d}"
    return key


def _iter_buckets(since: datetime, until: datetime, interval: str) -> list[str]:
    """Generate all bucket keys between *since* and *until* (inclusive).

    Used to fill empty buckets so charts render a contiguous axis.
    """
    keys: list[str] = []
    current = _bucket_key(since, interval)
    end = _bucket_key(until, interval)
    # Safety cap: avoid unbounded generation for huge ranges.
    max_buckets = 10_000
    while current <= end and len(keys) < max_buckets:
        keys.append(current)
        current = _next_bucket(current, interval)
    return keys


def get_stats(
    user_id: uuid.UUID,
    db: Session,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    interval: str = "1d",
) -> StatsResponse:
    """Aggregated stats with flexible time range and bucket granularity.

    Parameters
    ----------
    since : datetime | None
        Lower bound (inclusive).  None means "beginning of time".
    until : datetime | None
        Upper bound (inclusive).  Defaults to now.
    interval : {"5m","1h","1d","1w","1mo"}
        Bucket granularity for the time-series (``by_day``).
    """
    if interval not in _ALLOWED_INTERVALS:
        interval = "1d"

    if until is None:
        until = utcnow()

    # Build query
    q = select(LogEntry).where(LogEntry.user_id == user_id)
    if since is not None:
        q = q.where(LogEntry.created_at >= since)
    q = q.where(LogEntry.created_at <= until)
    q = q.order_by(LogEntry.created_at)  # ty:ignore[invalid-argument-type]

    entries = db.exec(q).all()

    # ---- Aggregate bucket-keyed stats ----
    def bucket_default() -> dict:
        return {
            "calls": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "tokens_saved": 0,
            "cost": 0.0,
        }
    bucket_map: dict[str, dict] = defaultdict(bucket_default)

    total_calls = len(entries)
    total_tokens = 0
    total_prompt_tokens = 0
    total_reasoning_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0
    total_tokens_saved = 0
    total_cost: float | None = 0.0

    for e in entries:
        key = _bucket_key(e.created_at, interval)
        b = bucket_map[key]
        b["calls"] += 1
        b["total_tokens"] += e.total_tokens
        b["reasoning_tokens"] += e.reasoning_tokens
        b["cache_read_tokens"] += e.cache_read_tokens
        b["cache_write_tokens"] += e.cache_write_tokens
        comp = (e.metadata_extra or {}).get("compression", {})
        ts = comp.get("tokens_saved", 0) if isinstance(comp, dict) else 0
        b["tokens_saved"] += int(ts)
        if e.cost_total:
            b["cost"] += e.cost_total

        total_tokens += e.total_tokens
        total_prompt_tokens += e.prompt_tokens
        total_reasoning_tokens += e.reasoning_tokens
        total_cache_read_tokens += e.cache_read_tokens
        total_cache_write_tokens += e.cache_write_tokens
        total_tokens_saved += int(ts)
        if e.cost_total:
            total_cost += e.cost_total

    if total_cost == 0.0 and total_calls == 0:
        total_cost = None

    # ---- Fill empty buckets for contiguous axis ----
    eff_since = since
    if eff_since is None and entries:
        eff_since = min(e.created_at for e in entries)
    if eff_since is not None:
        for key in _iter_buckets(eff_since, until, interval):
            if key not in bucket_map:
                bucket_map[key] = bucket_default()

    by_day = [
        DailyStats(
            date=key,
            calls=v["calls"],
            total_tokens=v["total_tokens"],
            reasoning_tokens=v["reasoning_tokens"],
            cache_read_tokens=v["cache_read_tokens"],
            cache_write_tokens=v["cache_write_tokens"],
            tokens_saved=v["tokens_saved"],
            cost=round(v["cost"], 8) if v["cost"] else None,
        )
        for key, v in sorted(bucket_map.items())
    ]

    # ---- By model ----
    def model_default() -> dict:
        return {
            "calls": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "tokens_saved": 0,
            "cost": 0.0,
        }
    model_map: dict[str, dict] = defaultdict(model_default)

    for e in entries:
        m = model_map[e.model]
        m["calls"] += 1
        m["total_tokens"] += e.total_tokens
        m["reasoning_tokens"] += e.reasoning_tokens
        m["cache_read_tokens"] += e.cache_read_tokens
        m["cache_write_tokens"] += e.cache_write_tokens
        comp = (e.metadata_extra or {}).get("compression", {})
        ts = comp.get("tokens_saved", 0) if isinstance(comp, dict) else 0
        m["tokens_saved"] += int(ts)
        if e.cost_total:
            m["cost"] += e.cost_total

    by_model = [
        ModelStats(
            model=model,
            calls=v["calls"],
            total_tokens=v["total_tokens"],
            reasoning_tokens=v["reasoning_tokens"],
            cache_read_tokens=v["cache_read_tokens"],
            cache_write_tokens=v["cache_write_tokens"],
            tokens_saved=v["tokens_saved"],
            cost=round(v["cost"], 8) if v["cost"] else None,
        )
        for model, v in sorted(model_map.items(), key=lambda x: -x[1]["calls"])
    ]

    return StatsResponse(
        total_calls=total_calls,
        total_tokens=total_tokens,
        total_prompt_tokens=total_prompt_tokens,
        total_reasoning_tokens=total_reasoning_tokens,
        total_cache_read_tokens=total_cache_read_tokens,
        total_cache_write_tokens=total_cache_write_tokens,
        total_tokens_saved=total_tokens_saved,
        total_cost=total_cost,
        interval=interval,
        since=since,
        until=until,
        by_day=by_day,
        by_model=by_model,
    )
