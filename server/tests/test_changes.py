"""Разовые изменения расписания, свои недели повторения, уведомления о смене аудитории, фото профиля."""

import base64
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import attendance, changes, schedule
from app.models import CHANGE_CANCEL, CHANGE_EDIT, Lesson, LessonChange, Teacher, User
from tests.conftest import SCHEDULE_HEADER, make_xlsx, owner, register


def next_weekday(weekday: int) -> date:
    """Ближайший такой день недели после сегодняшнего (не сегодня — чтобы пара была в будущем)."""
    d = changes.today() + timedelta(days=1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


TUESDAY = 1  # у ГР 2 во вторник Химия, ауд. 401


async def _setup(client):
    boss = await owner(client)
    code = (await client.get("/api/admin/group-code", params={"group": "ГР 2"}, headers=boss.h)).json()["code"]
    stud = await register(client, "elkin", "Ёлкин Пётр Иванович", code=code)
    tue = next_weekday(TUESDAY)
    week = (await client.get("/api/schedule", params={"date": tue.isoformat()}, headers=stud.h)).json()
    lesson = next(l for d in week["days"] if d["date"] == tue.isoformat() for l in d["lessons"])
    return boss, stud, tue, lesson


async def _inbox(client, acc) -> list[dict]:
    return (await client.get("/api/notifications", headers=acc.h)).json()["items"]


async def _day(client, acc, d: date) -> dict:
    week = (await client.get("/api/schedule", params={"date": d.isoformat()}, headers=acc.h)).json()
    return next(x for x in week["days"] if x["date"] == d.isoformat())


# --- свои недели ----------------------------------------------------------------

@pytest.mark.parametrize("raw,stored,label", [
    ("1-4, 6", "1-4,6", "недели 1–4, 6"),
    ("7", "7", "неделя 7"),
    ("нед. 2 / 3", "2/3", "каждая 3-я неделя с 2-й"),
    ("1-16/2", "1-16/2", "недели 1–16 через 1"),
    ("5/1", "5/1", "с 5-й недели"),
])
def test_weeks_format_and_label(raw, stored, label):
    assert schedule.format_weeks(raw) == stored
    assert schedule.week_text("custom", stored) == label


def test_weeks_match():
    assert [n for n in range(1, 12) if schedule.weeks_match("2/3", n)] == [2, 5, 8, 11]
    assert [n for n in range(1, 12) if schedule.weeks_match("1-4,9", n)] == [1, 2, 3, 4, 9]
    assert [n for n in range(1, 12) if schedule.weeks_match("3-9/3", n)] == [3, 6, 9]
    for bad in ("", "abc", "0", "5-2", "1/0", "61"):
        with pytest.raises(ValueError):
            schedule.parse_weeks(bad)


async def test_lesson_with_custom_weeks(client, seeded):
    boss = await owner(client)
    code = (await client.get("/api/admin/group-code", params={"group": "ГР 2"}, headers=boss.h)).json()["code"]
    stud = await register(client, "elkin", "Ёлкин Пётр Иванович", code=code)
    wed = next_weekday(2)
    n = schedule.week_number(wed, date(2026, 9, 1))
    body = {"group": "ГР 2", "weekday": 2, "week": "custom", "weeks": f"{n}, {n + 2}", "start": "15:00",
            "end": "16:30", "subject": "Факультатив", "room": "200"}
    r = await client.post("/api/admin/lessons", json=body, headers=boss.h)
    assert r.status_code == 200, r.text
    assert r.json()["weeks"] == f"{n},{n + 2}" and r.json()["week_label"] == f"недели {n}, {n + 2}"
    subjects = lambda day: [l["subject"] for l in day["lessons"]]  # noqa: E731
    assert subjects(await _day(client, stud, wed)) == ["Факультатив"]
    assert subjects(await _day(client, stud, wed + timedelta(days=7))) == []
    assert subjects(await _day(client, stud, wed + timedelta(days=14))) == ["Факультатив"]

    r = await client.post("/api/admin/lessons", json={**body, "weeks": "когда-нибудь"}, headers=boss.h)
    assert r.status_code == 422 and "Недели" in r.text


# --- разовые изменения ----------------------------------------------------------

async def test_one_off_room_change(client, seeded):
    boss, stud, tue, lesson = await _setup(client)
    await client.put("/api/me/settings", json={"notify_before": 15}, headers=stud.h)
    r = await client.post("/api/admin/changes", json={
        "group": "ГР 2", "date": tue.isoformat(), "action": "change", "lesson_id": lesson["id"], "room": "410",
    }, headers=boss.h)
    assert r.status_code == 200, r.text

    note = (await _inbox(client, stud))[0]
    assert note["title"] == "🚪 Другая аудитория · ГР 2" and note["kind"] == "schedule"
    assert "Химия в 09:00 — ауд. 410 (вместо 401)" in note["body"]

    day = (await _day(client, stud, tue))["lessons"][0]
    assert (day["room"], day["status"], day["was"]) == ("410", "changed", {"room": "401"})
    # Через неделю — как обычно
    assert (await _day(client, stud, tue + timedelta(days=7)))["lessons"][0]["room"] == "401"
    # Напоминание тоже с новой аудиторией
    plan = (await client.get("/api/me/plan", headers=stud.h)).json()["items"]
    assert any("ауд. 410 (вместо 401)" in i["body"] for i in plan if i["kind"] == "reminder")

    listed = (await client.get("/api/admin/changes", params={"group": "ГР 2"}, headers=boss.h)).json()
    assert len(listed) == 1 and listed[0]["summary"].endswith("Химия в 09:00 — ауд. 410 вместо 401")
    assert (await client.delete(f"/api/admin/changes/{listed[0]['id']}", headers=boss.h)).status_code == 200
    assert (await _inbox(client, stud))[0]["title"] == "↩️ Снова по расписанию · ГР 2"
    assert (await _day(client, stud, tue))["lessons"][0]["room"] == "401"


async def test_cancel_and_move(client, seeded):
    boss, stud, tue, lesson = await _setup(client)
    base = {"group": "ГР 2", "date": tue.isoformat(), "lesson_id": lesson["id"]}
    r = await client.post("/api/admin/changes", json={**base, "action": "cancel", "note": "Преподаватель болеет"},
                          headers=boss.h)
    assert r.status_code == 200, r.text
    day = await _day(client, stud, tue)
    assert day["lessons"] == [] and day["cancelled"][0]["subject"] == "Химия"
    note = (await _inbox(client, stud))[0]
    assert note["title"] == "❌ Пара отменена · ГР 2" and "Преподаватель болеет" in note["body"]

    # Перенос заменяет отмену: во вторник пары нет, в среду — разовая
    wed = tue + timedelta(days=1)
    r = await client.post("/api/admin/changes", json={
        **base, "action": "move", "to_date": wed.isoformat(), "start": "12:40", "end": "14:10", "room": "305",
    }, headers=boss.h)
    assert r.status_code == 200, r.text
    tue_day, wed_day = await _day(client, stud, tue), await _day(client, stud, wed)
    assert tue_day["lessons"] == [] and tue_day["cancelled"][0]["moved"]
    assert "Перенесена на ср" in tue_day["cancelled"][0]["note"]
    extra = wed_day["lessons"][0]
    assert (extra["subject"], extra["start"], extra["room"], extra["status"], extra["moved"]) == (
        "Химия", "12:40", "305", "extra", True)
    note = (await _inbox(client, stud))[0]
    assert note["title"] == "↪️ Перенос пары · ГР 2" and "12:40–14:10, ауд. 305" in note["body"]
    listed = (await client.get("/api/admin/changes", params={"group": "ГР 2"}, headers=boss.h)).json()
    assert len(listed) == 2  # отмена + разовая пара

    # Отменить перенос можно с любой стороны — уходят обе записи
    await client.delete(f"/api/admin/changes/{listed[1]['id']}", headers=boss.h)
    assert (await client.get("/api/admin/changes", params={"group": "ГР 2"}, headers=boss.h)).json() == []
    assert [l["subject"] for l in (await _day(client, stud, tue))["lessons"]] == ["Химия"]
    assert (await _inbox(client, stud))[0]["title"] == "↩️ Перенос отменён · ГР 2"


async def test_extra_lesson_and_checks(client, seeded):
    boss, stud, tue, lesson = await _setup(client)
    thu = tue + timedelta(days=2)
    r = await client.post("/api/admin/changes", json={
        "group": "ГР 2", "date": thu.isoformat(), "action": "add", "subject": "Консультация",
        "start": "15:00", "end": "16:30", "room": "101",
    }, headers=boss.h)
    assert r.status_code == 200, r.text
    assert [l["subject"] for l in (await _day(client, stud, thu))["lessons"]] == ["Консультация"]
    assert (await _inbox(client, stud))[0]["title"] == "➕ Дополнительная пара · ГР 2"

    async def post(**kw):
        return await client.post("/api/admin/changes", json={"group": "ГР 2", "date": tue.isoformat(), **kw},
                                 headers=boss.h)

    assert (await post(action="change", lesson_id=lesson["id"], room="401")).status_code == 400  # то же самое
    assert (await post(action="add", subject="Без времени")).status_code == 400
    assert (await post(action="add", subject="Наоборот", start="12:00", end="11:00")).status_code == 400
    r = await client.post("/api/admin/changes", json={"group": "ГР 2", "date": thu.isoformat(), "action": "cancel",
                                                      "lesson_id": lesson["id"]}, headers=boss.h)
    assert r.status_code == 400 and "этой пары нет" in r.text
    far = (changes.today() + timedelta(days=400)).isoformat()
    assert (await client.post("/api/admin/changes", json={"group": "ГР 2", "date": far, "action": "add",
                                                          "subject": "x", "start": "10:00", "end": "11:00"},
                              headers=boss.h)).status_code == 400
    # Студент менять не может
    assert (await post(action="cancel", lesson_id=lesson["id"])).status_code == 200
    r = await client.post("/api/admin/changes", json={"group": "ГР 2", "date": tue.isoformat(), "action": "cancel",
                                                      "lesson_id": lesson["id"]}, headers=stud.h)
    assert r.status_code == 403


async def test_teacher_sees_cancel_and_substitution(seeded):
    async with seeded.session() as s:
        users = []
        for login, fio in (("holm", "Холмов Холм Холмович"), ("petrov", "Петров Пётр Петрович")):
            u = User(login=login, password_hash="x", full_name=fio, code=login.upper()[:6].ljust(6, "X"))
            s.add(u)
            await s.flush()
            s.add(Teacher(user_id=u.id, full_name=fio))
            users.append(u)
        await s.commit()
        holm, petrov = [await s.get(Teacher, u.id) for u in users]
        # Анатомия у Холмова — по нечётным четвергам; ближайший такой
        d = next_weekday(3)
        if schedule.week_parity(d, date(2026, 9, 1)) != "odd":
            d += timedelta(days=7)
        anatomy = (await s.scalars(select(Lesson).where(Lesson.subject == "Анатомия"))).all()
        assert len((await attendance.teacher_days(s, holm, [d]))[d].lessons) == 2
        s.add(LessonChange(group_name="ГР 1", date=d, lesson_id=anatomy[0].id, action=CHANGE_CANCEL))
        s.add(LessonChange(group_name="ГР 1", date=d, lesson_id=anatomy[1].id, action=CHANGE_EDIT,
                           teacher="Петров П. П."))
        await s.commit()
        mine = (await attendance.teacher_days(s, holm, [d]))[d]
        assert mine.lessons == [] and len(mine.cancelled) == 1
        sub = (await attendance.teacher_days(s, petrov, [d]))[d].lessons
        assert [(l.subject, l.status) for l in sub] == [("Анатомия", "changed")]


# --- уведомления о смене аудитории --------------------------------------------------

async def test_room_change_of_recurring_lesson(client, seeded):
    boss, stud, tue, lesson = await _setup(client)
    lessons = (await client.get("/api/admin/lessons", params={"group": "ГР 2"}, headers=boss.h)).json()
    body = {k: lessons[0][k] for k in ("group", "weekday", "week", "weeks", "pair_num", "start", "end", "subject",
                                      "kind", "teacher", "half")}
    r = await client.put(f"/api/admin/lessons/{lessons[0]['id']}", json={**body, "room": "410"}, headers=boss.h)
    assert r.status_code == 200, r.text
    note = (await _inbox(client, stud))[0]
    assert note["title"] == "🚪 Новая аудитория · ГР 2"
    assert "Химия (Вт, 09:00): теперь ауд. 410 вместо ауд. 401 — каждую неделю." in note["body"]


async def test_reimport_reports_rooms_and_keeps_changes(client, seeded, schedule_xlsx):
    boss, stud, tue, lesson = await _setup(client)
    await client.post("/api/admin/changes", json={"group": "ГР 2", "date": tue.isoformat(), "action": "cancel",
                                                  "lesson_id": lesson["id"]}, headers=boss.h)
    before = len(await _inbox(client, stud))

    async def upload(data: bytes):
        r = await client.post("/api/admin/import", files={"file": ("s.xlsx", data)}, headers=boss.h)
        assert r.status_code == 200, r.text

    # Тот же файл — ничего не поменялось, уведомления нет
    await upload(schedule_xlsx)
    assert len(await _inbox(client, stud)) == before
    # Другая аудитория у химии
    await upload(make_xlsx([SCHEDULE_HEADER, ["ГР 2", "Вторник", "", 1, "9:00", "10:30", "Химия", "Лабораторная",
                                              402, ""]]))
    note = (await _inbox(client, stud))[0]
    assert note["title"] == "🚪 Новые аудитории · ГР 2"
    assert "Вт 09:00 Химия: ауд. 401 → ауд. 402" in note["body"]
    # Разовая отмена пережила повторную загрузку
    day = await _day(client, stud, tue)
    assert day["lessons"] == [] and day["cancelled"][0]["room"] == "402"


# --- фото профиля -------------------------------------------------------------------

def _jpeg_with_exif() -> bytes:
    def seg(marker: int, payload: bytes) -> bytes:
        return bytes((0xFF, marker)) + (len(payload) + 2).to_bytes(2, "big") + payload
    return (b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
            + seg(0xE1, b"Exif\x00\x00GPS 55.75N 37.61E") + seg(0xFE, b"comment")
            + seg(0xDB, b"\x00" + bytes(64)) + seg(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\x34\x56" + b"\xff\xd9")


async def test_profile_photo(client, seeded):
    boss, stud, _, _ = await _setup(client)
    data_url = "data:image/jpeg;base64," + base64.b64encode(_jpeg_with_exif()).decode()
    r = await client.post("/api/me/photo", json={"image": data_url}, headers=stud.h)
    assert r.status_code == 200, r.text
    url = r.json()["photo"]
    assert (await client.get("/api/me", headers=stud.h)).json()["photo"] == url
    pic = await client.get(url)  # без входа: ключ случайный
    assert pic.status_code == 200 and pic.headers["content-type"] == "image/jpeg"
    assert "immutable" in pic.headers["cache-control"]
    assert b"JFIF" in pic.content and b"GPS" not in pic.content and b"comment" not in pic.content
    assert pic.content.endswith(b"\x12\x34\x56\xff\xd9")
    # Видно старосте и админу в списке группы
    listed = (await client.get("/api/admin/students", headers=boss.h)).json()
    assert next(x for x in listed if x["user_id"] == stud.id)["photo"] == url

    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(20)).decode()
    assert (await client.post("/api/me/photo", json={"image": png}, headers=stud.h)).status_code == 422
    assert (await client.post("/api/me/photo", json={"image": "data:image/jpeg;base64,!!!"},
                              headers=stud.h)).status_code == 422

    # Админ убирает неподходящее фото — студенту приходит уведомление, старый адрес больше не работает
    assert (await client.delete(f"/api/admin/users/{stud.id}/photo", headers=boss.h)).status_code == 200
    assert (await _inbox(client, stud))[0]["title"] == "Фото профиля убрано"
    assert (await client.get(url)).status_code == 404
    assert (await client.get("/api/me", headers=stud.h)).json()["photo"] is None
    assert (await client.delete(f"/api/admin/users/{boss.id}/photo", headers=stud.h)).status_code == 403


async def test_edit_mode_shows_both_halves(client, seeded):
    """Староста выбрал свою половину, но править должен пары обеих."""
    boss = await owner(client)
    code = (await client.get("/api/admin/group-code", params={"group": "ГР 1"}, headers=boss.h)).json()["code"]
    star = await register(client, "testov", "Тестов Тест Тестович", code=code)
    await client.put(f"/api/admin/users/{star.id}/role", json={"role": "starosta"}, headers=boss.h)
    await client.put("/api/me/settings", json={"half": 1}, headers=star.h)
    thu = next_weekday(3)
    if schedule.week_parity(thu, date(2026, 9, 1)) != "odd":
        thu += timedelta(days=7)

    async def anatomy(**params):
        week = (await client.get("/api/schedule", params={"date": thu.isoformat(), **params}, headers=star.h)).json()
        day = next(x for x in week["days"] if x["date"] == thu.isoformat())
        return sorted(l["half"] for l in day["lessons"] if l["subject"] == "Анатомия")

    assert await anatomy() == [1]
    assert await anatomy(edit=1) == [1, 2]
    # Обычному студенту edit ничего не даёт
    stud = await register(client, "probnaya", "Пробная Анна Сергеевна", code=code)
    await client.put("/api/me/settings", json={"half": 2}, headers=stud.h)
    week = (await client.get("/api/schedule", params={"date": thu.isoformat(), "edit": 1}, headers=stud.h)).json()
    day = next(x for x in week["days"] if x["date"] == thu.isoformat())
    assert [l["half"] for l in day["lessons"] if l["subject"] == "Анатомия"] == [2]
