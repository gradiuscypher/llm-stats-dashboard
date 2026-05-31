"""Cost computation service.

Priority: use client-supplied cost if provided; otherwise compute from the
model pricing table. Falls back to None if no pricing data exists.
"""

from sqlmodel import Session, select

from app.models.model_price import ModelPrice
from app.schemas.log_entry import LogEntryCreate


def resolve_cost(entry: LogEntryCreate, db: Session) -> tuple[float | None, str, str]:
    """
    Returns (cost_total, currency, cost_source).
    cost_source is 'client' if the client supplied cost, 'computed' otherwise.
    """
    if entry.cost is not None:
        return entry.cost.total, entry.cost.currency, "client"

    # Try to compute from pricing table
    price = db.exec(
        select(ModelPrice)
        .where(ModelPrice.provider == entry.provider, ModelPrice.model == entry.model)
        .order_by(ModelPrice.effective_at.desc())  # type: ignore[arg-type]
    ).first()

    if price is None:
        return None, "USD", "computed"

    prompt_cost = (entry.usage.prompt_tokens / 1000) * price.input_price_per_1k
    completion_cost = (entry.usage.completion_tokens / 1000) * price.output_price_per_1k
    total = round(prompt_cost + completion_cost, 8)
    return total, price.currency, "computed"
