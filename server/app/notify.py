"""Уведомления в приложении. Лежат в базе: приложение показывает их лентой, а на телефоне —
системными уведомлениями (фоновая проверка раз в ~15 минут и сразу при открытии)."""

from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ROLE_ADMIN, ROLE_OWNER, ROLE_STAROSTA, Notification, Student, User
from app.security import utcnow

KIND_GROUP = "group"         # добавили в группу, новый студент в группе
KIND_ROLE = "role"           # назначили старостой, админом
KIND_CONTACT = "contact"     # студент написал админам
KIND_REPLY = "reply"         # ответ на сообщение
KIND_ANNOUNCE = "announce"   # объявление старосты или админа
KIND_SCHEDULE = "schedule"   # изменилось расписание группы
KIND_TEACHER = "teacher"     # новый преподаватель
KIND_INFO = "info"

# На такие уведомления можно ответить отправителю
REPLYABLE = {KIND_CONTACT, KIND_REPLY, KIND_ANNOUNCE}

TITLE_MAX = 200
BODY_MAX = 4000


async def push(
    s: AsyncSession, user_ids: Iterable[int], title: str, body: str = "",
    kind: str = KIND_INFO, sender_id: int | None = None, skip: Iterable[int] = (),
) -> int:
    """Кладёт уведомление каждому получателю (без повторов). Коммит — на вызывающем."""
    skip = set(skip)
    now = utcnow()
    count = 0
    for uid in dict.fromkeys(user_ids):
        if uid is None or uid in skip:
            continue
        s.add(Notification(user_id=uid, kind=kind, title=title[:TITLE_MAX], body=body[:BODY_MAX],
                           sender_id=sender_id, created_at=now))
        count += 1
    return count


async def group_member_ids(s: AsyncSession, groups: Iterable[str]) -> list[int]:
    groups = list(groups)
    if not groups:
        return []
    return list((await s.scalars(
        select(User.id).join(Student, User.student_id == Student.id).where(Student.group_name.in_(groups))
    )).all())


async def starosta_ids(s: AsyncSession, group: str) -> list[int]:
    return list((await s.scalars(
        select(User.id).join(Student, User.student_id == Student.id)
        .where(User.role == ROLE_STAROSTA, Student.group_name == group)
    )).all())


async def owner_ids(s: AsyncSession) -> list[int]:
    return list((await s.scalars(select(User.id).where(User.role == ROLE_OWNER))).all())


async def admin_ids(s: AsyncSession) -> list[int]:
    return list((await s.scalars(select(User.id).where(User.role.in_([ROLE_ADMIN, ROLE_OWNER])))).all())


async def unread_count(s: AsyncSession, user_id: int) -> int:
    return await s.scalar(
        select(func.count(Notification.id)).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    ) or 0


async def mark_read(s: AsyncSession, user_id: int, ids: list[int] | None = None) -> None:
    stmt = update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    if ids is not None:
        stmt = stmt.where(Notification.id.in_(ids))
    await s.execute(stmt.values(read_at=utcnow()))


def to_dict(n: Notification, sender: User | None = None) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "created_at": n.created_at.isoformat() + "Z",
        "read": n.read_at is not None,
        "sender": {"id": sender.id, "name": sender.display_name} if sender else None,
        "can_reply": n.kind in REPLYABLE and n.sender_id is not None,
    }
