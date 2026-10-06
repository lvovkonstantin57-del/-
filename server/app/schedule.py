"""Чётность недель и выборка пар на дату."""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.models import WEEK_EVEN, WEEK_EVERY, WEEK_ODD, Lesson

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEK_LABELS = {WEEK_EVERY: "каждая", WEEK_ODD: "нечётная", WEEK_EVEN: "чётная"}


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_number(d: date, semester_start: date) -> int:
    """Номер учебной недели: неделя, в которую попадает начало семестра, — 1-я."""
    return (monday(d) - monday(semester_start)).days // 7 + 1


def week_parity(d: date, semester_start: date) -> str:
    return WEEK_ODD if week_number(d, semester_start) % 2 else WEEK_EVEN


def lesson_visible(lesson: Lesson, d: date, semester_start: date, half: int | None) -> bool:
    if lesson.weekday != d.weekday():
        return False
    if lesson.week != WEEK_EVERY and lesson.week != week_parity(d, semester_start):
        return False
    # Если половина не выбрана — показываем обе, с пометкой
    if lesson.half and half and lesson.half != half:
        return False
    return True


def sort_key(lesson: Lesson):
    return (lesson.start_time, lesson.pair_num or 0, lesson.half or 0)


async def group_lessons(s: AsyncSession, group: str) -> list[Lesson]:
    return list((await s.scalars(select(Lesson).where(Lesson.group_name == group))).all())


async def lessons_on(s: AsyncSession, group: str, d: date, half: int | None) -> list[Lesson]:
    start = await db.get_semester_start(s)
    lessons = [l for l in await group_lessons(s, group) if lesson_visible(l, d, start, half)]
    return sorted(lessons, key=sort_key)


async def group_has_halves(s: AsyncSession, group: str) -> bool:
    return any(l.half for l in await group_lessons(s, group))


async def half_lessons(s: AsyncSession, group: str) -> list[Lesson]:
    return sorted(
        (l for l in await group_lessons(s, group) if l.half),
        key=lambda l: (l.weekday, l.week, l.start_time, l.half),
    )


# --- подписи ------------------------------------------------------------------

def human_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS_GEN[d.month - 1]}"


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def room_text(room: str) -> str:
    return f"ауд. {room}" if room[:1].isdigit() else room


@dataclass
class LessonDTO:
    """Пара в виде, удобном для JSON-ответа API."""

    id: int
    group: str
    weekday: int
    week: str
    pair_num: int | None
    start: str
    end: str
    subject: str
    kind: str
    room: str
    teacher: str
    half: int | None

    @classmethod
    def of(cls, l: Lesson) -> "LessonDTO":
        return cls(
            id=l.id, group=l.group_name, weekday=l.weekday, week=l.week, pair_num=l.pair_num,
            start=l.start_time, end=l.end_time, subject=l.subject, kind=l.kind, room=l.room,
            teacher=l.teacher, half=l.half,
        )
