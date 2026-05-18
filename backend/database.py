from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from backend.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in (
            "ALTER TABLE contents ADD COLUMN scheduled_at DATETIME",
            "ALTER TABLE contents ADD COLUMN image_method TEXT DEFAULT 'text_on_bg'",
        ):
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass  # Column already exists


async def get_db():
    async with async_session() as session:
        yield session
