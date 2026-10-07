"""Удалить все отметки на парах — журнал посещаемости целиком (пары с кодом и ручные отметки).

    python -m app.wipe_attendance          — показать, сколько записей будет удалено
    python -m app.wipe_attendance --yes    — удалить

Перед удалением копия базы сохраняется в папку бэкапов: если удалили зря — её можно вернуть.
На сервере: docker exec raspisanie-app-1 python -m app.wipe_attendance --yes
"""

import asyncio
import sys
from datetime import datetime

from sqlalchemy import delete, func, select

from app import backup, db
from app.config import config
from app.models import AttendanceGroup, AttendanceMark, AttendanceSession


async def count() -> tuple[int, int]:
    async with db.session() as s:
        sessions = await s.scalar(select(func.count()).select_from(AttendanceSession))
        marks = await s.scalar(select(func.count()).select_from(AttendanceMark))
    return sessions, marks


async def wipe(backup_dir: str | None = None) -> tuple[int, int, str]:
    """Удаляет отметки, вернёт (пар, отметок, путь к бэкапу)."""
    sessions, marks = await count()
    path = await backup.save_backup(datetime.now(config.tz), backup_dir)
    async with db.session() as s:
        await s.execute(delete(AttendanceMark))
        await s.execute(delete(AttendanceGroup))
        await s.execute(delete(AttendanceSession))
        await s.commit()
    return sessions, marks, path


async def run(confirm: bool) -> None:
    await db.init_db()
    if not confirm:
        sessions, marks = await count()
        print(f"В журнале {sessions} пар с отметкой и {marks} отметок студентов.")
        print("Чтобы удалить их, запусти с --yes")
        return
    sessions, marks, path = await wipe()
    print(f"Удалено: {sessions} пар с отметкой и {marks} отметок студентов.")
    print(f"Копия базы до удаления: {path}")


if __name__ == "__main__":
    asyncio.run(run("--yes" in sys.argv[1:]))
