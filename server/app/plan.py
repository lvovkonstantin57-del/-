"""План уведомлений для телефона: напоминания перед парами и ежедневная сводка.

Телефон сам ставит их в системный планировщик — приходят даже без сети и без сервера push.
Приложение обновляет план при каждом открытии и после изменения настроек.
"""

import zlib
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app import attendance, schedule
from app.config import config
from app.models import User

DAYS_AHEAD = 10
# iOS держит не больше 64 запланированных уведомлений на приложение
MAX_ITEMS = 60


def _at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=config.tz)


def notification_id(key: str) -> int:
    """Стабильный номер уведомления (int32): тот же для той же пары — без дублей при обновлении плана."""
    return zlib.crc32(key.encode()) & 0x7FFFFFFF


def _minutes(n: int) -> str:
    return f"{n} мин" if n < 60 or n % 60 else f"{n // 60} ч"


def _item(key: str, at: datetime, title: str, body: str, kind: str) -> dict:
    return {"id": notification_id(key), "at": at.isoformat(), "title": title, "body": body, "kind": kind}


def _student_line(l: schedule.Slot, show_half: bool) -> str:
    parts = [f"{l.start_time}–{l.end_time}"]
    if l.room:
        old = l.was.get("room")
        parts.append(schedule.room_text(l.room) + (f" (вместо {old})" if old else ""))
    if l.kind:
        parts.append(l.kind)
    if l.teacher:
        parts.append(l.teacher)
    if show_half and l.half:
        parts.append(f"{l.half}-я половина")
    return " · ".join(parts)


def _teacher_line(item: dict) -> str:
    parts = [f"{item['start']}–{item['end']}"]
    if item["room"]:
        parts.append(schedule.room_text(item["room"]))
    parts.append(attendance.groups_line(item))
    return " · ".join(parts)


def _digest(day_word: str, rows: list[tuple[str, str, str]]) -> tuple[str, str]:
    """rows — (начало, предмет, аудитория). Возвращает (заголовок, текст)."""
    if not rows:
        return f"{day_word} пар нет 🎉", "Можно отдохнуть."
    n = len(rows)
    title = f"{day_word} {n} {schedule.plural(n, 'пара', 'пары', 'пар')}, первая в {rows[0][0]}"
    body = "\n".join(f"{start} {subject}" + (f" · {schedule.room_text(room)}" if room else "")
                     for start, subject, room in rows)
    return title, body


async def build(s: AsyncSession, user: User, now: datetime | None = None, days: int = DAYS_AHEAD) -> list[dict]:
    now = now or datetime.now(config.tz)
    if not user.notify_before and not user.digest_time:
        return []
    student = user.student is not None
    if not student and not user.is_teacher:
        return []
    today = now.date()
    # +1 день — для сводки «на завтра» в последний день
    dates = [today + timedelta(days=i) for i in range(days + 1)]
    if student:
        by_day = await schedule.group_days(s, user.group_name, dates, user.half)
    else:
        by_day = await attendance.teacher_days(s, user.teacher, dates)
        if not any(day.lessons or day.cancelled for day in by_day.values()) \
                and not await attendance.teacher_lessons(s, user.teacher):
            return []

    async def day_rows(d: date) -> tuple[list[tuple[str, int | str, str, str]], list[tuple[str, str, str]]]:
        """(для напоминаний: начало, ключ, предмет, строка), (для сводки: начало, предмет, аудитория)."""
        lessons = by_day[d].lessons
        if student:
            show_half = user.half is None
            # У разовой пары нет id в расписании — ключом служит номер изменения
            return ([(l.start_time, l.id or f"x{l.change_id}", l.subject, _student_line(l, show_half)) for l in lessons],
                    [(l.start_time, l.subject, l.room) for l in lessons])
        items = attendance.teacher_items(lessons)
        return ([(i["start"], f"{i['subject']}|{','.join(i['groups'])}", i["subject"], _teacher_line(i)) for i in items],
                [(i["start"], i["subject"], i["room"]) for i in items])

    out: list[dict] = []
    for offset in range(days):
        d = today + timedelta(days=offset)
        reminders, digest_rows = await day_rows(d)
        if user.notify_before:
            for start, key, subject, line in reminders:
                at = _at(d, start) - timedelta(minutes=user.notify_before)
                if at > now:
                    out.append(_item(f"rem:{user.id}:{d}:{key}:{start}", at,
                                     f"{subject} через {_minutes(user.notify_before)}", line, "reminder"))
        if user.digest_time:
            at = _at(d, user.digest_time)
            if at > now:
                if user.digest_day == "tomorrow":
                    _, rows = await day_rows(d + timedelta(days=1))
                    word = "Завтра"
                else:
                    rows, word = digest_rows, "Сегодня"
                title, body = _digest(word, rows)
                out.append(_item(f"dig:{user.id}:{d}", at, title, body, "digest"))
    out.sort(key=lambda x: datetime.fromisoformat(x["at"]))
    return out[:MAX_ITEMS]
