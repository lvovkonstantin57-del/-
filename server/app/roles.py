"""Роли и права: главный админ, админ, староста, студент. И уведомления о них."""

import logging

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import db, notify
from app.models import ROLE_ADMIN, ROLE_OWNER, ROLE_STAROSTA, ROLE_USER, Student, User

log = logging.getLogger(__name__)

ROLE_LABELS = {
    ROLE_OWNER: "главный админ",
    ROLE_ADMIN: "админ",
    ROLE_STAROSTA: "староста",
    ROLE_USER: "студент",
}
ASSIGNABLE = (ROLE_USER, ROLE_STAROSTA, ROLE_ADMIN)


class RoleError(Exception):
    """Роль поменять нельзя; текст — для человека."""


async def manageable_groups(s: AsyncSession, user: User) -> list[str]:
    """Группы, где можно менять студентов и расписание."""
    if user.is_admin:
        return await db.list_groups(s)
    return [user.group_name] if user.is_starosta else []


def check_role_change(actor: User, target: User, role: str) -> None:
    if role not in ASSIGNABLE:
        raise RoleError("Неизвестная роль")
    if not actor.is_admin:
        raise RoleError("Роли меняют только админы")
    if target.id == actor.id:
        raise RoleError("Свою роль поменять нельзя")
    if target.is_owner:
        raise RoleError("Главного админа поменять нельзя")
    if ROLE_ADMIN in (role, target.role) and not actor.is_owner:
        raise RoleError("Админов назначает и снимает только главный админ")
    if role == ROLE_STAROSTA and target.student is None:
        raise RoleError("Старостой можно сделать только студента, который уже в группе")


async def set_role(s: AsyncSession, actor: User, target: User, role: str) -> str | None:
    """Меняет роль, сообщает человеку и сохраняет. Возвращает прежнюю роль или None, если ничего не поменялось."""
    check_role_change(actor, target, role)
    old = target.effective_role
    if old == role:
        return None
    target.role = role
    await announce_role(s, target, old)
    await s.commit()
    log.info("%s %s сменил роль %s: %s → %s", actor.role, actor.id, target.id, old, role)
    return old


async def announce_role(s: AsyncSession, user: User, old_role: str) -> None:
    """Уведомление человеку о новой роли (без коммита)."""
    role = user.effective_role
    if role == ROLE_STAROSTA:
        title = f"⭐ Ты староста группы {user.group_name}"
        body = ("Теперь в своей группе ты можешь добавлять студентов по их личному коду, делиться кодом группы, "
                "править пары и писать объявления. Всё это — во вкладке «Я староста».")
    elif role == ROLE_ADMIN:
        title = "⚙️ Тебя назначили админом"
        body = "Во вкладке «Я админ» — группы, студенты, расписание, роли и настройки семестра."
    elif role == ROLE_OWNER:
        title = "👑 Ты главный админ"
        body = "Во вкладке «Я админ» — всё управление: группы, роли, преподаватели, бэкап."
    elif old_role == ROLE_STAROSTA:
        title, body = "Ты больше не староста", "Расписание и уведомления работают как раньше."
    elif old_role in (ROLE_ADMIN, ROLE_OWNER):
        title, body = "Права админа сняты", "Расписание и уведомления работают как раньше."
    else:
        return
    await notify.push(s, [user.id], title, body, kind=notify.KIND_ROLE)


async def unlink_students(s: AsyncSession, student_ids: list[int]) -> list[int]:
    """Отвязывает аккаунты от записей в списке. Староста без группы — уже не староста.
    Возвращает id аккаунтов, которые отвязали."""
    if not student_ids:
        return []
    users = list((await s.scalars(select(User.id).where(User.student_id.in_(student_ids)))).all())
    await s.execute(
        update(User)
        .where(User.student_id.in_(student_ids), User.role == ROLE_STAROSTA)
        .values(role=ROLE_USER)
    )
    await s.execute(
        update(User).where(User.student_id.in_(student_ids)).values(student_id=None, half=None)
    )
    return users


def actor_label(actor: User) -> str:
    who = "Староста" if actor.effective_role == ROLE_STAROSTA else "Админ"
    return f"{who} {actor.display_name}"


async def notify_student_changed(s: AsyncSession, user_id: int, details: str, actor: User) -> None:
    """Студенту: его данные в списке поправили (без коммита)."""
    if user_id == actor.id:
        return
    await notify.push(s, [user_id], "ℹ️ Твои данные обновили", f"{details}\n\n{actor_label(actor)}",
                      kind=notify.KIND_GROUP, sender_id=actor.id)


async def notify_joined(s: AsyncSession, user: User, student: Student, actor: User | None = None) -> None:
    """Студенту — что он в группе; старостам группы и главному админу — что в группе новый студент.
    Без коммита."""
    group = student.group_name
    total = await s.scalar(select(func.count(Student.id)).where(Student.group_name == group))
    registered = await s.scalar(
        select(func.count(User.id)).join(Student, User.student_id == Student.id).where(Student.group_name == group)
    )
    if actor is not None and actor.id != user.id:
        await notify.push(s, [user.id], f"✅ Ты в группе {group}",
                          f"Добавил: {actor_label(actor).lower()}. Расписание группы уже в приложении.",
                          kind=notify.KIND_GROUP, sender_id=actor.id)
    body = f"{student.full_name} · код {user.code[:3]}-{user.code[3:]}\nВ приложении {registered} из {total}"
    skip = {user.id} | ({actor.id} if actor else set())
    await notify.push(s, await notify.starosta_ids(s, group), f"⭐ Новый студент в группе {group}", body,
                      kind=notify.KIND_GROUP, skip=skip)
    await notify.push(s, await notify.owner_ids(s), f"⚙️ Новый студент · {group}", body,
                      kind=notify.KIND_GROUP, skip=skip | set(await notify.starosta_ids(s, group)))
