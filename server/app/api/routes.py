import hashlib
import logging
import mimetypes
import re
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import attendance, backup, db, journal, notify, pictures, plan, roles, schedule, security, stats, users
from app.api import auth as auth_api
from app.api import changes as changes_api
from app.api import journal as journal_api
from app.api import notifications as notifications_api
from app.api import pictures as pictures_api
from app.api import teacher as teacher_api
from app.api.deps import AdminDep, OwnerDep, SessionDep, StaffDep, UserDep, check_group
from app.api.files import attachment
from app.attendance import AttendanceError
from app.config import config
from app.importer import ImportError_, clean_email, clean_group, import_any, name_key
from app.models import ROLE_ADMIN, ROLE_OWNER, ROLE_STAROSTA, ROLE_USER, WEEK_CUSTOM, GroupInfo, Lesson, Student, User
from app.users import AccountError, clean_fio

log = logging.getLogger(__name__)

MAX_UPLOAD = 5 * 1024 * 1024
WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"


_check_time = schedule.check_time


class NameIn(BaseModel):
    full_name: str = Field(min_length=3, max_length=200)


class SettingsIn(BaseModel):
    notify_before: int | None = Field(default=None, ge=1, le=180)
    digest_time: str | None = None
    digest_day: Literal["today", "tomorrow"] = "tomorrow"
    half: Literal[1, 2] | None = None

    @field_validator("digest_time")
    @classmethod
    def _time(cls, v):
        return _check_time(v)


class LessonIn(BaseModel):
    group: str = Field(min_length=1, max_length=100)
    weekday: int = Field(ge=0, le=6)
    week: Literal["every", "odd", "even", "custom"] = "every"
    # Для week == custom: номера недель — «1-4, 6», «2/3» (со 2-й каждую 3-ю)
    weeks: str | None = Field(default=None, max_length=200)
    pair_num: int | None = Field(default=None, ge=1, le=12)
    start: str
    end: str
    subject: str = Field(min_length=1, max_length=300)
    kind: str = Field(default="", max_length=50)
    room: str = Field(default="", max_length=100)
    teacher: str = Field(default="", max_length=200)
    half: Literal[1, 2] | None = None

    @field_validator("start", "end")
    @classmethod
    def _time(cls, v):
        return _check_time(v)

    @model_validator(mode="after")
    def _weeks(self):
        if self.week == WEEK_CUSTOM:
            try:
                self.weeks = schedule.format_weeks(self.weeks or "")
            except ValueError as e:
                raise ValueError(f"Недели: {e}") from e
        else:
            self.weeks = None
        if self.start >= self.end:
            raise ValueError("Пара должна закончиться позже, чем началась")
        return self

    def apply(self, lesson: Lesson) -> Lesson:
        lesson.group_name = clean_group(self.group)
        lesson.weekday, lesson.week, lesson.weeks, lesson.pair_num = self.weekday, self.week, self.weeks, self.pair_num
        lesson.start_time, lesson.end_time = self.start, self.end
        lesson.subject, lesson.kind = self.subject.strip(), self.kind.strip().lower()
        lesson.room, lesson.teacher, lesson.half = self.room.strip(), self.teacher.strip(), self.half
        return lesson


class Bell(BaseModel):
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _time(cls, v):
        return _check_time(v)


class ConfigIn(BaseModel):
    semester_start: date
    semester_end: date | None = None
    bells: list[Bell] = Field(min_length=1, max_length=12)
    lk_url: str | None = Field(default=None, max_length=500)

    @field_validator("lk_url")
    @classmethod
    def _url(cls, v):
        v = (v or "").strip()
        if v and not re.match(r"^https?://[^\s/]+\.[^\s]+$", v):
            raise ValueError("ссылка должна начинаться с https://")
        return v or None


def _check_email(v: str | None) -> str | None:
    try:
        return clean_email(v)
    except ValueError as e:
        raise ValueError("почта выглядит неправильно, нужно так: name@mpgu.su") from e


class GroupInfoIn(BaseModel):
    group: str = Field(min_length=1, max_length=100)
    curator: str = Field(default="", max_length=200)
    contact: str = Field(default="", max_length=200)


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class GroupCodeIn(BaseModel):
    group: str = Field(min_length=1, max_length=100)


class MemberIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    group: str = Field(min_length=1, max_length=100)


class StudentIn(BaseModel):
    full_name: str = Field(min_length=3, max_length=200)
    group: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v):
        return _check_email(v)


class StudentPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=3, max_length=200)
    group: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=200)  # "" — стереть

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v):
        return _check_email(v)


class RoleIn(BaseModel):
    role: Literal["user", "starosta", "admin"]


class ContactIn(BaseModel):
    topic: Literal["fio", "group", "schedule", "other"] = "other"
    text: str = Field(min_length=1, max_length=1000)


def today() -> date:
    return datetime.now(config.tz).date()


api = APIRouter(prefix="/api")


# --- для всех --------------------------------------------------------------

@api.get("/info")
async def info():
    """Проверка адреса сервера из приложения: «это наш сервер?»."""
    return {"app": "schedule", "name": config.app_name, "version": 1}


@api.get("/me")
async def me(user: UserDep, s: SessionDep):
    group = user.group_name
    half_lessons = await schedule.half_lessons(s, group) if group else []
    return {
        "id": user.id,
        "login": user.login,
        "full_name": user.full_name,
        "code": security.pretty_code(user.code),
        "photo": pictures.url(user.photo),
        "role": user.effective_role,
        "student": (
            {"id": user.student.id, "full_name": user.student.full_name, "group": group, "email": user.student.email}
            if user.student else None
        ),
        "curator": await group_curator(s, group) if group else None,
        "teacher": (
            {"full_name": user.teacher.full_name, **await attendance.teacher_profile(s, user.teacher)}
            if user.teacher else None
        ),
        "lk_url": await db.get_lk_url(s),
        "half": user.half,
        "half_lessons": [asdict(schedule.LessonDTO.of(l)) for l in half_lessons],
        "settings": {
            "notify_before": user.notify_before,
            "digest_time": user.digest_time,
            "digest_day": user.digest_day,
        },
        "unread": await notify.unread_count(s, user.id),
        "today": today().isoformat(),
        "timezone": config.timezone,
        "semester": await semester_info(s),
        "admin_contact": config.admin_contact or None,
        "app_name": config.app_name,
    }


async def group_curator(s: AsyncSession, group: str) -> dict | None:
    info = await s.get(GroupInfo, group)
    if info is None or not (info.curator or info.curator_contact):
        return None
    return {"name": info.curator, "contact": info.curator_contact}


async def semester_info(s: AsyncSession) -> dict:
    start = await db.get_semester_start(s)
    end = await db.get_semester_end(s)
    d = today()
    info = {"start": start.isoformat(), "end": None, "week": schedule.week_number(d, start)}
    if end and end > start:
        info.update(
            end=end.isoformat(),
            total_weeks=schedule.week_number(end - timedelta(days=1), start),
            days_left=(end - d).days,
            progress=round(min(1.0, max(0.0, (d - start).days / (end - start).days)), 3),
        )
    return info


@api.put("/me/settings")
async def save_settings(body: SettingsIn, user: UserDep, s: SessionDep):
    """Меняет только присланные поля: уведомления и половину группы сохраняют разные экраны."""
    if user.student is None and not user.is_teacher:
        raise HTTPException(403, "Сначала вступи в группу: покажи старосте свой код или введи код группы")
    fields = set(body.model_fields_set)
    if "half" in fields and user.student is None:
        raise HTTPException(403, "Половина группы — только для студентов")
    for field in fields:
        setattr(user, field, getattr(body, field))
    await s.commit()
    return {"ok": True}


@api.put("/me/name")
async def change_name(body: NameIn, user: UserDep, s: SessionDep):
    """Своё ФИО — прямо в профиле. У студента меняется и строка в списке группы; старосты получат уведомление."""
    fio = users.person_fio(body.full_name)
    old = user.display_name
    if fio == old and fio == user.full_name:
        return {"full_name": fio}
    student = user.student
    if student is not None:
        await _check_name_free(s, name_key(fio), student.group_name, student.id)
        student.full_name, student.name_key = fio, name_key(fio)
    if user.teacher is not None:
        user.teacher.full_name = fio
    user.full_name = fio
    if student is not None and fio != old:
        starostas = [i for i in await notify.starosta_ids(s, student.group_name) if i != user.id]
        await notify.push(s, starostas, "Студент сменил ФИО", f"{old} → {fio}\nГруппа {student.group_name}",
                          kind=notify.KIND_GROUP, sender_id=user.id)
    await s.commit()
    log.info("Пользователь %s сменил ФИО", user.id)
    return {"full_name": fio}


