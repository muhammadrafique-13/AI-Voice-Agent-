"""SQLAlchemy engine/session wiring.

Serverless note: on Vercel each invocation may land on a cold container, so we use
NullPool for Postgres. Holding a pool across freeze/thaw cycles yields dead sockets.
"""
import logging
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

Base = declarative_base()

if settings.is_sqlite:
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        future=True,
    )
else:
    engine = create_engine(
        settings.database_url,
        poolclass=NullPool,
        pool_pre_ping=True,
        future=True,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

_schema_ready = False


def init_db() -> None:
    """Idempotent schema creation.

    Trade-off: a real deployment would use Alembic migrations. For a single-table
    assessment app, create_all keeps the deploy to zero manual steps.
    """
    global _schema_ready
    if _schema_ready:
        return
    from app import models  # noqa: F401  (registers mappers before create_all)

    Base.metadata.create_all(bind=engine)
    _schema_ready = True
    logger.info("database schema ready (%s)", engine.url.get_backend_name())


def get_db():
    """FastAPI dependency yielding a request-scoped session."""
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope():
    """Standalone session for scripts and the Vapi webhook."""
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
