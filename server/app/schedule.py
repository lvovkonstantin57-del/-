"""Повторение пар по неделям, разовые изменения и выборка пар на дату."""

import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.models import (
    CHANGE_ADD, CHANGE_CANCEL, CHANGE_EDIT, WEEK_CUSTOM, WEEK_EVEN, WEEK_EVERY, WEEK_ODD, Lesson, LessonChange,
)

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEK_LABELS = {WEEK_EVERY: "каждая", WEEK_ODD: "нечётная", WEEK_EVEN: "чётная"}
MAX_WEEK = 60
MAX_STEP = 20

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Поля пары, которые можно поменять на один день
SLOT_FIELDS = ("pair_num", "start_time", "end_time", "subject", "kind", "room", "teacher", "half")


def check_time(v: str | None) -> str | None:
    """Для валидаторов pydantic: время «ЧЧ:ММ» или None."""
    if v is not None and not TIME_RE.match(v):
        raise ValueError("время в формате ЧЧ:ММ")
    return v


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_number(d: date, semester_start: date) -> int:
    """Номер учебной недели: неделя, в которую попадает начало семестра, — 1-я."""
    return (monday(d) - monday(semester_start)).days // 7 + 1


def week_parity(d: date, semester_start: date) -> str:
    return WEEK_ODD if week_number(d, semester_start) % 2 else WEEK_EVEN


# --- свои номера недель -----------------------------------------------------------

WEEKS_ITEM_RE = re.compile(r"(\d{1,2})(?:-(\d{1,2}))?(?:/(\d{1,2}))?")


@lru_cache(maxsize=512)
def parse_weeks(text: str | None) -> tuple[tuple[int, int | None, int], ...]:
    """«1-4, 6, 9/2» → ((1, 4, 1), (6, 6, 1), (9, None, 2)): с какой, по какую (None — до конца), шаг.

    Понимает: 7 · 1-4 · 2/3 (со 2-й каждую 3-ю) · 1-16/2 (с 1-й по 16-ю через одну) и «нед.» в начале.
    """
    s = (text or "").lower().replace("–", "-").replace("—", "-")
    s = re.sub(r"нед[а-яё]*\.?", " ", s)
    s = re.sub(r"\s*([-/])\s*", r"\1", s)
    parts = [p for p in re.split(r"[,;\s]+", s) if p]
    if not parts:
        raise ValueError("укажи номера недель, например: 1-4, 6, 9")
    items = []
    for part in parts:
        m = WEEKS_ITEM_RE.fullmatch(part)
        if not m:
            raise ValueError(f"не понял «{part}»: нужны номера недель, например 1-4, 6 или 2/3 — со 2-й каждую 3-ю")
        first = int(m[1])
        last = int(m[2]) if m[2] else (None if m[3] else first)
        step = int(m[3] or 1)
        if not 1 <= first <= MAX_WEEK or (last is not None and not first <= last <= MAX_WEEK):
            raise ValueError(f"«{part}»: недели — от 1 до {MAX_WEEK}, по возрастанию")
        if not 1 <= step <= MAX_STEP:
            raise ValueError(f"«{part}»: шаг — от 1 до {MAX_STEP} недель")
        items.append((first, last, step))
    return tuple(items)


def format_weeks(text: str) -> str:
    """Запись для хранения: «нед. 1 – 4; 6» → «1-4,6»."""
    out = []
    for first, last, step in parse_weeks(text):
        if last is None:
            out.append(f"{first}/{step}")
        elif first == last:
            out.append(str(first))
        else:
            out.append(f"{first}-{last}" + (f"/{step}" if step > 1 else ""))
    return ",".join(out)


def weeks_match(spec: str | None, n: int) -> bool:
    try:
        items = parse_weeks(spec)
    except ValueError:
        return False
    return any(n >= a and (b is None or n <= b) and (n - a) % step == 0 for a, b, step in items)


