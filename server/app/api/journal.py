"""Посещаемость группы: староста — своей, админ — любой."""

import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app import attendance, db, journal, wipe_attendance
from app.api.deps import OwnerDep, SessionDep, StaffDep, check_group
from app.api.files import attachment
from app.api.teacher import XLSX, MarkIn
from app.importer import clean_group
from app.schedule import check_time

log = logging.getLogger(__name__)
api = APIRouter(prefix="/api/admin/attendance")


class GroupSessionIn(BaseModel):
    group: str = Field(min_length=1, max_length=100)
    date: date
    start: str
    subject: str = Field(min_length=1, max_length=300)
    code: bool = False  # показать код студентам; без него — отметить вручную

    @field_validator("start")
    @classmethod
    def _time(cls, v):
        return check_time(v)


@api.get("")
async def group_journal(group: str, user: StaffDep, s: SessionDep):
    check_group(user, group)
    return await journal.group_journal(s, user, group)


@api.get("/export")
async def export_group(group: str, user: StaffDep, s: SessionDep):
    check_group(user, group)
    data = await journal.group_xlsx(s, group)
    if data is None:
        raise HTTPException(400, "В журнале группы пока нет пар")
    day = attendance.local_now().strftime("%d.%m.%Y")
    return attachment(data, f"Посещаемость {group} {day}.xlsx", XLSX)


@api.post("")
async def open_session(body: GroupSessionIn, user: StaffDep, s: SessionDep):
    group = clean_group(body.group)
    check_group(user, group)
    if group not in await db.list_groups(s):
        raise HTTPException(404, "Нет такой группы")
    x = await journal.open_group_session(s, user, group, body.date, body.start, body.subject, body.code)
    return await journal.staff_detail(s, user, x)


@api.get("/{session_id}")
async def get_session(session_id: int, user: StaffDep, s: SessionDep):
    return await journal.staff_detail(s, user, await journal.staff_session(s, user, session_id))


@api.post("/{session_id}/code")
async def new_code(session_id: int, user: StaffDep, s: SessionDep):
    x = await journal.staff_session(s, user, session_id, edit=True)
    await journal.show_code(s, user, x)
    return await journal.staff_detail(s, user, x)


@api.post("/{session_id}/close")
async def close_session(session_id: int, user: StaffDep, s: SessionDep):
    x = await journal.staff_session(s, user, session_id, edit=True)
    await attendance.close_session(s, x)
    return await journal.staff_detail(s, user, x)


@api.put("/{session_id}/marks/{student_id}")
async def set_mark(session_id: int, student_id: int, body: MarkIn, user: StaffDep, s: SessionDep):
    x = await journal.staff_session(s, user, session_id, edit=True)
    await attendance.set_mark(s, x, student_id, body.present)
    return await journal.staff_detail(s, user, x)


@api.delete("/{session_id}")
async def delete_session(session_id: int, user: StaffDep, s: SessionDep):
    x = await journal.staff_session(s, user, session_id)
    if not journal.can_delete(user, x):
        raise HTTPException(403, "Эту пару отмечает преподаватель — удалить её может он или главный админ")
    await s.delete(x)
    await s.commit()
    log.info("%s %s удалил пару %s из журнала", user.role, user.id, session_id)
    return {"ok": True}


# --- главный админ: снести отметки разом ---------------------------------------------

@api.delete("")
async def delete_group(group: str, owner: OwnerDep, s: SessionDep):
    """Все пары с отметками одной группы."""
    n = await journal.delete_group_sessions(s, group)
    log.info("Главный админ %s удалил отметки группы %s: %d пар", owner.id, group, n)
    return {"sessions": n}


@api.post("/wipe")
async def wipe_all(owner: OwnerDep):
    """Весь журнал посещаемости во всех группах; перед этим — копия базы в папку бэкапов."""
    sessions, marks, _ = await wipe_attendance.wipe()
    log.warning("Главный админ %s очистил журнал посещаемости: %d пар, %d отметок", owner.id, sessions, marks)
    return {"sessions": sessions, "marks": marks}