@api.get("/me/plan")
async def notification_plan(user: UserDep, s: SessionDep, days: int = Query(default=plan.DAYS_AHEAD, ge=1, le=21)):
    """Что поставить в системные уведомления телефона: напоминания перед парами и сводки."""
    return {"items": await plan.build(s, user, days=days), "timezone": config.timezone}


CONTACT_TOPICS = {"fio": "Ошибка в ФИО", "group": "Не та группа", "schedule": "Ошибка в расписании", "other": "Другое"}


@api.post("/me/contact")
async def contact_admins(body: ContactIn, user: UserDep, s: SessionDep):
    """Сообщение всем админам — в их ленту уведомлений, с кнопкой «Ответить»."""
    if not body.text.strip():
        raise HTTPException(422, "Напиши, что нужно исправить")
    if security.messages.blocked(user.id):
        raise HTTPException(429, "Слишком часто — подожди несколько минут")
    admins = [i for i in await notify.admin_ids(s) if i != user.id]
    if not admins:
        raise HTTPException(503, "Пока нет ни одного админа")
    where = user.group_name or "без группы"
    if user.half:
        where += f", {user.half}-я половина"
    text = f"{user.display_name} · {where}\nЛичный код: {security.pretty_code(user.code)}\n\n{body.text.strip()}"
    delivered = await notify.push(s, admins, f"✉️ {CONTACT_TOPICS[body.topic]}", text,
                                  kind=notify.KIND_CONTACT, sender_id=user.id)
    await s.commit()
    security.messages.hit(user.id)
    log.info("Сообщение от %s админам (%s), доставлено %d", user.id, body.topic, delivered)
    return {"ok": True, "delivered": delivered}


@api.get("/schedule")
async def get_schedule(
    user: UserDep,
    s: SessionDep,
    day: Annotated[date | None, Query(alias="date")] = None,
    group: str | None = None,
    mine: bool = False,
    edit: bool = False,
):
    """Неделя пар: своей группы, выбранной (админу) или, при mine, только пары преподавателя.
    edit — для разовых изменений: пары обеих половин группы (старосте и админу).
    У начавшихся пар — attendance: кто отметился (старосте — по группе, студенту — он сам)."""
    day = day or today()
    start = await db.get_semester_start(s)
    mon = schedule.monday(day)
    dates = [mon + timedelta(days=i) for i in range(7)]
    week = {
        "week_number": schedule.week_number(mon, start),
        "parity": schedule.week_parity(mon, start),
        "today": today().isoformat(),
    }
    # У преподавателя — его пары по всем подгруппам, где он стоит в расписании
    if mine or (user.is_teacher and not group and not user.group_name):
        if not user.is_teacher:
            raise HTTPException(403, "Это расписание преподавателя")
        by_day = await attendance.teacher_days(s, user.teacher, dates)
        found = bool(await attendance.teacher_lessons(s, user.teacher)) or any(x.lessons for x in by_day.values())
        days = [
            {"date": d.isoformat(), "weekday": i, "lessons": attendance.teacher_items(by_day[d].lessons),
             "cancelled": attendance.teacher_items(by_day[d].cancelled)}
            for i, d in enumerate(dates)
        ]
        await journal.mark_teacher_days(s, user.teacher, days)
        return {"mine": True, "found": found, "group": None, "half": None, "can_edit": False, **week, "days": days}
    if group and group != user.group_name:
        if not user.is_admin and not user.is_teacher:
            raise HTTPException(403, "Можно смотреть только свою группу")
        half = None
    else:
        group, half = user.group_name, user.half
    if not group:
        raise HTTPException(403, "Ты ещё не в группе: покажи старосте свой личный код или введи код группы")
    if edit and user.can_manage(group):
        half = None
    by_day = await schedule.group_days(s, group, dates, half)
    days = [{
        "date": d.isoformat(),
        "weekday": i,
        "lessons": [asdict(schedule.LessonDTO.of(l)) for l in by_day[d].lessons],
        "cancelled": [asdict(schedule.LessonDTO.of(l)) for l in by_day[d].cancelled],
    } for i, d in enumerate(dates)]
    await journal.mark_group_days(s, user, group, by_day, days)
    return {"mine": False, "group": group, "half": half, "can_edit": user.can_manage(group), **week, "days": days}


# --- админка ---------------------------------------------------------------

@api.get("/admin/groups")
async def groups(user: StaffDep, s: SessionDep):
    return await roles.manageable_groups(s, user)


