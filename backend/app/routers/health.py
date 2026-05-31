"""Health check endpoints."""

from fastapi import APIRouter, Depends
from sqlmodel import Session, text

from app.db import get_session

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    """Liveness probe — always returns 200 if the app is running."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(db: Session = Depends(get_session)) -> dict:
    """Readiness probe — checks DB connectivity."""
    db.exec(text("SELECT 1"))
    return {"status": "ok", "db": "connected"}
