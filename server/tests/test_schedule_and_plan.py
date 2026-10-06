from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import plan, schedule
from app.config import config
from app.models import Student, Teacher, User

START = date(2026, 9, 1)  # вторник


@pytest.mark.parametrize("d,num,parity", [
    (date(2026, 8, 31), 1, "odd"),   # понедельник той же недели
    (date(2026, 9, 6), 1, "odd"),
    (date(2026, 9, 7), 2, "even"),
    (date(2026, 10, 4), 5, "odd"),
    (date(2026, 10, 5), 6, "even"),
    (date(2026, 8, 24), 0, "even"),  # неделя до начала семестра
])
def test_week_parity(d, num, parity):
    assert schedule.week_number(d, START) == num
    assert schedule.week_parity(d, START) == parity


async def _student(s, half=None, notify_before=15, digest_time="20:00", digest_day="tomorrow") -> User:
    sid = await s.scalar(select(Student.id).where(Student.full_name == "Тестов Тест Тестович"))
    user = User(login="test", password_hash="x", full_name="Тестов Тест Тестович", code="ABC234",
                student_id=sid, half=half, notify_before=notify_before, digest_time=digest_time, digest_day=digest_day)
    s.add(user)
    await s.commit()
    await s.refresh(user, attribute_names=["student", "teacher"])
    return user


async def test_lessons_on_respects_parity_and_half(seeded):
    async with seeded.session() as s:
        odd_mon, even_mon = date(2026, 10, 5) - timedelta(days=7), date(2026, 10, 5)
        assert [l.subject for l in await schedule.lessons_on(s, "ГР 1", odd_mon, None)] == [
            "Физкультура", "Ботаника"]
        assert [l.subject for l in await schedule.lessons_on(s, "ГР 1", even_mon, None)] == [
            "Физкультура", "Зоология"]
        odd_thu = date(2026, 10, 8) - timedelta(days=7)
        assert [l.half for l in await schedule.lessons_on(s, "ГР 1", odd_thu, None)] == [1, 2]
        assert [l.start_time for l in await schedule.lessons_on(s, "ГР 1", odd_thu, 2)] == ["16:00"]
        assert await schedule.group_has_halves(s, "ГР 1")
        assert not await schedule.group_has_halves(s, "ГР 2")


def at(*args) -> datetime:
    return datetime(*args, tzinfo=config.tz)


async def test_plan_reminders_and_digest(seeded):
    async with seeded.session() as s:
        user = await _student(s)
        # Вс 27.09, 19:00: сводка в 20:00 на завтра, потом пн (нечётная): физкультура 09:00 и ботаника 10:40
        items = await plan.build(s, user, now=at(2026, 9, 27, 19, 0), days=2)
    assert [(i["kind"], i["at"][11:16]) for i in items] == [
        ("digest", "20:00"), ("reminder", "08:45"), ("reminder", "10:25"), ("digest", "20:00")]
    digest, first = items[0], items[1]
    assert digest["title"] == "Завтра 2 пары, первая в 09:00"
    assert digest["body"] == "09:00 Физкультура · Спортзал\n10:40 Ботаника · ауд. 304"
    assert first["title"] == "Физкультура через 15 мин"
    assert first["body"] == "09:00–10:30 · Спортзал · практика · Петров П. П."
    assert first["at"] == "2026-09-28T08:45:00+03:00"
    # Вторник у ГР 1 свободен
    assert items[3]["title"] == "Завтра пар нет 🎉"
    # Номера уведомлений стабильные — повторный план не создаёт дублей на телефоне
    async with seeded.session() as s:
        user = await s.scalar(select(User).where(User.login == "test"))
        again = await plan.build(s, user, now=at(2026, 9, 27, 19, 0), days=2)
    assert [i["id"] for i in again] == [i["id"] for i in items]
    assert all(0 < i["id"] < 2 ** 31 for i in items)


async def test_plan_skips_past_and_respects_half(seeded):
    async with seeded.session() as s:
        user = await _student(s, half=2, digest_time=None)
        # Чт 01.10 (нечётная), 12:00: у 2-й половины анатомия в 16:00, у 1-й в 12:40 — её не напоминаем
        items = await plan.build(s, user, now=at(2026, 10, 1, 12, 0), days=1)
        assert [(i["title"], i["at"][11:16]) for i in items] == [("Анатомия через 15 мин", "15:45")]
        # Половина не выбрана — напоминаем обе, с пометкой
        user.half = None
        items = await plan.build(s, user, now=at(2026, 10, 1, 12, 0), days=1)
        assert [i["at"][11:16] for i in items] == ["12:25", "15:45"]
        assert items[0]["body"].endswith("1-я половина")
        # Уведомления выключены — плана нет
        user.notify_before = None
        assert await plan.build(s, user, now=at(2026, 10, 1, 12, 0)) == []


async def test_plan_digest_today_and_limit(seeded):
    async with seeded.session() as s:
        user = await _student(s, notify_before=5, digest_time="07:30", digest_day="today")
        items = await plan.build(s, user, now=at(2026, 9, 28, 7, 0), days=1)
        assert items[0]["kind"] == "digest" and items[0]["title"] == "Сегодня 2 пары, первая в 09:00"
        many = await plan.build(s, user, now=at(2026, 9, 28, 7, 0), days=21)
        assert len(many) <= plan.MAX_ITEMS


async def test_plan_for_teacher(seeded):
    async with seeded.session() as s:
        user = User(login="sid", password_hash="x", full_name="Сидоров Сергей Сергеевич", code="TEA234",
                    notify_before=10, digest_day="tomorrow")
        s.add(user)
        await s.flush()
        s.add(Teacher(user_id=user.id, full_name=user.full_name))
        await s.commit()
        await s.refresh(user, attribute_names=["student", "teacher"])
        # Пн 05.10 — чётная: зоология у ГР 1
        items = await plan.build(s, user, now=at(2026, 10, 5, 8, 0), days=1)
    assert [(i["title"], i["body"]) for i in items] == [
        ("Зоология через 10 мин", "10:40–12:10 · ауд. 304 · ГР 1")]