@api.post("/admin/groups")
async def create_group(body: GroupIn, admin: AdminDep, s: SessionDep):
    name = clean_group(body.name)
    if not name:
        raise HTTPException(422, "Напиши название группы")
    existing = {g.casefold(): g for g in await db.list_groups(s)}
    if name.casefold() in existing:
        raise HTTPException(409, f"Группа уже есть: {existing[name.casefold()]}")
    await users.group_info(s, name)
    await s.commit()
    code = await users.group_code(s, name)
    log.info("Админ %s создал группу %s", admin.id, name)
    return {"group": name, "code": security.pretty_code(code)}


@api.delete("/admin/groups/{name}")
async def delete_group(name: str, admin: AdminDep, s: SessionDep):
    """Удалить можно только пустую группу: без студентов и пар."""
    has_students = await s.scalar(select(func.count(Student.id)).where(Student.group_name == name))
    has_lessons = await s.scalar(select(func.count(Lesson.id)).where(Lesson.group_name == name))
    if has_students or has_lessons:
        raise HTTPException(409, "В группе есть студенты или пары — сначала удали их")
    info = await s.get(GroupInfo, name)
    if info:
        await s.delete(info)
        await s.commit()
    log.info("Админ %s удалил группу %s", admin.id, name)
    return {"ok": True}


@api.get("/admin/group-code")
async def get_group_code(group: str, user: StaffDep, s: SessionDep):
    check_group(user, group)
    if group not in await db.list_groups(s):
        raise HTTPException(404, "Нет такой группы")
    return {"group": group, "code": security.pretty_code(await users.group_code(s, group))}


@api.post("/admin/group-code")
async def new_group_code(body: GroupCodeIn, user: StaffDep, s: SessionDep):
    """Новый код группы: старый перестаёт работать (например, его переслали не туда)."""
    group = clean_group(body.group)
    check_group(user, group)
    if group not in await db.list_groups(s):
        raise HTTPException(404, "Нет такой группы")
    code = await users.group_code(s, group, regenerate=True)
    log.info("%s %s сменил код группы %s", user.role, user.id, group)
    return {"group": group, "code": security.pretty_code(code)}


def _person(u: User) -> dict:
    return {
        "id": u.id, "full_name": u.display_name, "code": security.pretty_code(u.code),
        "group": u.group_name, "role": u.effective_role, "teacher": u.is_teacher, "photo": pictures.url(u.photo),
    }


@api.get("/admin/lookup")
async def lookup_code(code: str, user: StaffDep, s: SessionDep):
    """Кто это по личному коду — чтобы проверить перед добавлением в группу или назначением роли."""
    key = ("lookup", user.id)
    if security.code_fails.blocked(key):
        raise HTTPException(429, f"Слишком много неверных кодов. Подожди {security.code_fails.wait_minutes(key)} мин")
    target = await users.user_by_code(s, code)
    if target is None:
        security.code_fails.hit(key)
        raise HTTPException(404, "Нет человека с таким кодом. Код — 6 символов, он в профиле у студента")
    return _person(target)


@api.post("/admin/members")
async def add_member(body: MemberIn, user: StaffDep, s: SessionDep):
    """Добавить студента в группу по его личному коду."""
    group = clean_group(body.group)
    check_group(user, group)
    if group not in await db.list_groups(s):
        raise HTTPException(404, "Нет такой группы")
    key = ("lookup", user.id)
    if security.code_fails.blocked(key):
        raise HTTPException(429, f"Слишком много неверных кодов. Подожди {security.code_fails.wait_minutes(key)} мин")
    target = await users.user_by_code(s, body.code)
    if target is None:
        security.code_fails.hit(key)
        raise HTTPException(404, "Нет человека с таким кодом. Код — 6 символов, он в профиле у студента")
    if target.student is not None:
        if target.group_name == group:
            return {"status": "already", **_person(target)}
        if not user.is_admin:
            raise HTTPException(409, f"{target.display_name} уже в группе {target.group_name}. Перевести может админ")
        old_group = target.group_name
        target.student.group_name = group
        target.half = None
        await roles.notify_student_changed(s, target.id, f"Твоя группа теперь: {group} (была {old_group})", user)
        await s.commit()
        log.info("Админ %s перевёл %s: %s → %s", user.id, target.id, old_group, group)
        await s.refresh(target, attribute_names=["student"])
        return {"status": "moved", **_person(target)}
    student = await users.join_group(s, target, group)
    await roles.notify_joined(s, target, student, actor=user)
    await s.commit()
    log.info("%s %s добавил %s в группу %s", user.role, user.id, target.id, group)
    return {"status": "added", **_person(target)}


