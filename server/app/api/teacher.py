"""API отметки на паре: преподаватель открывает код, студент отмечается, главный админ выдаёт приглашения."""

import logging
import re
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app import attendance, notify
from app.api.deps import AdminDep, OwnerDep, SessionDep, TeacherDep, UserDep
from app.api.files import attachment
from app.models import Teacher, TeacherInvite, User

log = logging.getLogger(__name__)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

api = APIRouter(prefix="/api")


class SessionIn(BaseModel):
    groups: list[str] = Field(min_length=1, max_length=30)
    half: Literal[0, 1, 2] = 0
    subject: str = Field(default="", max_length=300)
    start: str | None = None
    end: str | None = None

    @field_validator("start", "end")
    @classmethod
    def _time(cls, v):
        if v is not None and not TIME_RE.match(v):
            raise ValueError("время в формате ЧЧ:ММ")
        return v


class ScheduleNameIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)  # None — искать по ФИО


class MarkIn(BaseModel):
    present: bool


class CheckinIn(BaseModel):
    code: str = Field(max_length=20)


# --- студент ------------------------------------------------------------------

@api.get("/attendance/me")
async def my_attendance(user: UserDep, s: SessionDep):
    """Пары с отметкой сегодня у моей группы: идёт ли сейчас отметка и отметился ли я."""
    return {"sessions": await attendance.student_today(s, user)}


@api.post("/attendance/checkin")
async def checkin(body: CheckinIn, user: UserDep, s: SessionDep):
    x, mark, already = await attendance.checkin(s, user, body.code)
    return {
        "ok": True, "already": already, "subject": x.subject, "teacher": x.teacher_name,
        "at": attendance.local_hhmm(mark.marked_at),
    }


# --- преподаватель ------------------------------------------------------------

@api.get("/teacher")
async def teacher_home(teacher: TeacherDep, s: SessionDep):
    return await attendance.teacher_summary(s, teacher)


@api.get("/teacher/people")
async def schedule_people(teacher: TeacherDep, s: SessionDep):
    """Преподаватели из расписания — найти себя, если записан не так, как зарегистрировался."""
    me = attendance.person_key(teacher.full_name)
    return {
        "current": teacher.schedule_name,
        "people": [
            {"name": p["name"], "names": p["names"], "lessons": p["lessons"], "subjects": p["subjects"],
             "match": bool(me) and attendance.same_person(me, p["key"])}
            for p in await attendance.schedule_people(s)
        ],
    }


@api.put("/teacher/schedule-name")
async def set_schedule_name(body: ScheduleNameIn, teacher: TeacherDep, s: SessionDep):
    await attendance.set_schedule_name(s, teacher, body.name)
    return await attendance.teacher_profile(s, teacher)


@api.get("/teacher/today")
async def teacher_today(teacher: TeacherDep, s: SessionDep):
    return await attendance.teacher_today(s, teacher)


@api.post("/teacher/sessions")
async def open_session(body: SessionIn, teacher: TeacherDep, s: SessionDep):
    x = await attendance.open_session(s, teacher, body.groups, body.half, body.subject, body.start, body.end)
    return await attendance.session_detail(s, x)


@api.get("/teacher/sessions")
async def list_sessions(teacher: TeacherDep, s: SessionDep):
    sessions = await attendance.teacher_sessions(s, teacher)
    r = await attendance.rosters_for(s, sessions)
    return [attendance.session_brief(x, r) for x in sessions]


@api.get("/teacher/sessions/{session_id}")
async def get_session(session_id: int, teacher: TeacherDep, s: SessionDep):
    return await attendance.session_detail(s, await attendance.teacher_session(s, teacher, session_id))


@api.post("/teacher/sessions/{session_id}/code")
async def new_code(session_id: int, teacher: TeacherDep, s: SessionDep):
    x = await attendance.new_code(s, await attendance.teacher_session(s, teacher, session_id))
    return await attendance.session_detail(s, x)


