"""Бэкап базы: согласованная копия SQLite. Главный админ скачивает её в приложении,
а каждую ночь копия сама сохраняется в BACKUP_DIR."""

import asyncio
import logging
import os
import sqlite3
import tempfile
from datetime import datetime

from app import db
from app.config import config

log = logging.getLogger(__name__)

BACKUP_TIME = "04:00"  # по Москве, когда все спят
KEEP = 14              # сколько ночных копий хранить


def _copy_sqlite(path: str) -> bytes:
    # backup() делает целостную копию даже во время записи в базу
    with tempfile.TemporaryDirectory() as tmp:
        dst_path = os.path.join(tmp, "backup.db")
        src = sqlite3.connect(path)
        dst = sqlite3.connect(dst_path)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        with open(dst_path, "rb") as f:
            return f.read()


async def make_backup() -> bytes:
    return await asyncio.to_thread(_copy_sqlite, db.engine.url.database)


def backup_name(now: datetime) -> str:
    return f"schedule-backup-{now:%Y-%m-%d_%H%M}.db"


async def save_backup(now: datetime, directory: str | None = None) -> str:
    """Кладёт копию в папку бэкапов и удаляет старые. Возвращает путь."""
    directory = directory or config.backup_dir
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, backup_name(now))
    data = await make_backup()
    with open(path, "wb") as f:
        f.write(data)
    old = sorted(n for n in os.listdir(directory) if n.startswith("schedule-backup-") and n.endswith(".db"))
    for name in old[:-KEEP]:
        os.remove(os.path.join(directory, name))
    log.info("Бэкап сохранён: %s (%d КБ)", path, len(data) // 1024)
    return path
