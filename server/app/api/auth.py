"""Регистрация, вход и аккаунт: логин и пароль, без Telegram."""

import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete

from app import codes, security, users
from app.api.deps import SessionDep, UserDep, bearer, client_ip
from app.models import AuthToken
from app.users import AccountError

log = logging.getLogger(__name__)
api = APIRouter(prefix="/api")


class RegisterIn(BaseModel):
    full_name: str = Field(min_length=3, max_length=200)
    login: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    # Код группы от старосты или приглашение преподавателя — можно ввести сразу
    code: str | None = Field(default=None, max_length=100)
    device: str = Field(default="", max_length=100)


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    device: str = Field(default="", max_length=100)


class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class DeleteIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=100)


@api.post("/auth/register")
async def register(body: RegisterIn, request: Request, s: SessionDep):
    ip = client_ip(request)
    if security.signups.blocked(ip):
        raise HTTPException(429, "Слишком много регистраций с этого адреса — попробуй позже")
    user = await users.create_user(s, body.login, body.password, body.full_name)
    security.signups.hit(ip)
    log.info("Новый аккаунт %s", user.id)
    result: dict = {"token": await users.issue_token(s, user, body.device)}
    if body.code and body.code.strip():
        try:
            result["code_result"] = await codes.apply_code(s, user, body.code)
        except AccountError as e:
            # Аккаунт уже создан — код можно ввести ещё раз в профиле
            result["code_error"] = str(e)
    return result


@api.post("/auth/login")
async def login(body: LoginIn, request: Request, s: SessionDep):
    key = body.login.strip().lower()
    ip = client_ip(request)
    for limit, k in ((security.login_fails, key), (security.login_fails_ip, ip)):
        if limit.blocked(k):
            raise HTTPException(429, f"Слишком много попыток. Подожди {limit.wait_minutes(k)} мин")
    user = await users.authenticate(s, body.login, body.password)
    if user is None:
        security.login_fails.hit(key)
        security.login_fails_ip.hit(ip)
        raise HTTPException(401, "Неверный логин или пароль")
    security.login_fails.reset(key)
    return {"token": await users.issue_token(s, user, body.device)}


@api.post("/auth/logout")
async def logout(s: SessionDep, authorization: Annotated[str | None, Header()] = None):
    token = bearer(authorization)
    if token:
        await s.execute(delete(AuthToken).where(AuthToken.token_hash == security.hash_token(token)))
        await s.commit()
    return {"ok": True}


@api.put("/me/password")
async def change_password(body: PasswordIn, user: UserDep, s: SessionDep,
                          authorization: Annotated[str | None, Header()] = None):
    if not security.verify_password(body.old_password, user.password_hash):
        raise HTTPException(403, "Старый пароль не подходит")
    await users.set_password(s, user, body.new_password, keep_token=bearer(authorization))
    return {"ok": True}


@api.post("/me/delete")
async def delete_account(body: DeleteIn, user: UserDep, s: SessionDep):
    """Удалить аккаунт. Запись в списке группы и журнал посещаемости остаются."""
    if not security.verify_password(body.password, user.password_hash):
        raise HTTPException(403, "Пароль не подходит")
    await s.delete(user)
    await s.commit()
    log.info("Аккаунт %s удалён владельцем", user.id)
    return {"ok": True}


@api.post("/me/code")
async def enter_code(body: CodeIn, user: UserDep, s: SessionDep):
    return await codes.apply_code(s, user, body.code)