def week_text(week: str, weeks: str | None) -> str:
    """Подпись для людей: «каждая неделя», «нечётные недели», «недели 1–4, 6», «каждая 3-я неделя с 2-й»."""
    if week == WEEK_ODD:
        return "нечётные недели"
    if week == WEEK_EVEN:
        return "чётные недели"
    if week != WEEK_CUSTOM:
        return "каждая неделя"
    try:
        items = parse_weeks(weeks)
    except ValueError:
        return f"недели {weeks}"
    plain, other = [], []
    for first, last, step in items:
        if last is None:
            other.append(f"с {first}-й недели" if step == 1 else f"каждая {step}-я неделя с {first}-й")
        elif step > 1:
            other.append(f"недели {first}–{last} через {step - 1}" if step == 2 else
                         f"каждая {step}-я неделя с {first}-й по {last}-ю")
        else:
            plain.append(str(first) if first == last else f"{first}–{last}")
    parts = []
    if plain:
        word = "неделя" if len(plain) == 1 and "–" not in plain[0] else "недели"
        parts.append(f"{word} {', '.join(plain)}")
    return ", ".join(parts + other)


def week_matches(lesson: Lesson, d: date, semester_start: date) -> bool:
    if lesson.week == WEEK_CUSTOM:
        return weeks_match(lesson.weeks, week_number(d, semester_start))
    return lesson.week == WEEK_EVERY or lesson.week == week_parity(d, semester_start)


def lesson_visible(lesson: Lesson, d: date, semester_start: date, half: int | None) -> bool:
    if lesson.weekday != d.weekday() or not week_matches(lesson, d, semester_start):
        return False
    # Если половина не выбрана — показываем обе, с пометкой
    return not (lesson.half and half and lesson.half != half)


# --- пара в конкретный день ---------------------------------------------------------

@dataclass
class Slot:
    """Пара в конкретный день — уже с разовыми изменениями. Поля как у Lesson."""

    id: int | None              # Lesson.id; у разовой пары None
    group_name: str
    weekday: int
    week: str
    weeks: str | None
    pair_num: int | None
    start_time: str
    end_time: str
    subject: str
    kind: str
    room: str
    teacher: str
    half: int | None
    status: str | None = None   # None — как обычно, changed — изменена на этот день, extra — разовая
    was: dict = field(default_factory=dict)  # прежние значения изменённых полей
    note: str = ""
    change_id: int | None = None
    moved: bool = False         # отмена или разовая пара — части переноса


def slot_of(l: Lesson) -> Slot:
    return Slot(
        id=l.id, group_name=l.group_name, weekday=l.weekday, week=l.week, weeks=l.weeks,
        pair_num=l.pair_num, start_time=l.start_time, end_time=l.end_time, subject=l.subject,
        kind=l.kind, room=l.room, teacher=l.teacher, half=l.half,
    )


def extra_slot(c: LessonChange) -> Slot:
    return Slot(
        id=None, group_name=c.group_name, weekday=c.date.weekday(), week=WEEK_EVERY, weeks=None,
        pair_num=c.pair_num, start_time=c.start_time or "", end_time=c.end_time or "", subject=c.subject or "",
        kind=c.kind or "", room=c.room or "", teacher=c.teacher or "", half=c.half,
        status="extra", note=c.note, change_id=c.id, moved=bool(c.moved_id),
    )


def changed_fields(slot: Slot, c: LessonChange) -> dict:
    """{поле: (было, стало)} — что разовое изменение меняет у пары."""
    out = {}
    for name in SLOT_FIELDS:
        new = getattr(c, name)
        if new is not None and new != getattr(slot, name):
            out[name] = (getattr(slot, name), new)
    return out


def apply_change(slot: Slot, c: LessonChange) -> Slot:
    diff = changed_fields(slot, c)
    return replace(slot, **{k: v[1] for k, v in diff.items()}, status="changed",
                   was={k: v[0] for k, v in diff.items()}, note=c.note, change_id=c.id)


