"""Unit tests for cost resolution service."""

import pytest
from sqlmodel import Session

from app.models.model_price import ModelPrice
from app.schemas.log_entry import (
    CanonicalMessage,
    CostPayload,
    LogEntryCreate,
    RequestPayload,
    ResponsePayload,
    UsagePayload,
)
from app.services.cost import resolve_cost


def _make_entry(**kwargs) -> LogEntryCreate:
    defaults = dict(
        provider="openai",
        model="gpt-4o",
        request=RequestPayload(messages=[CanonicalMessage(role="user", content="hi")]),
        response=ResponsePayload(message=CanonicalMessage(role="assistant", content="hello")),
        usage=UsagePayload(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    )
    defaults.update(kwargs)
    return LogEntryCreate(**defaults)


def test_client_cost_used_when_provided(pg_session: Session):
    entry = _make_entry(cost=CostPayload(total=0.05, currency="USD"))
    total, currency, source = resolve_cost(entry, pg_session)
    assert total == 0.05
    assert source == "client"


def test_server_computes_cost_from_pricing(pg_session: Session):
    price = ModelPrice(
        provider="openai",
        model="gpt-4o-test-unit",
        input_price_per_1k=0.005,
        output_price_per_1k=0.015,
    )
    pg_session.add(price)
    pg_session.commit()

    entry = _make_entry(model="gpt-4o-test-unit")  # no cost supplied
    total, currency, source = resolve_cost(entry, pg_session)
    # 100/1000 * 0.005 + 50/1000 * 0.015 = 0.0005 + 0.00075 = 0.00125
    assert total == pytest.approx(0.00125)
    assert source == "computed"


def test_returns_none_when_no_pricing(pg_session: Session):
    entry = _make_entry(model="unknown-model-xyz")
    total, currency, source = resolve_cost(entry, pg_session)
    assert total is None
    assert source == "computed"
