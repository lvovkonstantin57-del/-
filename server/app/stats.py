"""Статистика для админов: аккаунты, группы, уведомления, расписание."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db, schedule
from app.models import Lesson, Student, Teacher, User


async def collect(s: AsyncSession) -> dict:
    students = (await s.scalars(select(Student).order_by(Student.group_name, Student.full_name))).all()
    users = (await s.scalars(select(User))).all()
    linked = {u.student_id: u for u in users if u.student_id}
    registered = list(linked.values())
    groups = await db.list_groups(s)
    with_halves = {g for g in groups if await schedule.group_has_halves(s, g)}
    per_group = []
    for g in groups:
        in_group = [st for st in students if st.group_name == g]
        per_group.append({
            "group": g,
            "students": len(in_group),
            "registered": sum(1 for st in in_group if st.id in linked),
            # Записи из xlsx, к которым ещё никто не привязался
            "missing": [st.full_name for st in in_group if st.id not in linked],
        })
    return {
        "accounts": len(users),
        # Зарегистрировались, но ещё не в группе: им надо показать личный код старосте
        "without_group": sum(1 for u in users if u.student_id is None and not u.is_admin and not u.is_teacher),
        "students": len(students),
        "registered": len(registered),
        "reminders": sum(1 for u in registered if u.notify_before),
        "digest": sum(1 for u in registered if u.digest_time),
        "half_unset": sum(1 for u in registered if u.student.group_name in with_halves and u.half is None),
        "admins": sum(1 for u in users if u.is_admin),
        "starostas": sum(1 for u in users if u.is_starosta),
        "teachers": await s.scalar(select(func.count(Teacher.user_id))),
        "lessons": await s.scalar(select(func.count(Lesson.id))),
        "groups": per_group,
    }