def sort_key(lesson):
    return (lesson.start_time, lesson.pair_num or 0, lesson.half or 0)


@dataclass
class Day:
    lessons: list[Slot]     # пары этого дня, с изменениями и разовыми
    cancelled: list[Slot]   # отменённые в этот день (и перенесённые на другой)


def half_ok(lesson_half: int | None, half: int | None) -> bool:
    return not (lesson_half and half and lesson_half != half)


def build_day(lessons: list[Lesson], changes: list[LessonChange], d: date, semester_start: date,
              half: int | None) -> Day:
    """Пары дня: из расписания по неделям, минус отмены, с изменениями, плюс разовые."""
    by_lesson = {c.lesson_id: c for c in changes if c.date == d and c.lesson_id and c.action != CHANGE_ADD}
    active, cancelled = [], []
    for l in lessons:
        if not lesson_visible(l, d, semester_start, half):
            continue
        slot, c = slot_of(l), by_lesson.get(l.id)
        if c is not None and c.action == CHANGE_CANCEL:
            cancelled.append(replace(slot, note=c.note, change_id=c.id, moved=bool(c.moved_id)))
            continue
        if c is not None and c.action == CHANGE_EDIT:
            slot = apply_change(slot, c)
        active.append(slot)
    active += [extra_slot(c) for c in changes if c.date == d and c.action == CHANGE_ADD and half_ok(c.half, half)]
    return Day(sorted(active, key=sort_key), sorted(cancelled, key=sort_key))


async def group_lessons(s: AsyncSession, group: str) -> list[Lesson]:
    return list((await s.scalars(select(Lesson).where(Lesson.group_name == group))).all())


async def changes_between(s: AsyncSession, first: date, last: date, groups: list[str] | None = None) -> list[LessonChange]:
    q = select(LessonChange).where(LessonChange.date >= first, LessonChange.date <= last)
    if groups is not None:
        q = q.where(LessonChange.group_name.in_(groups))
    return list((await s.scalars(q)).all())


async def group_days(s: AsyncSession, group: str, dates: list[date], half: int | None) -> dict[date, Day]:
    start = await db.get_semester_start(s)
    lessons = await group_lessons(s, group)
    changes = await changes_between(s, min(dates), max(dates), [group])
    return {d: build_day(lessons, changes, d, start, half) for d in dates}


async def lessons_on(s: AsyncSession, group: str, d: date, half: int | None) -> list[Slot]:
    return (await group_days(s, group, [d], half))[d].lessons


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


def short_date(d: date) -> str:
    return f"{WEEKDAYS_SHORT[d.weekday()].lower()}, {d.day} {MONTHS_GEN[d.month - 1]}"


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

    id: int | None
    group: str
    weekday: int
    week: str
    weeks: str | None
    week_label: str
    pair_num: int | None
    start: str
    end: str
    subject: str
    kind: str
    room: str
    teacher: str
    half: int | None
    status: str | None = None
    was: dict | None = None
    note: str = ""
    change_id: int | None = None
    moved: bool = False

    @classmethod
    def of(cls, l: Lesson | Slot) -> "LessonDTO":
        was = getattr(l, "was", None) or None
        if was:
            was = {{"start_time": "start", "end_time": "end"}.get(k, k): v for k, v in was.items()}
        return cls(
            id=l.id, group=l.group_name, weekday=l.weekday, week=l.week, weeks=l.weeks,
            week_label=week_text(l.week, l.weeks), pair_num=l.pair_num,
            start=l.start_time, end=l.end_time, subject=l.subject, kind=l.kind, room=l.room,
            teacher=l.teacher, half=l.half, status=getattr(l, "status", None), was=was,
            note=getattr(l, "note", ""), change_id=getattr(l, "change_id", None), moved=getattr(l, "moved", False),
        )