@api.get("/admin/stats")
async def admin_stats(_: AdminDep, s: SessionDep):
    return await stats.collect(s)


@api.get("/admin/config")
async def get_config(_: StaffDep, s: SessionDep):
    return {
        "semester_start": (await db.get_semester_start(s)).isoformat(),
        "semester_end": (end.isoformat() if (end := await db.get_semester_end(s)) else None),
        "bells": [{"start": a, "end": b} for a, b in await db.get_bells(s)],
        "lk_url": await db.get_lk_url(s),
    }


@api.put("/admin/config")
async def put_config(body: ConfigIn, admin: AdminDep, s: SessionDep):
    if body.semester_end and body.semester_end <= body.semester_start:
        raise HTTPException(400, "Сессия должна начинаться позже начала семестра")
    await db.set_setting(s, "semester_start", body.semester_start.isoformat())
    await db.set_setting(s, "semester_end", body.semester_end.isoformat() if body.semester_end else None)
    await db.set_setting(s, "bells", [[b.start, b.end] for b in body.bells])
    if "lk_url" in body.model_fields_set:
        await db.set_setting(s, "lk_url", body.lk_url)
    await s.commit()
    log.info("Админ %s обновил настройки семестра", admin.id)
    return {"ok": True}


@api.get("/admin/group-info")
async def get_group_info(group: str, user: StaffDep, s: SessionDep):
    check_group(user, group)
    info = await s.get(GroupInfo, group)
    return {"group": group, "curator": info.curator if info else "", "contact": info.curator_contact if info else ""}


@api.put("/admin/group-info")
async def put_group_info(body: GroupInfoIn, user: StaffDep, s: SessionDep):
    group = clean_group(body.group)
    check_group(user, group)
    info = await users.group_info(s, group)
    info.curator = " ".join(body.curator.split())
    info.curator_contact = body.contact.strip()
    await s.commit()
    log.info("%s %s обновил куратора группы %s", user.role, user.id, group)
    return {"group": group, "curator": info.curator, "contact": info.curator_contact}


# --- пары ------------------------------------------------------------------

def _lesson_line(l: Lesson) -> str:
    week = "" if l.week == "every" else f", {schedule.week_text(l.week, l.weeks)}"
    half = f", {l.half}-я половина" if l.half else ""
    room = f", {schedule.room_text(l.room)}" if l.room else ""
    return f"{schedule.WEEKDAYS_SHORT[l.weekday]}{week}, {l.start_time}–{l.end_time} — {l.subject}{room}{half}"


LESSON_FIELDS = ("group_name", "weekday", "week", "weeks", "pair_num", "start_time", "end_time", "subject", "kind",
                 "room", "teacher", "half")


def _lesson_state(l: Lesson) -> dict:
    return {f: getattr(l, f) for f in LESSON_FIELDS}


async def _schedule_changed(s: AsyncSession, actor: User, group: str, text: str,
                            title: str = "🗓 Изменение в расписании", half: int | None = None,
                            teachers: tuple[str | None, ...] = ()) -> None:
    recipients = await notify.group_member_ids(s, [group], half=half)
    recipients += await attendance.teacher_user_ids(s, *teachers)
    await notify.push(s, recipients, f"{title} · {group}", f"{text}\n\n{roles.actor_label(actor)}",
                      kind=notify.KIND_SCHEDULE, skip=[actor.id])


@api.get("/admin/lessons")
async def list_lessons(group: str, user: StaffDep, s: SessionDep):
    check_group(user, group)
    lessons = await schedule.group_lessons(s, group)
    lessons.sort(key=lambda l: (l.weekday, l.start_time, l.week, l.weeks or "", l.half or 0))
    return [asdict(schedule.LessonDTO.of(l)) for l in lessons]


@api.post("/admin/lessons")
async def create_lesson(body: LessonIn, user: StaffDep, s: SessionDep):
    check_group(user, clean_group(body.group))
    lesson = body.apply(Lesson())
    s.add(lesson)
    await _schedule_changed(s, user, lesson.group_name, "Новая пара: " + _lesson_line(lesson),
                            half=lesson.half, teachers=(lesson.teacher,))
    await s.commit()
    return asdict(schedule.LessonDTO.of(lesson))


