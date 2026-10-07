"""Аккаунты с ФИО, которое не похоже на настоящее («д.д.д», «1 1 1», «admin t t») — обычно пробные.

    python -m app.bad_names          — показать такие аккаунты
    python -m app.bad_names --yes    — удалить их (главного админа не трогает)

Перед удалением копия базы сохраняется в папку бэкапов. Вместе с аккаунтом удаляется
и его строка в списке группы, если там то же неправильное ФИО.
На сервере: docker exec raspisanie-app-1 python -m app.bad_names --yes
"""

import asyncio
import sys
from datetime import datetime

from sqlalchemy import select

from app import backup, db
from app.config import config
from app.models import ROLE_OWNER, Student, User
from app.users import AccountError, person_fio


def is_bad(name: str | None) -> bool:
    try:
        person_fio(name)
    except AccountError:
        return True
    return False


async def find(s) -> list[User]:
    found = []
    for user in (await s.scalars(select(User).order_by(User.id))).unique().all():
        await s.refresh(user, attribute_names=["student", "teacher"])
        if is_bad(user.full_name) or (user.student is not None and is_bad(user.student.full_name)):
            found.append(user)
    return found


async def run(confirm: bool, backup_dir: str | None = None) -> list[str]:
    """Вернёт строки отчёта; с confirm — удаляет."""
    await db.init_db()
    lines = []
    async with db.session() as s:
        users = await find(s)
        if not users:
            return ["Аккаунтов с неправильным ФИО нет."]
        if confirm:
            lines.append(f"Копия базы до удаления: {await backup.save_backup(datetime.now(config.tz), backup_dir)}")
        for user in users:
            who = f"#{user.id} {user.login}: «{user.display_name}»" + (f", группа {user.group_name}" if user.group_name else "")
            if user.role == ROLE_OWNER:
                lines.append(f"{who} — главный админ, не удаляю: смени ФИО в профиле")
                continue
            if not confirm:
                lines.append(who)
                continue
            student: Student | None = user.student
            if student is not None and is_bad(student.full_name):
                await s.delete(student)
            await s.delete(user)
            lines.append(f"{who} — удалён")
        await s.commit()
    if not confirm:
        lines.append("Чтобы удалить их, запусти с --yes")
    return lines


if __name__ == "__main__":
    for line in asyncio.run(run("--yes" in sys.argv[1:])):
        print(line)
