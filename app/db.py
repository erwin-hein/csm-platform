from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """One session per request. Service functions never commit; the route does,
    so a mutation and its event row always land in the same transaction."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