@api.put("/admin/lessons/{lesson_id}")
async def update_lesson(lesson_id: int, body: LessonIn, user: StaffDep, s: SessionDep):
    lesson = await s.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(404, "Пара не найдена")
    check_group(user, lesson.group_name)
    check_group(user, clean_group(body.group))
    before, before_state, old_group = _lesson_line(lesson), _lesson_state(lesson), lesson.group_name
    old_room, old_teacher, old_half = lesson.room, lesson.teacher, lesson.half
    body.apply(lesson)
    after_state = _lesson_state(lesson)
    changed = {f for f in LESSON_FIELDS if before_state[f] != after_state[f]}
    if changed:
        if changed == {"room"}:
            title = "🚪 Новая аудитория"
            new_room = schedule.room_text(lesson.room) if lesson.room else "без аудитории"
            was = f" вместо {schedule.room_text(old_room)}" if old_room else ""
            when = "каждую неделю" if lesson.week == "every" else schedule.week_text(lesson.week, lesson.weeks)
            text = (f"{lesson.subject} ({schedule.WEEKDAYS_SHORT[lesson.weekday]}, {lesson.start_time}): "
                    f"теперь {new_room}{was} — {when}.")
        else:
            title = "🚪 Изменение в расписании" if "room" in changed else "🗓 Изменение в расписании"
            text = f"Было: {before}\nСтало: {_lesson_line(lesson)}"
        for g in sorted({old_group, lesson.group_name}):
            await _schedule_changed(s, user, g, text, title=title,
                                    half=lesson.half if lesson.half == old_half else None,
                                    teachers=(old_teacher, lesson.teacher))
    await s.commit()
    return asdict(schedule.LessonDTO.of(lesson))


@api.delete("/admin/lessons/{lesson_id}")
async def delete_lesson(lesson_id: int, user: StaffDep, s: SessionDep):
    lesson = await s.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(404, "Пара не найдена")
    check_group(user, lesson.group_name)
    await _schedule_changed(s, user, lesson.group_name, "Пары больше нет: " + _lesson_line(lesson),
                            half=lesson.half, teachers=(lesson.teacher,))
    await s.delete(lesson)
    await s.commit()
    return {"ok": True}


@api.post("/admin/import")
async def import_file(file: UploadFile, admin: AdminDep, s: SessionDep):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Файл больше 5 МБ")
    try:
        message = await import_any(s, data)
    except ImportError_ as e:
        raise HTTPException(400, str(e)) from e
    log.info("Импорт файла %s админом %s", file.filename, admin.id)
    return {"message": message}


# --- студенты ----------------------------------------------------------------

@api.get("/admin/students")
async def list_students(user: StaffDep, s: SessionDep):
    stmt = select(Student).order_by(Student.group_name, Student.full_name)
    if not user.is_admin:
        stmt = stmt.where(Student.group_name == user.group_name)
    students = (await s.scalars(stmt)).all()
    linked = {
        u.student_id: u
        for u in (await s.scalars(select(User).where(User.student_id.is_not(None)))).unique().all()
    }
    out = []
    for st in students:
        u = linked.get(st.id)
        item = {
            "id": st.id, "full_name": st.full_name, "group": st.group_name, "email": st.email,
            "linked": u is not None,
            "user_id": u.id if u else None,
            "code": security.pretty_code(u.code) if u else None,
            "half": u.half if u else None,
            "role": u.effective_role if u else None,
            "photo": pictures.url(u.photo) if u else None,
        }
        if user.is_admin:
            item["login"] = u.login if u else None
        out.append(item)
    return out


async def _student_for(user: User, s: AsyncSession, student_id: int) -> Student:
    student = await s.get(Student, student_id)
    if student is None:
        raise HTTPException(404, "Студента нет в списке")
    check_group(user, student.group_name)
    return student


async def _check_name_free(s: AsyncSession, key: str, group: str, student_id: int | None = None) -> None:
    clash = await s.scalar(select(Student).where(
        Student.name_key == key, Student.group_name == group, Student.id != (student_id or 0)))
    if clash:
        raise HTTPException(409, f"Такой студент уже есть в группе: {clash.full_name}")


@api.post("/admin/students")
async def add_student(body: StudentIn, user: StaffDep, s: SessionDep):
    """Строка в списке без аккаунта: когда студент вступит в группу с таким же ФИО — привяжется к ней."""
    fio = clean_fio(body.full_name)
    if fio is None:
        raise HTTPException(422, "Нужны фамилия и имя — лучше полностью, с отчеством")
    group = clean_group(body.group)
    check_group(user, group)
    await _check_name_free(s, name_key(fio), group)
    student = Student(full_name=fio, name_key=name_key(fio), group_name=group, email=body.email)
    s.add(student)
    await s.commit()
    log.info("%s %s добавил студента в %s", user.role, user.id, group)
    return {"id": student.id, "full_name": fio, "group": group, "email": student.email}


