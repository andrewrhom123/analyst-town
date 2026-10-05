"""Engine / session management. Works with SQLite locally and PostgreSQL on Railway."""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from utils.config import DATABASE_URL

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    # SQLite connections are used from the scheduler thread and the request threads.
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from database import models  # noqa: F401  (registers tables on Base.metadata)

    models.Base.metadata.create_all(bind=engine)
    _add_missing_columns(models.Base.metadata)


def _add_missing_columns(metadata) -> None:
    """Lightweight migration: add nullable columns introduced after a table was first created."""
    inspector = inspect(engine)
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}"
            with engine.begin() as conn:
                conn.execute(text(ddl))


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts, agents and scheduler jobs."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
