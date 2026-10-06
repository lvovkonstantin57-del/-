"""Поле «Ввести код» в профиле: код группы, приглашение преподавателя или код главного админа."""

import hmac
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app import attendance, roles, security, users
from app.config import config
from app.models import ROLE_OWNER, User
from app.users import AccountError

log = logging.getLogger(__name__)


def _is_owner_code(text: str) -> bool:
    return bool(config.owner_code) and hmac.compare_digest(text.encode(), config.owner_code.encode())


async def apply_code(s: AsyncSession, user: User, text: str | None) -> dict:
    """Применяет код. Возвращает {"kind": group|teacher|owner, "message": ...} или кидает AccountError."""
    raw = (text or "").strip()
    if not raw:
        raise AccountError("Введи код", 422)
    key = ("code", user.id)
    if security.code_fails.blocked(key):
        raise AccountError(f"Слишком много неверных кодов. Попробуй через {security.code_fails.wait_minutes(key)} мин", 429)

    if _is_owner_code(raw):
        if user.is_owner:
            return {"kind": "owner", "message": "Ты уже главный админ"}
        user.role = ROLE_OWNER
        await s.commit()
        log.warning("Пользователь %s стал главным админом по коду", user.id)
        return {"kind": "owner", "message": "Ты главный админ 👑 Во вкладке «Я админ» — всё управление."}

    invite = await attendance.valid_invite(s, raw)
    if invite is not None:
        if user.is_teacher:
            return {"kind": "teacher", "message": "Ты уже преподаватель — код не потрачен, его можно отдать коллеге"}
        teacher = await attendance.register_teacher(s, user.id, invite.code, user.full_name)
        profile = await attendance.teacher_profile(s, teacher)
        await attendance.notify_owners(
            s, "🎓 Новый преподаватель",
            user.full_name + (f"\nВ расписании: {profile['schedule_name']}" if profile["schedule_name"]
                              else "\nВ расписании пока не найден"),
            skip=user.id,
        )
        await s.commit()
        await s.refresh(user, attribute_names=["teacher"])
        found = (f"В расписании нашлись ваши пары: {profile['lessons']}." if profile["lessons"]
                 else "Пар с вашей фамилией в расписании пока нет — они подтянутся сами, "
                      "когда админ загрузит расписание. Если вы записаны иначе — «Я учитель» → «В расписании».")
        return {"kind": "teacher", "message": f"Вы зарегистрированы как преподаватель 🎓 {found}"}

    group = await users.group_by_code(s, raw)
    if group is not None:
        if user.student is not None:
            if user.group_name == group:
                return {"kind": "group", "group": group, "message": f"Ты уже в группе {group}"}
            raise AccountError(
                f"Ты уже в группе {user.group_name}. Перейти в другую поможет староста или админ", 409)
        student = await users.join_group(s, user, group)
        await roles.notify_joined(s, user, student)
        await s.commit()
        security.code_fails.reset(key)
        return {"kind": "group", "group": group, "message": f"Готово! Ты в группе {group} ✅"}

    other = await users.user_by_code(s, raw)
    security.code_fails.hit(key)
    if other is not None and other.id == user.id:
        raise AccountError("Это твой личный код — покажи его старосте, и он добавит тебя в группу. "
                           "А код группы спроси у старосты")
    if other is not None:
        raise AccountError("Это личный код человека, а не код группы. Код группы спроси у старосты")
    raise AccountError("Код не подошёл. Проверь его или попроси новый", 404)
