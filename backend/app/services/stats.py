"""Statistics aggregation service."""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.models.log_entry import LogEntry
from app.schemas.log_entry import DailyStats, ModelStats, StatsResponse


def get_stats(user_id: uuid.UUID, db: Session, days: int = 30) -> StatsResponse:
    since = datetime.utcnow() - timedelta(days=days)
    entries = db.exec(
        select(LogEntry)
        .where(LogEntry.user_id == user_id, LogEntry.created_at >= since)
        .order_by(LogEntry.created_at)  # type: ignore[arg-type]
    ).all()

    total_calls = len(entries)
    total_tokens = sum(e.total_tokens for e in entries)
    costs = [e.cost_total for e in entries if e.cost_total is not None]
    total_cost: float | None = round(sum(costs), 8) if costs else None

    # By day
    day_map: dict[str, dict] = defaultdict(lambda: {"calls": 0, "total_tokens": 0, "cost": 0.0})
    for e in entries:
        day = e.created_at.strftime("%Y-%m-%d")
        day_map[day]["calls"] += 1
        day_map[day]["total_tokens"] += e.total_tokens
        if e.cost_total:
            day_map[day]["cost"] += e.cost_total

    by_day = [
        DailyStats(
            date=day,
            calls=v["calls"],
            total_tokens=v["total_tokens"],
            cost=round(v["cost"], 8) if v["cost"] else None,
        )
        for day, v in sorted(day_map.items())
    ]

    # By model
    model_map: dict[str, dict] = defaultdict(lambda: {"calls": 0, "total_tokens": 0, "cost": 0.0})
    for e in entries:
        model_map[e.model]["calls"] += 1
        model_map[e.model]["total_tokens"] += e.total_tokens
        if e.cost_total:
            model_map[e.model]["cost"] += e.cost_total

    by_model = [
        ModelStats(
            model=model,
            calls=v["calls"],
            total_tokens=v["total_tokens"],
            cost=round(v["cost"], 8) if v["cost"] else None,
        )
        for model, v in sorted(model_map.items(), key=lambda x: -x[1]["calls"])
    ]

    return StatsResponse(
        total_calls=total_calls,
        total_tokens=total_tokens,
        total_cost=total_cost,
        by_day=by_day,
        by_model=by_model,
    )
