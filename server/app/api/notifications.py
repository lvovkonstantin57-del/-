"""Лента уведомлений, ответы и объявления старост и админов."""

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app import notify, roles, security
from app.api.deps import SessionDep, StaffDep, UserDep
from app.importer import clean_group
from app.models import Notification, User

log = logging.getLogger(__name__)
api = APIRouter(prefix="/api")

PAGE = 50


class ReadIn(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=500)  # None — все


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class AnnounceIn(BaseModel):
    groups: list[str] = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)


async def _with_senders(s, items: list[Notification]) -> list[dict]:
    ids = {n.sender_id for n in items if n.sender_id}
    senders = {u.id: u for u in (await s.scalars(select(User).where(User.id.in_(ids)))).unique().all()} if ids else {}
    return [notify.to_dict(n, senders.get(n.sender_id)) for n in items]


@api.get("/notifications")
async def list_notifications(user: UserDep, s: SessionDep, before_id: int | None = None,
                             limit: int = Query(default=PAGE, ge=1, le=100)):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if before_id:
        stmt = stmt.where(Notification.id < before_id)
    items = list((await s.scalars(stmt.order_by(Notification.id.desc()).limit(limit))).all())
    return {"items": await _with_senders(s, items), "unread": await notify.unread_count(s, user.id),
            "more": len(items) == limit}


@api.get("/notifications/new")
async def new_notifications(user: UserDep, s: SessionDep, after_id: int = 0):
    """Непрочитанные новее after_id — для фоновой проверки на телефоне."""
    items = list((await s.scalars(
        select(Notification)
        .where(Notification.user_id == user.id, Notification.id > after_id, Notification.read_at.is_(None))
        .order_by(Notification.id).limit(20)
    )).all())
    return {"items": [notify.to_dict(n) for n in items], "unread": await notify.unread_count(s, user.id)}


@api.post("/notifications/read")
async def read_notifications(body: ReadIn, user: UserDep, s: SessionDep):
    await notify.mark_read(s, user.id, body.ids)
    await s.commit()
    return {"unread": await notify.unread_count(s, user.id)}


@api.delete("/notifications")
async def clear_notifications(user: UserDep, s: SessionDep):
    """Очистить ленту целиком."""
    result = await s.execute(delete(Notification).where(Notification.user_id == user.id))
    await s.commit()
    return {"deleted": result.rowcount, "unread": 0}


@api.delete("/notifications/{notification_id}")
async def delete_notification(notification_id: int, user: UserDep, s: SessionDep):
    n = await s.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, "Уведомление не найдено")
    await s.delete(n)
    await s.commit()
    return {"unread": await notify.unread_count(s, user.id)}


@api.post("/notifications/{notification_id}/reply")
async def reply(notification_id: int, body: ReplyIn, user: UserDep, s: SessionDep):
    n = await s.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, "Уведомление не найдено")
    if n.kind not in notify.REPLYABLE or n.sender_id is None:
        raise HTTPException(400, "На это уведомление ответить нельзя")
    if not body.text.strip():
        raise HTTPException(422, "Напиши ответ")
    if security.messages.blocked(user.id):
        raise HTTPException(429, "Слишком часто — подожди несколько минут")
    if await s.get(User, n.sender_id) is None:
        raise HTTPException(410, "Этого аккаунта больше нет")
    security.messages.hit(user.id)
    await notify.push(s, [n.sender_id], f"💬 Ответ: {user.display_name}",
                      f"На «{n.title}»\n\n{body.text.strip()}", kind=notify.KIND_REPLY, sender_id=user.id)
    await notify.mark_read(s, user.id, [n.id])
    await s.commit()
    return {"ok": True}


@api.post("/announce")
async def announce(body: AnnounceIn, user: StaffDep, s: SessionDep):
    """Объявление группе: староста — своей, админ — любым."""
    groups = sorted({clean_group(g) for g in body.groups})
    for g in groups:
        if not user.can_manage(g):
            raise HTTPException(403, "Староста пишет объявления только своей группе")
    if not body.text.strip():
        raise HTTPException(422, "Напиши текст объявления")
    recipients = await notify.group_member_ids(s, groups)
    who = roles.actor_label(user)
    where = groups[0] if len(groups) == 1 else f"{len(groups)} групп"
    sent = await notify.push(s, recipients, f"📣 {who} · {where}", body.text.strip(),
                             kind=notify.KIND_ANNOUNCE, sender_id=user.id, skip=[user.id])
    await s.commit()
    log.info("%s %s отправил объявление в %s: %d получателей", user.role, user.id, ", ".join(groups), sent)
    return {"delivered": sent}
