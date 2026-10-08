"""Посещаемость группы: журнал старосты и отметки на прошедших парах в расписании.

Отметку на паре открывает преподаватель (вкладка «Код») или сам староста: показывает код
студентам или отмечает вручную, кто был. В расписании у прошедших пар видно, кто отметился:
старосте — сколько человек из группы и кто именно, студенту — его собственная отметка.
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import attendance, schedule
from app.attendance import (
    CODE_TTL, AttendanceError, Rosters, _new_code, _rate, code_left, is_open, journal_xlsx, local_hhmm,
    rosters_for, session_detail, utcnow,
)
from app.models import AttendanceGroup, AttendanceSession, Student, Teacher, User

log = logging.getLogger(__name__)

MATCH_EARLY = 20     # отметку открыли за столько минут до начала — она для этой пары
JOURNAL_DAYS = 366   # насколько давнюю пару староста может отметить задним числом


def _minutes(hhmm: str | None) -> int | None:
    if not hhmm:
        return None
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _same_subject(a: str | None, b: str | None) -> bool:
    return " ".join((a or "").split()).casefold() == " ".join((b or "").split()).casefold()


def _fits(x: AttendanceSession, half: int | None) -> bool:
    """Отметка половины группы не подходит паре другой половины."""
    return not (x.half and half and x.half != half)


def _loose(x: AttendanceSession, item: tuple) -> bool:
    """Отметка без точного времени: то же название или открыта во время пары."""
    start, end, subject, _ = item
    if _same_subject(x.subject, subject):
        return True
    if x.start_time is not None:
        return False
    opened, a, b = _minutes(local_hhmm(x.created_at)), _minutes(start), _minutes(end)
    return None not in (opened, a, b) and a - MATCH_EARLY <= opened <= b


def match_sessions(items: list[tuple], sessions: list[AttendanceSession]) -> dict[int, AttendanceSession]:
    """Какая отметка к какой паре дня. items — (начало, конец, предмет, половина) пар по порядку.

    Сначала — по времени начала, потом остальные (преподаватель открыл «Другую пару»
    или пару перенесли) — по названию или по тому, когда отметку открыли. Если пару отметили
    и преподаватель, и староста, показываем журнал преподавателя.
    """
    out: dict[int, AttendanceSession] = {}
    pending = sorted(sessions, key=lambda x: (x.opened_by is not None, x.created_at))
    for exact in (True, False):
        rest = []
        for x in pending:
            i = next((i for i, item in enumerate(items) if i not in out and _fits(x, item[3])
                      and (x.start_time == item[0] if exact else _loose(x, item))), None)
            if i is None:
                rest.append(x)
            else:
                out[i] = x
        pending = rest
    return out


def slot_item(l: schedule.Slot) -> tuple:
    return (l.start_time, l.end_time, l.subject, l.half)


def can_view(user: User, x: AttendanceSession) -> bool:
    return any(user.can_manage(g) for g in x.group_names)


def can_edit(user: User, x: AttendanceSession) -> bool:
    """Староста правит только свои отметки; у пары преподавателя журнал ведёт преподаватель."""
    return x.opened_by is not None and bool(x.groups) and all(user.can_manage(g) for g in x.group_names)


def can_delete(user: User, x: AttendanceSession) -> bool:
    """Удалить пару из журнала: свою отметку — староста, любую (и преподавателя) — главный админ."""
    return user.is_owner or can_edit(user, x)


async def delete_group_sessions(s: AsyncSession, group: str) -> int:
    """Все отметки группы. Общая пара нескольких групп (лекция) остаётся у остальных —
    из неё уходят только эта группа и отметки её студентов. Вернёт, у скольких пар убрали группу."""
    sessions = await group_sessions(s, group)
    ids = set((await s.scalars(select(Student.id).where(Student.group_name == group))).all())
    for x in sessions:
        if x.group_names == [group]:
            await s.delete(x)
            continue
        x.groups = [g for g in x.groups if g.group_name != group]
        x.marks = [m for m in x.marks if m.student_id not in ids]
    await s.commit()
    return len(sessions)


def group_count(x: AttendanceSession, r: Rosters, group: str) -> tuple[int, int]:
    """Сколько студентов группы отметились и сколько должны были быть."""
    marked = {m.student_id for m in x.marks}
    expected = [st for st in r.expected(x) if st.group_name == group]
    return sum(st.id in marked for st in expected), len(expected)


def started(d: date, start: str, now) -> bool:
    return d < now.date() or (d == now.date() and start <= now.strftime("%H:%M"))


async def group_sessions(s: AsyncSession, group: str, dates: list[date] | None = None) -> list[AttendanceSession]:
    stmt = (select(AttendanceSession).join(AttendanceGroup).where(AttendanceGroup.group_name == group)
            .order_by(AttendanceSession.lesson_date.desc(), AttendanceSession.start_time.desc(),
                      AttendanceSession.created_at.desc()))
    if dates is not None:
        stmt = stmt.where(AttendanceSession.lesson_date.in_(dates))
    return list((await s.scalars(stmt)).all())


# --- отметки в расписании ----------------------------------------------------------

async def mark_group_days(s: AsyncSession, user: User, group: str, by_day: dict[date, schedule.Day],
                          days: list[dict]) -> None:
    """Добавляет к начавшимся и прошедшим парам недели поле attendance.

    Старосте и админу — {staff, session, present, total, open, by_teacher}: session None —
    отметки не было, её можно сделать. Студенту — {marked, at}, только если отметка была.
    """
    staff = user.can_manage(group)
    student_id = user.student_id if user.group_name == group else None
    now = attendance.local_now()
    dates = [d for d in by_day if d <= now.date()]
    if not dates or (not staff and student_id is None):
        return
    by_date: dict[date, list[AttendanceSession]] = defaultdict(list)
    for x in await group_sessions(s, group, dates):
        by_date[x.lesson_date].append(x)
    r = await Rosters.load(s, {group}) if staff else None
    for day in days:
        d = date.fromisoformat(day["date"])
        if d not in by_day or d > now.date():
            continue
        matched = match_sessions([slot_item(l) for l in by_day[d].lessons], by_date[d])
        for i, lesson in enumerate(day["lessons"]):
            if not started(d, lesson["start"], now):
                continue
            x = matched.get(i)
            info: dict = {}
            if x is not None and student_id is not None:
                mark = next((m for m in x.marks if m.student_id == student_id), None)
                info = {"marked": mark is not None, "at": local_hhmm(mark.marked_at) if mark else None}
            if staff:
                info["staff"] = True
                info["session"] = x.id if x else None
                if x is not None:
                    info["present"], info["total"] = group_count(x, r, group)
                    info["open"] = code_left(x) > 0
                    info["by_teacher"] = x.opened_by is None
            if info:
                lesson["attendance"] = info


async def mark_teacher_days(s: AsyncSession, teacher: Teacher, days: list[dict]) -> None:
    """Расписание преподавателя: у прошедших пар — сколько отметились на его отметке."""
    now = attendance.local_now()
    dates = [date.fromisoformat(day["date"]) for day in days]
    dates = [d for d in dates if d <= now.date()]
    if not dates:
        return
    sessions = list((await s.scalars(select(AttendanceSession).where(
        AttendanceSession.teacher_id == teacher.user_id, AttendanceSession.lesson_date.in_(dates)
    ))).all())
    if not sessions:
        return
    r = await rosters_for(s, sessions)
    for day in days:
        d = date.fromisoformat(day["date"])
        items = [(l["start"], l["end"], l["subject"], l["half"]) for l in day["lessons"]]
        matched = match_sessions(items, [x for x in sessions if x.lesson_date == d])
        for i, x in matched.items():
            day["lessons"][i]["attendance"] = {
                "teacher": True, "session": x.id, "present": len(x.marks), "total": len(r.expected(x)),
                "open": is_open(x),
            }


# --- староста отмечает сам -----------------------------------------------------------

async def find_slot(s: AsyncSession, group: str, d: date, start: str, subject: str) -> tuple[schedule.Slot, int, schedule.Day]:
    day = (await schedule.group_days(s, group, [d], None))[d]
    for i, l in enumerate(day.lessons):
        if l.start_time == start and _same_subject(l.subject, subject):
            return l, i, day
    raise AttendanceError("Такой пары в этот день нет — обнови расписание", 404)


async def open_group_session(s: AsyncSession, user: User, group: str, d: date, start: str, subject: str,
                             with_code: bool) -> AttendanceSession:
    """Отметка старосты на паре из расписания. Уже есть отметка этой пары — возвращает её
    (а с with_code, если она старосты, — с новым кодом)."""
    today = attendance.local_now().date()
    if d > today or (d == today and start > attendance.local_now().strftime("%H:%M") and not with_code):
        raise AttendanceError("Пара ещё не началась — отметить можно, когда она начнётся")
    if d < today - timedelta(days=JOURNAL_DAYS):
        raise AttendanceError("Слишком давняя пара")
    if with_code and d != today:
        raise AttendanceError("Код можно показать только на сегодняшней паре — прошлую отметь вручную")
    slot, index, day = await find_slot(s, group, d, start, subject)
    existing = match_sessions([slot_item(l) for l in day.lessons], await group_sessions(s, group, [d])).get(index)
    if existing is not None:
        if with_code and can_edit(user, existing):
            await show_code(s, user, existing)
        return existing
    now = utcnow()
    x = AttendanceSession(
        teacher_id=None, opened_by=user.id, teacher_name=slot.teacher or "", subject=slot.subject,
        lesson_date=d, start_time=slot.start_time, end_time=slot.end_time, half=slot.half or 0,
        created_at=now, closed_at=None if with_code else now,
        groups=[AttendanceGroup(group_name=group)], marks=[],
    )
    s.add(x)
    if with_code:
        await show_code(s, user, x)
    else:
        await s.commit()
    log.info("Пользователь %s открыл отметку старосты для %s на %s %s", user.id, group, d, start)
    return x


async def show_qr(s: AsyncSession, user: User, x: AttendanceSession) -> dict:
    """QR вместо цифр: как show_code, только код меняется каждые несколько секунд."""
    await _today_open(s, user, x)
    return await attendance.qr_token(s, x)


async def _today_open(s: AsyncSession, user: User, x: AttendanceSession) -> AttendanceSession:
    """Завершённую сегодняшнюю отметку открывает снова; другие открытые отметки этого старосты
    закрываются — код один."""
    if x.lesson_date != attendance.local_now().date():
        raise AttendanceError("Код можно показать только на сегодняшней паре")
    now = utcnow()
    if not is_open(x, now):
        x.closed_at = None
        x.created_at = now
    for old in (await s.scalars(select(AttendanceSession).where(
        AttendanceSession.opened_by == user.id, AttendanceSession.closed_at.is_(None)
    ))).all():
        if old is not x:
            old.closed_at = now
    return x


async def show_code(s: AsyncSession, user: User, x: AttendanceSession) -> None:
    """Новый код на минуту."""
    await _today_open(s, user, x)
    x.code = await _new_code(s)
    x.code_expires_at = utcnow() + timedelta(seconds=CODE_TTL)
    await s.commit()


async def staff_session(s: AsyncSession, user: User, session_id: int, edit: bool = False) -> AttendanceSession:
    x = await s.get(AttendanceSession, session_id)
    if x is None or not can_view(user, x):
        raise AttendanceError("Отметка не найдена", 404)
    if edit and not can_edit(user, x):
        raise AttendanceError("Эту пару отмечает преподаватель — поменять отметки может только он", 403)
    return x


async def staff_detail(s: AsyncSession, user: User, x: AttendanceSession) -> dict:
    """Как у преподавателя, но только студенты своей группы; код пары преподавателя не показываем."""
    d = await session_detail(s, x)
    roster = [p for p in d["roster"] if user.can_manage(p["group"])]
    editable = can_edit(user, x)
    d.update(roster=roster, present=sum(p["present"] for p in roster), total=len(roster),
             editable=editable, deletable=can_delete(user, x), by_teacher=x.opened_by is None)
    if not editable:
        d.update(code=None, expires_in=0)
    return d


# --- все пары с отметками (админ) --------------------------------------------------

async def all_sessions(s: AsyncSession, user: User, group: str | None = None, limit: int = 500) -> list[dict]:
    """Пары с отметками всех групп (или одной), новые сверху — чтобы просмотреть и удалить лишнее."""
    stmt = (select(AttendanceSession)
            .order_by(AttendanceSession.lesson_date.desc(), AttendanceSession.start_time.desc(),
                      AttendanceSession.created_at.desc())
            .limit(limit))
    if group:
        stmt = stmt.join(AttendanceGroup).where(AttendanceGroup.group_name == group)
    sessions = list((await s.scalars(stmt)).unique().all())
    r = await rosters_for(s, sessions)
    return [{**attendance.session_brief(x, r), "by_teacher": x.opened_by is None, "deletable": can_delete(user, x)}
            for x in sessions]


# --- журнал группы -------------------------------------------------------------------

async def group_journal(s: AsyncSession, user: User, group: str) -> dict:
    """Все пары группы с отметкой и посещаемость каждого студента: был, из скольких, что пропустил."""
    sessions = await group_sessions(s, group)
    r = await Rosters.load(s, {group})
    counts = {st.id: [0, 0, []] for st in r.by_id.values()}
    briefs = []
    for x in sessions:
        marked = {m.student_id for m in x.marks}
        expected = [st for st in r.expected(x) if st.group_name == group]
        for st in expected:
            counts[st.id][1] += 1
            if st.id in marked:
                counts[st.id][0] += 1
            else:
                counts[st.id][2].append(x.id)
        briefs.append({
            "id": x.id, "subject": x.subject, "date": x.lesson_date.isoformat(), "start": x.start_time,
            "end": x.end_time, "half": x.half, "teacher": x.teacher_name, "by_teacher": x.opened_by is None,
            "open": code_left(x) > 0, "editable": can_edit(user, x),
            "present": sum(st.id in marked for st in expected), "total": len(expected),
        })
    students = [
        {"id": st.id, "full_name": st.full_name, "in_app": st.id in r.linked, "attended": c[0], "total": c[1],
         "rate": _rate(c[0], c[1]), "missed": c[2]}
        for st in sorted(r.by_id.values(), key=lambda st: st.full_name)
        for c in [counts[st.id]]
    ]
    was = sum(b["present"] for b in briefs)
    should = sum(b["total"] for b in briefs)
    return {"group": group, "rate": _rate(was, should), "sessions": briefs, "students": students}


async def group_xlsx(s: AsyncSession, group: str) -> bytes | None:
    sessions = list(reversed(await group_sessions(s, group)))
    if not sessions:
        return None
    r = await rosters_for(s, sessions)
    return journal_xlsx(sessions, r, lambda x: x.teacher_name or "", keep=lambda st: st.group_name == group)


# --- своя посещаемость студента --------------------------------------------------------

async def student_stats(s: AsyncSession, user: User) -> dict:
    """Посещаемость студента в его группе: всего, по предметам и какие пары пропущены (последние сначала)."""
    if user.student is None:
        raise AttendanceError("Посещаемость видна студентам, которые уже в группе", 403)
    group, sid = user.group_name, user.student_id
    r = await Rosters.load(s, {group})
    subjects: dict[str, list[int]] = {}
    missed = []
    was = should = 0
    for x in await group_sessions(s, group):
        if sid not in {st.id for st in r.expected(x)}:
            continue  # пара другой половины группы
        here = any(m.student_id == sid for m in x.marks)
        c = subjects.setdefault(x.subject, [0, 0])
        c[1] += 1
        should += 1
        if here:
            c[0] += 1
            was += 1
        else:
            missed.append({"date": x.lesson_date.isoformat(), "start": x.start_time, "subject": x.subject,
                           "teacher": x.teacher_name})
    return {
        "attended": was, "total": should, "rate": _rate(was, should),
        "subjects": sorted(
            ({"subject": name, "attended": a, "total": t, "rate": _rate(a, t)} for name, (a, t) in subjects.items()),
            key=lambda x: (x["rate"], x["subject"])),
        "missed": missed,
    }
