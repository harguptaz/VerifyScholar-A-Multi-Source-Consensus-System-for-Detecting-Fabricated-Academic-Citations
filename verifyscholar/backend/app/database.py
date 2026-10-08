"""SQLite persistence: jobs, HTTP cache and the hash-chained transparency log."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import JSON, Float, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|error
    stage: Mapped[str] = mapped_column(String(200), default="")
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)


class CacheRow(Base):
    __tablename__ = "http_cache"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class LogRow(Base):
    """One row of the transparency log. `entry_hash` chains to the previous row of the same job."""

    __tablename__ = "transparency_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[str] = mapped_column(String(40))
    citation_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(80))
    query: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    verdict: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))


class Database:
    """Thin wrapper so tests can point the app at a throw-away database."""

    def __init__(self) -> None:
        self.engine: Optional[Engine] = None
        self._maker: Optional[sessionmaker] = None

    def configure(self, url: Optional[str] = None) -> None:
        url = url or get_settings().database_url
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if url.endswith(":memory:") or url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        elif url.startswith("sqlite:///"):
            path = url.replace("sqlite:///", "", 1)
            folder = os.path.dirname(os.path.abspath(path))
            os.makedirs(folder, exist_ok=True)
        engine = create_engine(url, **kwargs)

        if url.startswith("sqlite"):
            @event.listens_for(engine, "connect")
            def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()

        Base.metadata.create_all(engine)
        self.engine = engine
        self._maker = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        if self._maker is None:
            self.configure()
        assert self._maker is not None
        s = self._maker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


db = Database()
