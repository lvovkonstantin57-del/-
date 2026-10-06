"""Фоновые задачи сервера: ночной бэкап и уборка старых записей."""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete

from app import backup, db
from app.api.deps import SESSION_TTL
from app.config import config
from app.models import AuthToken, JobMark, Notification
from app.security import utcnow

log = logging.getLogger(__name__)

TICK_SECONDS = 60
NOTIFICATIONS_KEEP = timedelta(days=90)


def _at(now: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


async def maybe_backup(now: datetime) -> bool:
    """Раз в сутки в BACKUP_TIME — копия базы в BACKUP_DIR."""
    trigger = _at(now, backup.BACKUP_TIME)
    if not trigger <= now < trigger + timedelta(hours=1):
        return False
    key = f"backup:{now.date()}"
    async with db.session() as s:
        if await s.get(JobMark, key):
            return False
        s.add(JobMark(key=key))
        await s.commit()
    await backup.save_backup(now)
    return True


async def cleanup() -> None:
    now = utcnow()
    async with db.session() as s:
        await s.execute(delete(AuthToken).where(AuthToken.last_used_at < now - SESSION_TTL))
        await s.execute(delete(Notification).where(Notification.created_at < now - NOTIFICATIONS_KEEP))
        await s.execute(delete(JobMark).where(JobMark.created_at < now - timedelta(days=7)))
        await s.commit()


async def run() -> None:
    log.info("Фоновые задачи запущены")
    last_cleanup = None
    while True:
        now = datetime.now(config.tz)
        try:
            await maybe_backup(now)
            if last_cleanup != now.date():
                await cleanup()
                last_cleanup = now.date()
        except Exception:  # noqa: BLE001 — одна ошибка не должна останавливать цикл
            log.exception("Ошибка в фоновых задачах")
        await asyncio.sleep(TICK_SECONDS)
