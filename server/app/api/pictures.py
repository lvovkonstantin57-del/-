"""Фото профиля: загрузить, удалить, показать."""

import re

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app import notify, pictures, roles
from app.api.deps import SessionDep, StaffDep, UserDep
from app.models import Picture, User

api = APIRouter(prefix="/api")

KEY_RE = re.compile(r"^[0-9a-f]{32}$")


class PhotoIn(BaseModel):
    # JPEG в base64 (data URL); приложение уже уменьшило его до 512×512
    image: str = Field(min_length=10, max_length=600_000)


@api.get("/pictures/{key}")
async def get_picture(key: str, s: SessionDep):
    """Без входа: ключ случайный и меняется при каждой замене фото, поэтому кэшируется навсегда."""
    pic = await s.get(Picture, key) if KEY_RE.match(key) else None
    if pic is None:
        raise HTTPException(404, "Нет такой картинки")
    return Response(pic.data, media_type=pic.mime, headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })


@api.post("/me/photo")
async def upload_photo(body: PhotoIn, user: UserDep, s: SessionDep):
    try:
        await pictures.set_photo(s, user, body.image)
    except pictures.PictureError as e:
        raise HTTPException(422, str(e)) from e
    await s.commit()
    return {"photo": pictures.url(user.photo)}


@api.delete("/me/photo")
async def delete_photo(user: UserDep, s: SessionDep):
    await pictures.remove_photo(s, user)
    await s.commit()
    return {"ok": True}


@api.delete("/admin/users/{user_id}/photo")
async def delete_user_photo(user_id: int, user: StaffDep, s: SessionDep):
    """Убрать неподходящее фото: староста — у своей группы, админ — у всех."""
    target = await s.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Пользователь не найден")
    if not user.is_admin and not user.can_manage(target.group_name):
        raise HTTPException(403, "Староста может убрать фото только у своей группы")
    if not target.photo:
        return {"ok": True}
    await pictures.remove_photo(s, target)
    if target.id != user.id:
        await notify.push(s, [target.id], "Фото профиля убрано",
                          f"{roles.actor_label(user)} убрал твоё фото. Можно поставить другое в профиле.",
                          kind=notify.KIND_INFO, sender_id=user.id)
    await s.commit()
    return {"ok": True}
