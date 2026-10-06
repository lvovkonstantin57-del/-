"""Ответ-файл для скачивания."""

import re
from urllib.parse import quote

from fastapi.responses import Response


def attachment(data: bytes, filename: str, media_type: str) -> Response:
    """Файл для скачивания: русское имя — через filename*, приложение читает его из заголовка."""
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "file"
    return Response(data, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
    })
