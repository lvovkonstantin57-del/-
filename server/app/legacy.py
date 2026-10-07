"""Перенос данных из базы Telegram-бота в базу приложения.

    python -m app.legacy /путь/к/bot.db

Переносятся списки студентов, группы и кураторы, расписание, настройки семестра и журнал
посещаемости. Аккаунты Telegram не переносятся: каждый регистрируется в приложении заново,
а староста добавляет студентов по личному коду (или студент вводит код группы) — если ФИО
совпадает со строкой из списка, аккаунт привяжется к ней вместе с историей посещаемости.
"""

import asyncio
import sqlite3
import sys

from app import db
from app.config import config

# Таблица → колонки, которые есть в обеих базах
TABLES = {
    "students": ["id", "full_name", "name_key", "group_name", "email"],
    "group_info": ["group_name", "curator", "curator_contact"],
    "lessons": ["id", "group_name", "weekday", "week", "pair_num", "start_time", "end_time", "subject", "kind",
                "room", "teacher", "half"],
    "settings": ["key", "value"],
    "attendance_sessions": ["id", "teacher_name", "subject", "lesson_date", "start_time", "end_time", "half",
                            "created_at", "closed_at"],
    "attendance_groups": ["session_id", "group_name"],
    "attendance_marks": ["session_id", "student_id", "marked_at", "method"],
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def copy_data(old_path: str, new_path: str) -> dict[str, int]:
    src = sqlite3.connect(f"file:{old_path}?mode=ro", uri=True)
    dst = sqlite3.connect(new_path)
    try:
        if "tg_id" not in _columns(src, "users"):
            raise SystemExit(f"{old_path} — не база Telegram-бота (нет users.tg_id)")
        if dst.execute("SELECT COUNT(*) FROM students").fetchone()[0] or \
                dst.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]:
            raise SystemExit(f"В {new_path} уже есть данные — перенос делается в пустую базу")
        counts = {}
        with dst:
            for table, wanted in TABLES.items():
                have = _columns(src, table)
                if not have:
                    counts[table] = 0
                    continue
                cols = [c for c in wanted if c in have]
                rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
                placeholders = ", ".join("?" for _ in cols)
                dst.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", rows)
                counts[table] = len(rows)
        return counts
    finally:
        src.close()
        dst.close()


async def _create_schema() -> None:
    db.setup()
    await db.init_db()
    await db.engine.dispose()


async def _keep_journal() -> None:
    """Перенесённый журнал посещаемости — настоящий: разовая очистка пробных отметок его не трогает."""
    from app.wipe_attendance import WIPED_ONCE
    db.setup()
    async with db.session() as s:
        await db.set_setting(s, WIPED_ONCE, True)
        await s.commit()
    await db.engine.dispose()


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit(__doc__)
    asyncio.run(_create_schema())
    counts = copy_data(argv[1], config.db_path)
    asyncio.run(_keep_journal())
    print(f"Готово, данные перенесены в {config.db_path}:")
    for table, n in counts.items():
        print(f"  {table}: {n}")


if __name__ == "__main__":
    main(sys.argv)