@api.put("/admin/students/{student_id}")
async def edit_student(student_id: int, body: StudentPatch, user: StaffDep, s: SessionDep):
    student = await _student_for(user, s, student_id)
    changes = []
    if body.full_name is not None:
        fio = clean_fio(body.full_name)
        if fio is None:
            raise HTTPException(422, "Нужны фамилия и имя — лучше полностью, с отчеством")
        if fio != student.full_name:
            await _check_name_free(s, name_key(fio), student.group_name, student.id)
            student.full_name, student.name_key = fio, name_key(fio)
            changes.append(f"ФИО в списке группы: {fio}")
    if body.group is not None and clean_group(body.group) != student.group_name:
        if not user.is_admin:
            raise HTTPException(403, "Перевести в другую группу может только админ")
        student.group_name = clean_group(body.group)
        # У другой группы своё деление на половины — выбор сбрасываем
        await s.execute(update(User).where(User.student_id == student.id).values(half=None))
        changes.append(f"Твоя группа теперь: {student.group_name}")
    if "email" in body.model_fields_set and body.email != student.email:
        student.email = body.email
        if body.email:
            changes.append(f"Почта: {body.email}")
    if changes:
        log.info("%s %s исправил студента %s", user.role, user.id, student.id)
        linked = await users.account_of_student(s, student.id)
        if linked:
            await roles.notify_student_changed(s, linked.id, "\n".join(changes), user)
    await s.commit()
    return {"id": student.id, "full_name": student.full_name, "group": student.group_name, "email": student.email}


@api.delete("/admin/students/{student_id}")
async def delete_student(student_id: int, user: StaffDep, s: SessionDep):
    """Убрать из группы: запись в списке удаляется, аккаунт остаётся — уже без группы."""
    student = await _student_for(user, s, student_id)
    linked = await users.account_of_student(s, student.id)
    if linked is not None and linked.id == user.id:
        raise HTTPException(400, "Себя убрать из группы нельзя — попроси админа")
    group = student.group_name
    for uid in await roles.unlink_students(s, [student.id]):
        await notify.push(s, [uid], f"Ты больше не в группе {group}",
                          f"{roles.actor_label(user)} убрал тебя из списка группы. Если это ошибка — "
                          "покажи старосте свой личный код ещё раз.", kind=notify.KIND_GROUP, sender_id=user.id)
    await s.delete(student)
    await s.commit()
    log.info("%s %s удалил студента %s", user.role, user.id, student_id)
    return {"ok": True}


@api.post("/admin/students/{student_id}/unlink")
async def unlink_student(student_id: int, admin: AdminDep, s: SessionDep):
    """Отвязать аккаунт от записи (запись в списке остаётся)."""
    await roles.unlink_students(s, [student_id])
    await s.commit()
    log.info("Админ %s отвязал аккаунт от студента %s", admin.id, student_id)
    return {"ok": True}


@api.post("/admin/users/{user_id}/password")
async def reset_password(user_id: int, user: StaffDep, s: SessionDep):
    """Временный пароль для того, кто забыл свой: староста — своей группе, админ — всем, кроме админов."""
    target = await s.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Пользователь не найден")
    if target.id == user.id:
        raise HTTPException(400, "Свой пароль меняется в профиле")
    if target.is_admin and not user.is_owner:
        raise HTTPException(403, "Пароль админа сбрасывает только главный админ")
    if target.is_owner:
        raise HTTPException(403, "Пароль главного админа сбросить нельзя")
    if not user.is_admin and (target.is_staff or not user.can_manage(target.group_name)):
        raise HTTPException(403, "Староста сбрасывает пароль только студентам своей группы")
    password = security.temp_password()
    await users.set_password(s, target, password)
    await notify.push(s, [target.id], "🔑 Пароль сброшен",
                      f"{roles.actor_label(user)} выдал временный пароль. Смени его в профиле.",
                      kind=notify.KIND_INFO, sender_id=user.id)
    await s.commit()
    log.info("%s %s сбросил пароль %s", user.role, user.id, target.id)
    return {"password": password, "login": target.login}


# --- роли и команда --------------------------------------------------------------

