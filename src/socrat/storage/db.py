"""Async engine and schema initialization."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from socrat.config import Settings, get_settings

from .models import Base


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    config = settings or get_settings()
    return create_async_engine(config.database_url, pool_pre_ping=True)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
