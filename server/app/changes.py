"""Разовые изменения расписания на одну дату — без повторений по неделям.

Отмена, другая аудитория / время / преподаватель, перенос на другой день и разовая пара.
О каждом изменении (и об отмене изменения) группа и преподаватель получают уведомление.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import attendance, db, notify, roles, schedule
from app.config import config
from app.models import CHANGE_ADD, CHANGE_CANCEL, CHANGE_EDIT, Lesson, LessonChange, User

ACTION_MOVE = "move"   # в базе — пара записей: отмена + разовая пара
PAST_DAYS = 7          # насколько назад можно поправить (например, отметить отмену задним числом)
FUTURE_DAYS = 366

FIELD_NAMES = {
    "room": "Аудитория", "start_time": "Начало", "end_time": "Конец", "pair_num": "№ пары",
    "subject": "Предмет", "kind": "Тип", "teacher": "Преподаватель", "half": "Половина",
}


class ChangeError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class ChangeRequest:
    group: str
    date: date
    action: str                       # cancel | change | add | move
    lesson_id: int | None = None
    to_date: date | None = None       # для move
    values: dict | None = None        # новые значения: поля Lesson (start_time, room, …)
    note: str = ""


def today() -> date:
    return datetime.now(config.tz).date()


def _check_date(d: date) -> None:
    t = today()
    if d < t - timedelta(days=PAST_DAYS):
        raise ChangeError(f"Изменения задним числом — не раньше чем за {PAST_DAYS} дней")
    if d > t + timedelta(days=FUTURE_DAYS):
        raise ChangeError("Слишком далеко вперёд — не больше года")


def _value_text(name: str, value) -> str:
    if value in (None, ""):
        return "—"
    if name == "room":
        return schedule.room_text(str(value))
    if name == "half":
        return f"{value}-я половина"
    return str(value)


def _end(text: str) -> str:
    """Точка в конце — если её ещё нет («Петров П. П.» уже кончается точкой)."""
    return text if text.endswith(".") else text + "."


def _when(d: date, slot) -> str:
    return f"{schedule.short_date(d).capitalize()}, {slot.subject} в {slot.start_time}"


def _place(slot) -> str:
    parts = [f"{slot.start_time}–{slot.end_time}"]
    if slot.room:
        parts.append(schedule.room_text(slot.room))
    if slot.teacher:
        parts.append(slot.teacher)
    if slot.half:
        parts.append(f"{slot.half}-я половина")
    return ", ".join(parts)


def describe_edit(d: date, base: schedule.Slot, diff: dict) -> tuple[str, str]:
    """Заголовок и текст уведомления о разовом изменении пары."""
    if set(diff) == {"room"}:
        old, new = diff["room"]
        title = "🚪 Другая аудитория"
        text = f"{_when(d, base)} — {_value_text('room', new)}" + (f" (вместо {old})" if old else "") + "."
        return title, text
    title = "🚪 Другая аудитория" if "room" in diff else "🗓 Изменение в расписании"
    lines = [f"{_when(d, base)} — только в этот день:"]
    times = {k: diff.pop(k) for k in ("start_time", "end_time") if k in diff}
    if times:
        was = f"{base.start_time}–{base.end_time}"
        now = f"{times.get('start_time', (0, base.start_time))[1]}–{times.get('end_time', (0, base.end_time))[1]}"
        lines.append(f"Время: {was} → {now}")
    for name, (old, new) in diff.items():
        lines.append(f"{FIELD_NAMES[name]}: {_value_text(name, old)} → {_value_text(name, new)}")
    return title, "\n".join(lines)


async def _notify(s: AsyncSession, actor: User, group: str, title: str, text: str, note: str,
                  half: int | None, teachers: tuple[str | None, ...]) -> None:
    body = text + (f"\n{note}" if note else "") + f"\n\n{roles.actor_label(actor)}"
    recipients = await notify.group_member_ids(s, [group], half=half)
    recipients += await attendance.teacher_user_ids(s, *teachers)
    await notify.push(s, recipients, f"{title} · {group}", body, kind=notify.KIND_SCHEDULE, skip=[actor.id])


async def _lesson_on(s: AsyncSession, group: str, lesson_id: int | None, d: date) -> Lesson:
    lesson = await s.get(Lesson, lesson_id) if lesson_id else None
    if lesson is None or lesson.group_name != group:
        raise ChangeError("Пара не найдена", 404)
    if not schedule.lesson_visible(lesson, d, await db.get_semester_start(s), None):
        raise ChangeError(f"{schedule.short_date(d).capitalize()} этой пары нет по расписанию")
    return lesson


async def _drop_existing(s: AsyncSession, lesson_id: int, d: date) -> None:
    """У пары на дату одно изменение: новое заменяет прежнее (и вторую половину переноса)."""
    old = (await s.scalars(select(LessonChange).where(
        LessonChange.lesson_id == lesson_id, LessonChange.date == d))).all()
    for c in old:
        await _delete_with_pair(s, c)


async def _delete_with_pair(s: AsyncSession, c: LessonChange) -> None:
    pair = await s.get(LessonChange, c.moved_id) if c.moved_id else None
    await s.delete(c)
    if pair is not None:
        await s.delete(pair)


def _clean_values(values: dict | None) -> dict:
    out = {}
    for name, value in (values or {}).items():
        if name not in schedule.SLOT_FIELDS or value is None:
            continue
        out[name] = " ".join(value.split()) if isinstance(value, str) else value
    if "kind" in out:
        out["kind"] = out["kind"].lower()
    return out


def _check_times(start: str | None, end: str | None) -> None:
    if start and end and start >= end:
        raise ChangeError("Пара должна закончиться позже, чем началась")


async def create(s: AsyncSession, actor: User, req: ChangeRequest) -> list[LessonChange]:
    """Создаёт изменение и уведомляет. Коммит — здесь же."""
    _check_date(req.date)
    values = _clean_values(req.values)
    note = " ".join(req.note.split())[:300]
    created: list[LessonChange] = []

    if req.action == CHANGE_ADD:
        for name in ("subject", "start_time", "end_time"):
            if not values.get(name):
                raise ChangeError("Для разовой пары нужны предмет, начало и конец")
        _check_times(values["start_time"], values["end_time"])
        c = LessonChange(group_name=req.group, date=req.date, action=CHANGE_ADD, note=note,
                         created_by=actor.id, **values)
        s.add(c)
        created.append(c)
        slot = schedule.extra_slot(c)
        await _notify(s, actor, req.group, "➕ Дополнительная пара",
                      _end(f"{schedule.short_date(req.date).capitalize()}: {slot.subject}, {_place(slot)}"),
                      note, slot.half, (slot.teacher,))
        await s.commit()
        return created

    lesson = await _lesson_on(s, req.group, req.lesson_id, req.date)
    base = schedule.slot_of(lesson)
    await _drop_existing(s, lesson.id, req.date)

    if req.action == CHANGE_CANCEL:
        c = LessonChange(group_name=req.group, date=req.date, lesson_id=lesson.id, action=CHANGE_CANCEL,
                         note=note, created_by=actor.id)
        s.add(c)
        created.append(c)
        await _notify(s, actor, req.group, "❌ Пара отменена", f"{_when(req.date, base)} отменена.",
                      note, base.half, (base.teacher,))

    elif req.action == CHANGE_EDIT:
        c = LessonChange(group_name=req.group, date=req.date, lesson_id=lesson.id, action=CHANGE_EDIT,
                         note=note, created_by=actor.id, **values)
        diff = schedule.changed_fields(base, c)
        if not diff:
            raise ChangeError("Ничего не поменялось — укажи новую аудиторию, время или преподавателя")
        _check_times(c.start_time or base.start_time, c.end_time or base.end_time)
        s.add(c)
        created.append(c)
        title, text = describe_edit(req.date, base, dict(diff))
        await _notify(s, actor, req.group, title, text, note, base.half,
                      (base.teacher, c.teacher if "teacher" in diff else None))

    elif req.action == ACTION_MOVE:
        to = req.to_date
        if to is None:
            raise ChangeError("Укажи, на какой день перенести")
        _check_date(to)
        new = {name: getattr(base, name) for name in schedule.SLOT_FIELDS}
        new.update(values)
        _check_times(new["start_time"], new["end_time"])
        if to == req.date and (new["start_time"], new["room"]) == (base.start_time, base.room):
            raise ChangeError("Это тот же день и то же время — выбери другой день или время")
        target = schedule.Slot(id=None, group_name=req.group, weekday=to.weekday(), week="every", weeks=None, **new)
        cancel = LessonChange(group_name=req.group, date=req.date, lesson_id=lesson.id, action=CHANGE_CANCEL,
                              note=note or f"Перенесена на {schedule.short_date(to)}, {target.start_time}",
                              created_by=actor.id)
        extra = LessonChange(group_name=req.group, date=to, action=CHANGE_ADD,
                             note=note or f"Перенос с {schedule.short_date(req.date)}", created_by=actor.id, **new)
        s.add_all([cancel, extra])
        await s.flush()
        cancel.moved_id, extra.moved_id = extra.id, cancel.id
        created += [cancel, extra]
        await _notify(s, actor, req.group, "↪️ Перенос пары",
                      _end(f"{base.subject}: с {schedule.short_date(req.date)} ({base.start_time}) "
                           f"на {schedule.short_date(to)}, {_place(target)}"),
                      req.note.strip(), base.half, (base.teacher, target.teacher))
    else:
        raise ChangeError("Неизвестное изменение")

    await s.commit()
    return created


async def remove(s: AsyncSession, actor: User, c: LessonChange) -> None:
    """Отменить изменение: пара снова по расписанию (или разовой пары не будет). Перенос — обе записи."""
    group = c.group_name
    pair = await s.get(LessonChange, c.moved_id) if c.moved_id else None
    cancel = next((x for x in (c, pair) if x is not None and x.action == CHANGE_CANCEL), None)
    extra = next((x for x in (c, pair) if x is not None and x.action == CHANGE_ADD), None)
    own = cancel or (c if c.action == CHANGE_EDIT else None)
    lesson = await s.get(Lesson, own.lesson_id) if own is not None and own.lesson_id else None
    base = schedule.slot_of(lesson) if lesson else None
    d = own.date if own is not None else c.date
    if cancel is not None and extra is not None:
        title = "↩️ Перенос отменён"
        text = (f"{_when(d, base)} — снова по расписанию, переноса не будет." if base
                else f"Переноса пары «{extra.subject}» не будет.")
    elif extra is not None:
        title = "❌ Дополнительной пары не будет"
        text = f"{schedule.short_date(extra.date).capitalize()}: {extra.subject} в {extra.start_time} не будет."
    else:
        title = "↩️ Снова по расписанию"
        text = (_end(f"{_when(d, base)} — как обычно: {_place(base)}") if base
                else f"{schedule.short_date(d).capitalize()}: пара снова по расписанию.")
    who = base or (schedule.extra_slot(extra) if extra is not None else None)
    await s.delete(c)
    if pair is not None:
        await s.delete(pair)
    await _notify(s, actor, group, title, text, "", who.half if who else None, (who.teacher if who else None,))
    await s.commit()


def summary(c: LessonChange, lesson: Lesson | None) -> str:
    """Строка для списка изменений у старосты."""
    when = schedule.short_date(c.date).capitalize()
    if c.action == CHANGE_ADD:
        slot = schedule.extra_slot(c)
        prefix = "Перенос сюда" if c.moved_id else "Разовая пара"
        return f"{when}: {prefix} — {slot.subject}, {_place(slot)}"
    if lesson is None:
        return f"{when}: изменение пары, которой больше нет в расписании"
    base = schedule.slot_of(lesson)
    if c.action == CHANGE_CANCEL:
        return f"{when}: {base.subject} в {base.start_time} — " + (c.note.lower() if c.moved_id else "отменена")
    diff = schedule.changed_fields(base, c)
    parts = [f"{_value_text(k, new)} вместо {old or '—'}" if k == "room" else
             f"{FIELD_NAMES[k].lower()} {_value_text(k, new)}" for k, (old, new) in diff.items()]
    return f"{when}: {base.subject} в {base.start_time} — " + ", ".join(parts)


async def upcoming(s: AsyncSession, group: str) -> list[dict]:
    rows = (await s.scalars(select(LessonChange).where(
        LessonChange.group_name == group, LessonChange.date >= today()
    ).order_by(LessonChange.date, LessonChange.start_time))).all()
    lessons = {l.id: l for l in (await s.scalars(select(Lesson).where(
        Lesson.id.in_({c.lesson_id for c in rows if c.lesson_id})))).all()}
    out = []
    for c in rows:
        out.append({
            "id": c.id, "date": c.date.isoformat(), "action": c.action, "moved": bool(c.moved_id),
            "lesson_id": c.lesson_id, "summary": summary(c, lessons.get(c.lesson_id)), "note": c.note,
        })
    return out


def lesson_identity(l) -> tuple:
    """Чем пара узнаётся после повторной загрузки xlsx: день, время, предмет, половина."""
    return (l.group_name, l.weekday, l.start_time, l.subject, l.half or 0)