@api.post("/teacher/sessions/{session_id}/close")
async def close_session(session_id: int, teacher: TeacherDep, s: SessionDep):
    x = await attendance.teacher_session(s, teacher, session_id)
    await attendance.close_session(s, x)
    return await attendance.session_detail(s, x)


@api.delete("/teacher/sessions/{session_id}")
async def delete_session(session_id: int, teacher: TeacherDep, s: SessionDep):
    x = await attendance.teacher_session(s, teacher, session_id)
    await s.delete(x)
    await s.commit()
    log.info("Преподаватель %s удалил пару %s из журнала", teacher.user_id, session_id)
    return {"ok": True}


@api.put("/teacher/sessions/{session_id}/marks/{student_id}")
async def set_mark(session_id: int, student_id: int, body: MarkIn, teacher: TeacherDep, s: SessionDep):
    x = await attendance.teacher_session(s, teacher, session_id)
    await attendance.set_mark(s, x, student_id, body.present)
    return await attendance.session_detail(s, x)


@api.get("/teacher/attendance")
async def subject_attendance(subject: str, teacher: TeacherDep, s: SessionDep):
    return await attendance.subject_attendance(s, teacher, subject)


@api.get("/teacher/export")
async def export_journal(teacher: TeacherDep, s: SessionDep):
    """Журнал в Excel: лист на каждый предмет. Приложение сохраняет файл или открывает «Поделиться»."""
    data = await attendance.export_xlsx(s, teacher)
    if data is None:
        raise HTTPException(400, "В журнале пока нет пар")
    day = attendance.local_now().strftime("%d.%m.%Y")
    return attachment(data, f"Посещаемость {day}.xlsx", XLSX)


# --- главный админ: приглашения и список преподавателей ---------------------------

def _invite_dict(invite: TeacherInvite) -> dict:
    return {
        "code": invite.code,
        "pretty": attendance.pretty_invite(invite.code),
        "expires": invite.expires_at.date().isoformat(),
    }


@api.get("/admin/teachers")
async def list_teachers(admin: AdminDep, s: SessionDep):
    teachers = (await s.scalars(select(Teacher).order_by(Teacher.full_name))).all()
    accounts = {u.id: u for u in (await s.scalars(
        select(User).where(User.id.in_([t.user_id for t in teachers]))
    )).unique().all()} if teachers else {}
    out = {"teachers": [], "invites": []}
    for t in teachers:
        profile = await attendance.teacher_profile(s, t)
        out["teachers"].append({
            "id": t.user_id, "full_name": t.full_name, "schedule_name": profile["schedule_name"],
            "subjects": [x["subject"] for x in profile["subjects"]],
            "login": accounts[t.user_id].login if t.user_id in accounts else None,
        })
    # Действующие коды видит только главный админ: по коду любой станет преподавателем
    if admin.is_owner:
        now = attendance.utcnow()
        invites = (await s.scalars(
            select(TeacherInvite).where(TeacherInvite.expires_at > now).order_by(TeacherInvite.created_at)
        )).all()
        out["invites"] = [_invite_dict(i) for i in invites]
    return out


@api.post("/admin/teachers/invites")
async def create_invite(owner: OwnerDep, s: SessionDep):
    return _invite_dict(await attendance.create_invite(s, owner))


@api.delete("/admin/teachers/invites/{code}")
async def delete_invite(code: str, _: OwnerDep, s: SessionDep):
    invite = await s.get(TeacherInvite, attendance.clean_invite(code) or "")
    if invite:
        await s.delete(invite)
        await s.commit()
    return {"ok": True}


@api.delete("/admin/teachers/{user_id}")
async def remove_teacher(user_id: int, owner: OwnerDep, s: SessionDep):
    if not await attendance.remove_teacher(s, user_id):
        raise HTTPException(404, "Преподаватель не найден")
    log.info("Главный админ %s убрал преподавателя %s", owner.id, user_id)
    await notify.push(s, [user_id], "Доступ преподавателя отключён",
                      "Главный админ убрал вас из преподавателей. Журнал ваших пар сохранён.", kind=notify.KIND_TEACHER)
    await s.commit()
    return {"ok": True}
