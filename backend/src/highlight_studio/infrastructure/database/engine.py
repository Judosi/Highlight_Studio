from __future__ import annotations

from contextlib import contextmanager
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session

from ...core.settings import AUTO_CREATE_DATABASE, DATABASE_URL
from .models import Base


def _engine_options(url: str) -> dict:
    options = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    else:
        options.update({"pool_size": 10, "max_overflow": 20, "pool_recycle": 1800})
    return options


engine = create_engine(DATABASE_URL, **_engine_options(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def database_backend() -> str:
    """Return the active SQLAlchemy dialect for diagnostics and the web UI."""
    return str(engine.dialect.name)


def init_database() -> None:
    if AUTO_CREATE_DATABASE:
        # Desktop and isolated test databases remain zero-configuration.
        Base.metadata.create_all(bind=engine)
        return
    # PostgreSQL production must be migrated explicitly. Silently calling
    # create_all would bypass Alembic and make future upgrades unreliable.
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    required = {"users", "auth_sessions", "projects", "project_memberships", "jobs"}
    existing = set(inspect(engine).get_table_names())
    missing = sorted(required - existing)
    if missing:
        raise RuntimeError(
            "Database schema is not initialized. Run `python -m alembic -c alembic.ini upgrade head` before starting web mode. "
            f"Missing tables: {', '.join(missing)}"
        )


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
