"""Разовые изменения расписания: староста — своей группе, админ — любой."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app import changes, db
from app.api.deps import SessionDep, StaffDep, check_group
from app.importer import clean_group
from app.models import LessonChange
from app.schedule import check_time

api = APIRouter(prefix="/api/admin")


class ChangeIn(BaseModel):
    group: str = Field(min_length=1, max_length=100)
    date: date
    action: Literal["cancel", "change", "add", "move"]
    lesson_id: int | None = None
    to_date: date | None = None
    pair_num: int | None = Field(default=None, ge=1, le=12)
    start: str | None = None
    end: str | None = None
    subject: str | None = Field(default=None, max_length=300)
    kind: str | None = Field(default=None, max_length=50)
    room: str | None = Field(default=None, max_length=100)
    teacher: str | None = Field(default=None, max_length=200)
    half: Literal[1, 2] | None = None
    note: str = Field(default="", max_length=300)

    @field_validator("start", "end")
    @classmethod
    def _time(cls, v):
        return check_time(v or None)


@api.get("/changes")
async def list_changes(group: str, user: StaffDep, s: SessionDep):
    """Разовые изменения группы с сегодняшнего дня — чтобы видеть их списком и отменять."""
    check_group(user, group)
    return await changes.upcoming(s, group)


@api.post("/changes")
async def create_change(body: ChangeIn, user: StaffDep, s: SessionDep):
    group = clean_group(body.group)
    check_group(user, group)
    if group not in await db.list_groups(s):
        raise HTTPException(404, "Нет такой группы")
    values = {
        "pair_num": body.pair_num, "start_time": body.start, "end_time": body.end, "subject": body.subject,
        "kind": body.kind, "room": body.room, "teacher": body.teacher, "half": body.half,
    }
    req = changes.ChangeRequest(group=group, date=body.date, action=body.action, lesson_id=body.lesson_id,
                                to_date=body.to_date, values=values, note=body.note)
    try:
        created = await changes.create(s, user, req)
    except changes.ChangeError as e:
        raise HTTPException(e.status, str(e)) from e
    return {"ids": [c.id for c in created]}


@api.delete("/changes/{change_id}")
async def delete_change(change_id: int, user: StaffDep, s: SessionDep):
    c = await s.get(LessonChange, change_id)
    if c is None:
        raise HTTPException(404, "Изменение уже отменено")
    check_group(user, c.group_name)
    await changes.remove(s, user, c)
    return {"ok": True}
