"""Картинки в базе: фото профиля. Приложение само уменьшает фото до квадрата и шлёт JPEG;
сервер проверяет, что это JPEG, и вырезает метаданные (EXIF с геопозицией, комментарии)."""

import base64
import binascii
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Picture, User

MAX_PHOTO = 400 * 1024
KEY_LEN = 32


class PictureError(ValueError):
    pass


def url(key: str | None) -> str | None:
    return f"/api/pictures/{key}" if key else None


def decode_data_url(text: str) -> bytes:
    """«data:image/jpeg;base64,…» или просто base64 → байты."""
    if text.startswith("data:"):
        head, _, text = text.partition(",")
        if ";base64" not in head:
            raise PictureError("Нужна картинка в base64")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as e:
        raise PictureError("Картинка повреждена") from e


def clean_jpeg(data: bytes) -> bytes:
    """Проверяет JPEG и выбрасывает APP1–APP15 (EXIF, XMP, геопозиция) и комментарии."""
    if data[:3] != b"\xff\xd8\xff":
        raise PictureError("Нужна фотография в формате JPEG")
    out = bytearray(b"\xff\xd8")
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            raise PictureError("Фотография повреждена")
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            break
        marker = data[i]
        i += 1
        if marker == 0xD9:
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out += bytes((0xFF, marker))
            continue
        if i + 2 > len(data):
            raise PictureError("Фотография повреждена")
        length = int.from_bytes(data[i:i + 2], "big")
        if length < 2 or i + length > len(data):
            raise PictureError("Фотография повреждена")
        segment = data[i:i + length]
        i += length
        if marker == 0xDA:
            # Дальше сжатая картинка — копируем как есть
            return bytes(out + bytes((0xFF, marker)) + segment + data[i:])
        if 0xE1 <= marker <= 0xEF or marker == 0xFE:
            continue
        out += bytes((0xFF, marker)) + segment
    raise PictureError("Фотография повреждена")


async def set_photo(s: AsyncSession, user: User, data_url: str) -> str:
    data = decode_data_url(data_url)
    if len(data) > MAX_PHOTO:
        raise PictureError("Фото больше 400 КБ — выбери другое")
    data = clean_jpeg(data)
    await remove_photo(s, user)
    key = secrets.token_hex(KEY_LEN // 2)
    s.add(Picture(key=key, mime="image/jpeg", data=data))
    user.photo = key
    return key


async def remove_photo(s: AsyncSession, user: User) -> None:
    if user.photo:
        old = await s.get(Picture, user.photo)
        if old is not None:
            await s.delete(old)
        user.photo = None
