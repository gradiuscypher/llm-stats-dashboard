"""Seed the model pricing table with common provider prices."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import Session, select

from app.db import engine
from app.models.model_price import ModelPrice

PRICING = [
    ("openai", "gpt-4o", 0.005, 0.015),
    ("openai", "gpt-4o-mini", 0.00015, 0.0006),
    ("openai", "gpt-4-turbo", 0.01, 0.03),
    ("openai", "gpt-3.5-turbo", 0.0005, 0.0015),
    ("anthropic", "claude-3-5-sonnet-20241022", 0.003, 0.015),
    ("anthropic", "claude-3-5-haiku-20241022", 0.0008, 0.004),
    ("anthropic", "claude-3-opus-20240229", 0.015, 0.075),
    ("google", "gemini-1.5-pro", 0.00125, 0.005),
    ("google", "gemini-1.5-flash", 0.000075, 0.0003),
    ("mistral", "mistral-large-latest", 0.002, 0.006),
]


def seed() -> None:
    with Session(engine) as db:
        added = 0
        for provider, model, inp, out in PRICING:
            existing = db.exec(
                select(ModelPrice).where(
                    ModelPrice.provider == provider,
                    ModelPrice.model == model,
                )
            ).first()
            if not existing:
                db.add(
                    ModelPrice(
                        provider=provider,
                        model=model,
                        input_price_per_1k=inp,
                        output_price_per_1k=out,
                    )
                )
                added += 1
        db.commit()
    print(f"Seeded {added} new pricing entries ({len(PRICING) - added} already existed).")


if __name__ == "__main__":
    seed()
