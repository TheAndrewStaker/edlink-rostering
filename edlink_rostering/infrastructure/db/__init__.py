"""Database layer.

SQLAlchemy 2.x async engine and session, plus ORM models for canonical,
snapshot, audit, and operational tables. Alembic owns the schema; the models
here are runtime types that read and write against migrated tables.
"""

from edlink_rostering.infrastructure.db.engine import (
    app_engine,
    app_session,
    app_session_factory,
    ops_engine,
    ops_session,
    ops_session_factory,
)
from edlink_rostering.infrastructure.db.models import Base

__all__ = [
    "Base",
    "app_engine",
    "app_session",
    "app_session_factory",
    "ops_engine",
    "ops_session",
    "ops_session_factory",
]