@api.put("/admin/users/{user_id}/role")
async def change_role(user_id: int, body: RoleIn, admin: AdminDep, s: SessionDep):
    target = await s.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Пользователь не найден")
    try:
        await roles.set_role(s, admin, target, body.role)
    except roles.RoleError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "role": target.effective_role}


@api.get("/admin/admins")
async def list_admins(_: AdminDep, s: SessionDep):
    """Команда: админы и старосты групп."""
    admins = (await s.scalars(select(User).where(User.role.in_([ROLE_ADMIN, ROLE_OWNER])))).unique().all()
    starostas = [u for u in (await s.scalars(select(User).where(User.role == ROLE_STAROSTA))).unique().all()
                 if u.is_starosta]
    starostas.sort(key=lambda u: (u.group_name, u.display_name))
    return {
        "admins": [
            {"id": u.id, "full_name": u.display_name, "login": u.login, "code": security.pretty_code(u.code),
             "role": u.role, "photo": pictures.url(u.photo)}
            for u in sorted(admins, key=lambda u: (u.role != ROLE_OWNER, u.display_name))
        ],
        "starostas": [
            {"id": u.id, "full_name": u.display_name, "code": security.pretty_code(u.code), "group": u.group_name,
             "photo": pictures.url(u.photo)}
            for u in starostas
        ],
    }


@api.delete("/admin/admins/{user_id}")
async def remove_admin(user_id: int, owner: OwnerDep, s: SessionDep):
    user = await s.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Не найден")
    try:
        await roles.set_role(s, owner, user, ROLE_USER)
    except roles.RoleError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@api.get("/admin/backup")
async def download_backup(owner: OwnerDep):
    data = await backup.make_backup()
    log.info("Главный админ %s скачал бэкап", owner.id)
    return attachment(data, backup.backup_name(datetime.now(config.tz)), "application/octet-stream")


# --- приложение ------------------------------------------------------------------

mimetypes.add_type("application/manifest+json", ".webmanifest")


# Скрипты и запросы — только со своего сайта: даже если в текст (ФИО, объявление) пролезет
# чужой <script>, браузер его не запустит и токен входа никуда не уйдёт
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "font-src 'self'",
    "manifest-src 'self'",
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))
LONG_CACHE = (".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp", ".woff2")


def cache_policy(path: str, versioned: bool) -> str:
    """API — не хранить; картинки и app.js?v=… — надолго (адрес меняется при обновлении);
    остальное (index.html, sw.js) — каждый раз сверять с сервером."""
    if path.startswith("/api/"):
        return "no-store"
    if versioned:
        return "public, max-age=31536000, immutable"
    if path.endswith(LONG_CACHE):
        return "public, max-age=2592000"  # 30 дней
    return "no-cache"


def index_html() -> str:
    """index.html со ссылками вида app.js?v=<хэш>: после обновления адрес меняется,
    и браузер не покажет старую копию из кэша."""
    assets = ("app.js", "style.css")
    version = hashlib.sha256(b"".join((WEBAPP_DIR / a).read_bytes() for a in assets)).hexdigest()[:10]
    html = (WEBAPP_DIR / "index.html").read_text(encoding="utf-8")
    for a in assets:
        html = html.replace(f'"{a}"', f'"{a}?v={version}"')
    return html


def create_app() -> FastAPI:
    app = FastAPI(title=config.app_name, docs_url=None, redoc_url=None, openapi_url=None)
    # Приложение на телефоне открывается с capacitor://localhost или https://localhost —
    # чтобы оно могло обращаться к API, разрешаем эти адреса
    app.add_middleware(
        CORSMiddleware, allow_origins=config.cors_list, allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"], expose_headers=["Content-Disposition"], max_age=3600,
    )
    app.include_router(auth_api.api)
    app.include_router(notifications_api.api)
    app.include_router(api)
    app.include_router(changes_api.api)
    app.include_router(journal_api.api)
    app.include_router(pictures_api.api)
    app.include_router(teacher_api.api)

    @app.exception_handler(AttendanceError)
    async def attendance_error(_: Request, e: AttendanceError):
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    @app.exception_handler(AccountError)
    async def account_error(_: Request, e: AccountError):
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if "cache-control" not in response.headers:
            response.headers["Cache-Control"] = cache_policy(path, "v" in request.query_params)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if not path.startswith("/api/"):
            response.headers.setdefault("Content-Security-Policy", CSP)
            response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    page = index_html()

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    async def index():
        return HTMLResponse(page)

    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
    return app
