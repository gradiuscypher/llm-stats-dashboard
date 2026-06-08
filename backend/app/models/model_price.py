"""Model pricing table for server-side cost computation."""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.utils.time import utcnow


class ModelPrice(SQLModel, table=True):
    __tablename__ = "model_prices"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    provider: str = Field(max_length=64, index=True)
    model: str = Field(max_length=128, index=True)
    input_price_per_1k: float  # USD per 1k prompt tokens
    output_price_per_1k: float  # USD per 1k completion tokens
    currency: str = Field(default="USD", max_length=8)
    effective_at: datetime = Field(default_factory=utcnow)
