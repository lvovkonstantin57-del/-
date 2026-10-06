"""Общие зависимости API: сессия БД, текущий пользователь и проверки прав."""

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import db, security
from app.models import AuthToken, Teacher, User

SESSION_TTL = timedelta(days=180)   # без использования — выходим
TOUCH_EVERY = timedelta(hours=12)   # как часто обновлять last_used_at


async def get_session():
    async with db.session() as s:
        yield s


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def bearer(authorization: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:].strip() or None
    return None


async def user_by_token(s: AsyncSession, token: str) -> User | None:
    row = await s.get(AuthToken, security.hash_token(token))
    if row is None:
        return None
    now = security.utcnow()
    if row.last_used_at < now - SESSION_TTL:
        await s.delete(row)
        await s.commit()
        return None
    if row.last_used_at < now - TOUCH_EVERY:
        row.last_used_at = now
        await s.commit()
    user = await s.get(User, row.user_id)
    if user is not None:
        await s.refresh(user, attribute_names=["student", "teacher"])
    return user


async def current_user(s: SessionDep, authorization: Annotated[str | None, Header()] = None) -> User:
    token = bearer(authorization)
    if token is None:
        raise HTTPException(401, "Войди в аккаунт")
    user = await user_by_token(s, token)
    if user is None:
        raise HTTPException(401, "Сессия истекла — войди заново")
    return user


UserDep = Annotated[User, Depends(current_user)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def require_admin(user: UserDep) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Только для админов")
    return user


def require_owner(user: UserDep) -> User:
    if not user.is_owner:
        raise HTTPException(403, "Только для главного админа")
    return user


def require_staff(user: UserDep) -> User:
    """Админ или староста (у старосты права только на свою группу)."""
    if not user.is_staff:
        raise HTTPException(403, "Только для админов и старост")
    return user


AdminDep = Annotated[User, Depends(require_admin)]
OwnerDep = Annotated[User, Depends(require_owner)]
StaffDep = Annotated[User, Depends(require_staff)]


def check_group(user: User, group: str | None) -> None:
    if not user.can_manage(group):
        raise HTTPException(403, "Староста может менять только свою группу")


def require_teacher(user: UserDep) -> Teacher:
    if user.teacher is None:
        raise HTTPException(403, "Только для преподавателей")
    return user.teacher


TeacherDep = Annotated[Teacher, Depends(require_teacher)]
