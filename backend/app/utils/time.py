"""Shared time utilities — timezone-aware UTC helpers.

Sqlmodel columns use TIMESTAMP WITHOUT TIME ZONE by default (naive datetime).
The helper returns a naive UTC datetime so existing DB columns stay consistent
while being explicit about UTC.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current UTC datetime as a naive (tzinfo=None) object.

    Replaces the deprecated ``datetime.utcnow()`` with an equivalent
    timezone-aware construction followed by tzinfo removal, matching the
    existing TIMESTAMP WITHOUT TIME ZONE column semantics.
    """
    return datetime.now(UTC).replace(tzinfo=None)
