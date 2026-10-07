"""Удалить все отметки на парах — журнал посещаемости целиком (пары с кодом и ручные отметки).

    python -m app.wipe_attendance          — показать, сколько записей будет удалено
    python -m app.wipe_attendance --yes    — удалить

Перед удалением копия базы сохраняется в папку бэкапов: если удалили зря — её можно вернуть.
На сервере: docker exec raspisanie-app-1 python -m app.wipe_attendance --yes
"""

import asyncio
import logging
import sys
from datetime import datetime

from sqlalchemy import delete, func, select

from app import backup, db
from app.config import config
from app.models import AttendanceGroup, AttendanceMark, AttendanceSession

log = logging.getLogger(__name__)


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


# Один раз при запуске сервера убираем пробные отметки, сделанные до этой версии
WIPED_ONCE = "attendance_wiped_v1"


async def wipe_once(backup_dir: str | None = None) -> int | None:
    """Удаляет журнал посещаемости при первом запуске новой версии и запоминает это навсегда.
    Вернёт число удалённых пар или None, если уже делали."""
    async with db.session() as s:
        if await db.get_setting(s, WIPED_ONCE):
            return None
    sessions, _ = await count()
    if sessions:
        sessions, marks, path = await wipe(backup_dir)
        log.warning("Журнал посещаемости очищен: %d пар, %d отметок; копия базы — %s", sessions, marks, path)
    async with db.session() as s:
        await db.set_setting(s, WIPED_ONCE, True)
        await s.commit()
    return sessions


async def run(confirm: bool) -> None:
    await db.init_db()
    if not confirm:
        sessions, marks = await count()
        print(f"В журнале — пар с отметкой: {sessions}, отметок студентов: {marks}.")
        print("Чтобы удалить их, запусти с --yes")
        return
    sessions, marks, path = await wipe()
    print(f"Удалено — пар с отметкой: {sessions}, отметок студентов: {marks}.")
    print(f"Копия базы до удаления: {path}")


if __name__ == "__main__":
    db.setup()
    asyncio.run(run("--yes" in sys.argv[1:]))
