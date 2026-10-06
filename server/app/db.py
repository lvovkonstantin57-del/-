import json
import os
from datetime import date

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import DEFAULT_BELLS, config
from app.models import Base, Setting

engine = None
Session: async_sessionmaker[AsyncSession] | None = None


class LegacyDatabaseError(RuntimeError):
    """По DB_PATH лежит база Telegram-бота: её нужно перенести, а не открывать."""


def setup(db_url: str | None = None) -> None:
    global engine, Session
    if db_url is None:
        os.makedirs(os.path.dirname(os.path.abspath(config.db_path)), exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{config.db_path}"
    engine = create_async_engine(db_url)

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    Session = async_sessionmaker(engine, expire_on_commit=False)


# Колонки, которые появятся после первого запуска. create_all создаёт только новые
# таблицы, а в существующие колонки не добавляет — делаем это сами.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "lessons": {"weeks": "VARCHAR(200)"},
    "users": {"photo": "VARCHAR(40)"},
}


def _check_not_legacy(conn) -> None:
    insp = inspect(conn)
    if "users" in insp.get_table_names() and "tg_id" in {c["name"] for c in insp.get_columns("users")}:
        raise LegacyDatabaseError(
            "Это база Telegram-бота. Перенеси данные в новую базу: python -m app.legacy <старая.db>"
        )


def _add_missing_columns(conn) -> None:
    insp = inspect(conn)
    for table, columns in ADDED_COLUMNS.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in columns.items():
            if name not in have:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_check_not_legacy)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def session() -> AsyncSession:
    return Session()


# --- настройки -------------------------------------------------------------

async def get_setting(s: AsyncSession, key: str, default=None):
    row = await s.get(Setting, key)
    return json.loads(row.value) if row else default


async def set_setting(s: AsyncSession, key: str, value) -> None:
    row = await s.get(Setting, key)
    if row:
        row.value = json.dumps(value, ensure_ascii=False)
    else:
        s.add(Setting(key=key, value=json.dumps(value, ensure_ascii=False)))


async def get_semester_start(s: AsyncSession) -> date:
    value = await get_setting(s, "semester_start")
    return date.fromisoformat(value) if value else config.default_semester_start


async def get_semester_end(s: AsyncSession) -> date | None:
    """Дата начала сессии (первый день после учебных недель); None — не задана."""
    value = await get_setting(s, "semester_end")
    return date.fromisoformat(value) if value else None


async def get_lk_url(s: AsyncSession) -> str | None:
    """Ссылка на личный кабинет университета для кнопки в профиле."""
    return await get_setting(s, "lk_url") or None


async def get_bells(s: AsyncSession) -> list[tuple[str, str]]:
    value = await get_setting(s, "bells")
    return [tuple(b) for b in value] if value else list(DEFAULT_BELLS)


async def list_groups(s: AsyncSession) -> list[str]:
    """Все группы: из списков студентов, из расписания и созданные админом."""
    from app.models import GroupInfo, Lesson, Student

    names = set((await s.scalars(select(Student.group_name).distinct())).all())
    names |= set((await s.scalars(select(Lesson.group_name).distinct())).all())
    names |= set((await s.scalars(select(GroupInfo.group_name))).all())
    return sorted(names)
