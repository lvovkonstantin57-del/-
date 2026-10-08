"""Отметка на паре по коду: преподаватели, коды, журнал."""

import io
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from sqlalchemy import select

from app import attendance, users
from app.api.routes import create_app
from app.config import config
from app.importer import import_schedule, import_students
from app.models import AttendanceSession, Lesson, Student, Teacher, User
from tests.conftest import PASSWORD, owner, register


@pytest.fixture
async def c(database, schedule_xlsx, students_xlsx):
    async with database.session() as s:
        await import_schedule(s, schedule_xlsx)
        await import_students(s, students_xlsx)
        # Сидоров ведёт зоологию и у ГР 2 — там он записан иначе и вдвоём с Петровым
        s.add(Lesson(group_name="ГР 2", weekday=1, week="every", pair_num=2, start_time="10:40", end_time="12:10",
                     subject="Зоология", kind="лекция", room="304", teacher="доц. Сидоров С.С., Петров П. П."))
        await s.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as client:
        yield client


async def _account(database, login: str, fio: str) -> tuple[int, dict]:
    async with database.session() as s:
        user = await users.create_user(s, login, PASSWORD, fio)
        token = await users.issue_token(s, user)
        return user.id, {"Authorization": f"Bearer {token}"}


async def _student(database, login: str, fio: str, half: int | None = None) -> dict:
    """Студент из загруженного списка, уже в своей группе."""
    uid, h = await _account(database, login, fio)
    async with database.session() as s:
        user = await s.get(User, uid)
        user.student_id = await s.scalar(select(Student.id).where(Student.full_name == fio))
        user.half = half
        await s.commit()
    return h


async def _teacher(database, login: str, fio: str = "Сидоров Сергей Сергеевич", schedule_name: str | None = None):
    uid, h = await _account(database, login, fio)
    async with database.session() as s:
        s.add(Teacher(user_id=uid, full_name=fio, schedule_name=schedule_name))
        await s.commit()
    return uid, h


async def _expire_code(database, session_id: int) -> None:
    async with database.session() as s:
        x = await s.get(AttendanceSession, session_id)
        x.code_expires_at = attendance.utcnow() - timedelta(seconds=1)
        await s.commit()


async def _sid(database, fio: str) -> int:
    async with database.session() as s:
        return await s.scalar(select(Student.id).where(Student.full_name == fio))


async def test_teacher_opens_code_and_students_check_in(c, database):
    _, t = await _teacher(database, "sidorov")
    s1 = await _student(database, "stud1", "Тестов Тест Тестович")
    s2 = await _student(database, "stud2", "Пробная Анна Сергеевна")
    s3 = await _student(database, "stud3", "Ёлкин Пётр Иванович")  # ГР 2

    me = (await c.get("/api/me", headers=t)).json()
    # Пары — из расписания, где стоит его фамилия: зоология у ГР 1 и ГР 2
    assert me["teacher"]["full_name"] == "Сидоров Сергей Сергеевич" and me["teacher"]["schedule_name"] == "Сидоров С. С."
    assert me["teacher"]["subjects"] == [{"subject": "Зоология", "groups": ["ГР 1", "ГР 2"]}]
    assert me["teacher"]["groups"] == ["ГР 1", "ГР 2"] and me["teacher"]["lessons"] == 2
    # Преподаватель смотрит расписание любой группы
    assert (await c.get("/api/schedule", params={"group": "ГР 2"}, headers=t)).status_code == 200

    r = await c.post("/api/teacher/sessions", headers=t, json={"groups": ["ГР 1"], "subject": "Зоология"})
    assert r.status_code == 200, r.text
    x = r.json()
    assert len(x["code"]) == 4 and x["code"].isdigit() and 55 < x["expires_in"] <= 60
    assert [p["full_name"] for p in x["roster"]] == ["Пробная Анна Сергеевна", "Тестов Тест Тестович"]
    assert x["present"] == 0 and x["total"] == 2 and all(p["in_app"] for p in x["roster"])

    # Студент ГР 1 отмечается; повторно — «уже отмечен»
    r = await c.post("/api/attendance/checkin", headers=s1, json={"code": x["code"]})
    assert r.status_code == 200 and r.json()["already"] is False and r.json()["subject"] == "Зоология"
    r = await c.post("/api/attendance/checkin", headers=s1, json={"code": f" {x['code']} "})
    assert r.json()["already"] is True
    # Студент другой группы — нет
    r = await c.post("/api/attendance/checkin", headers=s3, json={"code": x["code"]})
    assert r.status_code == 400 and "другой группы" in r.json()["detail"]
    # Не студент — нет
    assert (await c.post("/api/attendance/checkin", headers=t, json={"code": x["code"]})).status_code == 403

    detail = (await c.get(f"/api/teacher/sessions/{x['id']}", headers=t)).json()
    assert detail["present"] == 1 and [p["present"] for p in detail["roster"]] == [False, True]
    assert detail["roster"][1]["method"] == "code" and detail["roster"][1]["at"]

    # Студент видит отметку у себя
    mine = (await c.get("/api/attendance/me", headers=s1)).json()["sessions"]
    assert mine[0]["marked"] is True and mine[0]["code_active"] is True
    assert (await c.get("/api/attendance/me", headers=s2)).json()["sessions"][0]["marked"] is False

    # Код истёк — отметиться нельзя; новый код работает, отметки остаются
    await _expire_code(database, x["id"])
    r = await c.post("/api/attendance/checkin", headers=s2, json={"code": x["code"]})
    assert r.status_code == 400 and "не действует" in r.json()["detail"]
    fresh = (await c.post(f"/api/teacher/sessions/{x['id']}/code", headers=t)).json()
    assert fresh["code"] and fresh["present"] == 1
    assert (await c.post("/api/attendance/checkin", headers=s2, json={"code": fresh["code"]})).status_code == 200

    # Завершили — код больше не работает, пара в журнале
    closed = (await c.post(f"/api/teacher/sessions/{x['id']}/close", headers=t)).json()
    assert closed["open"] is False and closed["code"] is None and closed["present"] == 2
    home = (await c.get("/api/teacher", headers=t)).json()
    assert home["sessions_total"] == 1 and home["rate"] == 100
    assert home["subjects"] == [
        {"subject": "Зоология", "groups": ["ГР 1", "ГР 2"], "students": 3, "sessions": 1, "rate": 100}]
    assert home["groups"] == ["ГР 1", "ГР 2"] and home["schedule_name"] == "Сидоров С. С."
    assert home["recent"][0]["present"] == 2 and home["recent"][0]["total"] == 2


async def test_code_permissions_and_limits(c, database):
    _, t = await _teacher(database, "sidorov")
    _, t2 = await _teacher(database, "ivanov", "Иванов Иван Иванович")  # ботаника у ГР 1
    s1 = await _student(database, "stud1", "Тестов Тест Тестович")

    # Только подгруппы, где у него пары, и только преподавателю
    assert (await c.post("/api/teacher/sessions", headers=t2, json={"groups": ["ГР 2"]})).status_code == 403
    assert (await c.get("/api/teacher", headers=s1)).status_code == 403
    x = (await c.post("/api/teacher/sessions", headers=t, json={"groups": ["ГР 1"]})).json()
    assert x["subject"] == "Пара"
    # Чужую пару не видно
    assert (await c.get(f"/api/teacher/sessions/{x['id']}", headers=t2)).status_code == 404
    assert (await c.get("/api/teacher/attendance", params={"subject": "Ботаника"}, headers=t)).status_code == 403

    # Неверный формат не считается попыткой; пять неверных кодов — пауза
    assert (await c.post("/api/attendance/checkin", headers=s1, json={"code": "12a4"})).status_code == 422
    wrong = f"{(int(x['code']) + 1) % 10000:04d}"
    for _ in range(attendance.FAIL_LIMIT):
        assert (await c.post("/api/attendance/checkin", headers=s1, json={"code": wrong})).status_code == 400
    r = await c.post("/api/attendance/checkin", headers=s1, json={"code": x["code"]})
    assert r.status_code == 429 and "Слишком много" in r.json()["detail"]

    # Новая пара закрывает прошлую
    y = (await c.post("/api/teacher/sessions", headers=t, json={"groups": ["ГР 1"], "subject": "Ботаника"})).json()
    assert (await c.get(f"/api/teacher/sessions/{x['id']}", headers=t)).json()["open"] is False
    assert (await c.get("/api/teacher/today", headers=t)).json()["active"]["id"] == y["id"]

    # Удалить пару из журнала
    assert (await c.delete(f"/api/teacher/sessions/{x['id']}", headers=t)).status_code == 200
    assert (await c.get(f"/api/teacher/sessions/{x['id']}", headers=t)).status_code == 404


async def test_half_and_manual_marks(c, database):
    _, t = await _teacher(database, "sidorov")
    await _student(database, "stud1", "Тестов Тест Тестович", half=1)
    s2 = await _student(database, "stud2", "Пробная Анна Сергеевна", half=2)
    x = (await c.post("/api/teacher/sessions", headers=t, json={"groups": ["ГР 1"], "half": 1})).json()
    # На паре 1-й половины в списке нет 2-й половины
    assert [p["full_name"] for p in x["roster"]] == ["Тестов Тест Тестович"]
    r = await c.post("/api/attendance/checkin", headers=s2, json={"code": x["code"]})
    assert r.status_code == 400 and "1-й половины" in r.json()["detail"]

    # Вручную: отметить и снять
    sid = x["roster"][0]["id"]
    r = await c.put(f"/api/teacher/sessions/{x['id']}/marks/{sid}", headers=t, json={"present": True})
    assert r.json()["present"] == 1 and r.json()["roster"][0]["method"] == "manual"
    r = await c.put(f"/api/teacher/sessions/{x['id']}/marks/{sid}", headers=t, json={"present": False})
    assert r.json()["present"] == 0
    other = await _sid(database, "Ёлкин Пётр Иванович")
    r = await c.put(f"/api/teacher/sessions/{x['id']}/marks/{other}", headers=t, json={"present": True})
    assert r.status_code == 404


async def test_lessons_by_teacher_name_today_and_export(c, database, monkeypatch):
    # В расписании такого нет — пар нет, код не создать
    _, t = await _teacher(database, "novikov", "Новиков Николай Николаевич")
    assert (await c.get("/api/me", headers=t)).json()["teacher"]["subjects"] == []
    r = await c.post("/api/teacher/sessions", headers=t, json={"groups": ["ГР 1"]})
    assert r.status_code == 400 and "В расписании нет ваших пар" in r.json()["detail"]

    # Найти себя в расписании вручную
    people = (await c.get("/api/teacher/people", headers=t)).json()
    assert people["current"] is None
    sid = next(p for p in people["people"] if p["name"] == "Сидоров С. С.")
    assert sid["lessons"] == 2 and sid["match"] is False and "доц. Сидоров С.С." in sid["names"]
    assert (await c.put("/api/teacher/schedule-name", headers=t, json={"name": "Нет Такого Т. Т."})).status_code == 400
    r = await c.put("/api/teacher/schedule-name", headers=t, json={"name": "Сидоров С. С."})
    assert r.json()["subjects"] == [{"subject": "Зоология", "groups": ["ГР 1", "ГР 2"]}]
    r = await c.put("/api/teacher/schedule-name", headers=t, json={"name": None})
    assert r.json()["subjects"] == [] and r.json()["schedule_name"] is None
    await c.put("/api/teacher/schedule-name", headers=t, json={"name": "Сидоров С. С."})

    # Пары на сегодня — только его: в понедельник у ГР 1 ещё физкультура, но её ведёт Петров
    monday = datetime(2026, 10, 5, 10, 50, tzinfo=config.tz)
    monkeypatch.setattr(attendance, "local_now", lambda: monday)
    today = (await c.get("/api/teacher/today", headers=t)).json()
    assert [(l["start"], l["subject"], l["groups"]) for l in today["lessons"]] == [("10:40", "Зоология", ["ГР 1"])]
    assert today["groups"] == ["ГР 1", "ГР 2"] and today["active"] is None

    # Вкладка «Расписание» у преподавателя — неделя только его пар по всем подгруппам
    week = (await c.get("/api/schedule", params={"date": "2026-10-05"}, headers=t)).json()
    assert week["mine"] is True and week["found"] is True and week["group"] is None
    assert [[(l["start"], l["subject"], l["groups"]) for l in d["lessons"]] for d in week["days"][:3]] == [
        [("10:40", "Зоология", ["ГР 1"])], [("10:40", "Зоология", ["ГР 2"])], []]
    assert week["days"][1]["lessons"][0]["teacher"] == "доц. Сидоров С.С., Петров П. П."
    # Нечётная неделя: у ГР 1 в понедельник ботаника Иванова — её нет
    odd = (await c.get("/api/schedule", params={"date": "2026-10-12", "mine": "1"}, headers=t)).json()
    assert odd["days"][0]["lessons"] == [] and len(odd["days"][1]["lessons"]) == 1
    # Студенту чужое «Мои пары» не положено
    s4 = await _student(database, "stud4", "Ёлкин Пётр Иванович")
    assert (await c.get("/api/schedule", params={"mine": "1"}, headers=s4)).status_code == 403

    # По предмету без пар с отметкой — подгруппы из расписания
    att = (await c.get("/api/teacher/attendance", params={"subject": "Зоология"}, headers=t)).json()
    assert att["sessions"] == 0 and att["rate"] is None and {s["group"] for s in att["students"]} == {"ГР 1", "ГР 2"}

    s1 = await _student(database, "stud1", "Тестов Тест Тестович")
    x = (await c.post("/api/teacher/sessions", headers=t, json={
        "groups": ["ГР 1"], "subject": "Зоология", "start": "10:40", "end": "12:10"})).json()
    assert x["date"] == "2026-10-05" and x["start"] == "10:40"
    await c.post("/api/attendance/checkin", headers=s1, json={"code": x["code"]})

    att = (await c.get("/api/teacher/attendance", params={"subject": "Зоология"}, headers=t)).json()
    assert att["sessions"] == 1 and att["rate"] == 50
    assert {s["full_name"]: (s["attended"], s["total"], s["in_app"]) for s in att["students"]} == {
        "Тестов Тест Тестович": (1, 1, True), "Пробная Анна Сергеевна": (0, 1, False)}

    # Журнал в Excel — файлом
    r = await c.get("/api/teacher/export", headers=t)
    assert r.status_code == 200 and "filename*=UTF-8''" in r.headers["content-disposition"]
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    ws = load_workbook(io.BytesIO(r.content))["Зоология"]
    rows = list(ws.values)
    assert rows[0][:2] == ("Группа", "ФИО") and "05.10 10:40" in rows[0][2] and rows[0][-3:] == ("Был", "Из", "%")
    assert rows[1] == ("ГР 1", "Пробная Анна Сергеевна", "—", 0, 1, 0)
    assert rows[2] == ("ГР 1", "Тестов Тест Тестович", "+", 1, 1, 100)


async def test_invites_and_teacher_list(c, database):
    boss = await owner(c)
    adm = await register(c, "adm", "Админ Обычный")
    await c.put(f"/api/admin/users/{adm.id}/role", json={"role": "admin"}, headers=boss.h)
    # Код выдаёт только главный админ
    assert (await c.post("/api/admin/teachers/invites", headers=adm.h)).status_code == 403
    inv = (await c.post("/api/admin/teachers/invites", headers=boss.h)).json()
    assert len(inv["code"]) == 8 and inv["pretty"][4] == "-"
    assert (await c.get("/api/admin/teachers", headers=boss.h)).json()["invites"][0]["code"] == inv["code"]
    # Обычный админ видит преподавателей, но не коды
    assert (await c.get("/api/admin/teachers", headers=adm.h)).json() == {"teachers": [], "invites": []}

    # Преподаватель регистрируется в приложении и вводит код (можно маленькими буквами)
    teacher = await register(c, "ivanov", "Иванов Иван Иванович", code=inv["pretty"].lower())
    assert teacher.me["teacher"]["schedule_name"] == "Иванов И. И."
    titles = [n["title"] for n in (await c.get("/api/notifications", headers=boss.h)).json()["items"]]
    assert "🎓 Новый преподаватель" in titles
    # Код одноразовый
    late = await register(c, "petrov", "Петров Пётр Петрович")
    assert (await c.post("/api/me/code", json={"code": inv["code"]}, headers=late.h)).status_code == 404

    x = (await c.post("/api/teacher/sessions", headers=teacher.h,
                      json={"groups": ["ГР 1"], "subject": "Ботаника"})).json()
    listed = (await c.get("/api/admin/teachers", headers=adm.h)).json()["teachers"]
    assert listed == [{"id": teacher.id, "full_name": "Иванов Иван Иванович", "schedule_name": "Иванов И. И.",
                       "subjects": ["Ботаника"], "login": "ivanov"}]

    # Убрать преподавателя: доступ пропадает, пара остаётся в базе с его именем
    assert (await c.delete(f"/api/admin/teachers/{teacher.id}", headers=adm.h)).status_code == 403
    assert (await c.delete(f"/api/admin/teachers/{teacher.id}", headers=boss.h)).status_code == 200
    assert (await c.get("/api/teacher", headers=teacher.h)).status_code == 403
    feed = (await c.get("/api/notifications", headers=teacher.h)).json()["items"]
    assert feed[0]["title"] == "Доступ преподавателя отключён"
    async with database.session() as s:
        kept = await s.get(AttendanceSession, x["id"])
        assert kept.teacher_id is None and kept.teacher_name == "Иванов Иван Иванович"


async def test_teacher_code_twice_does_not_burn_invite(c, database):
    boss = await owner(c)
    first = (await c.post("/api/admin/teachers/invites", headers=boss.h)).json()["code"]
    second = (await c.post("/api/admin/teachers/invites", headers=boss.h)).json()["code"]
    teacher = await register(c, "sid", "Сидоров Сергей Сергеевич", code=first)
    r = await c.post("/api/me/code", json={"code": second}, headers=teacher.h)
    assert r.json()["kind"] == "teacher" and "не потрачен" in r.json()["message"]
    assert len((await c.get("/api/admin/teachers", headers=boss.h)).json()["invites"]) == 1


def test_clean_invite():
    assert attendance.clean_invite("abcd-efgh") == "ABCDEFGH"
    assert attendance.clean_invite("АВСЕ КМНР") == "ABCEKMHP"  # русские буквы, похожие на латинские
    assert attendance.clean_invite("Иванов Иван") is None
    assert attendance.clean_invite("ABCD-EFG0") is None  # 0 в кодах не бывает
    assert attendance.clean_code(" 0042 ") == "0042" and attendance.clean_code("42") is None


def test_teacher_names_in_schedule():
    P = attendance.Person
    key = attendance.person_key
    for text in ("Сидоров С. С.", "Сидоров С.С.", "доц. Сидоров С.С.", "Сидоров Сергей Сергеевич", "С. С. Сидоров"):
        assert key(text) == P("сидоров", ("с", "с")), text
    assert key("Ёлкин Пётр") == P("елкин", ("п",)) and key("Петров-Водкин К.") == P("петров-водкин", ("к",))
    assert key("") is None and key("—") is None
    assert [n for n, _ in attendance.people_in("Иванов И. И., Петров П. П.")] == ["Иванов И. И.", "Петров П. П."]
    # Инициалы не противоречат — тот же человек; другие инициалы или фамилия — нет
    me = key("Сидоров Сергей Сергеевич")
    assert attendance.same_person(me, key("Сидоров С.")) and attendance.same_person(me, key("Сидоров"))
    assert not attendance.same_person(me, key("Сидоров П. С.")) and not attendance.same_person(me, key("Сидорова С. С."))


async def test_qr_checkin_changes_every_few_seconds(c, database, monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(attendance, "clock", lambda: now[0])
    tid, t = await _teacher(database, "sidorov")
    s1 = await _student(database, "stud1", "Тестов Тест Тестович")
    s2 = await _student(database, "stud2", "Пробная Анна Сергеевна")
    s3 = await _student(database, "stud3", "Ёлкин Пётр Иванович")  # ГР 2
    x = (await c.post("/api/teacher/sessions", headers=t, json={"groups": ["ГР 1"], "subject": "Зоология"})).json()

    q = (await c.post(f"/api/teacher/sessions/{x['id']}/qr", headers=t)).json()
    assert q["step"] == attendance.QR_STEP and 0 < q["next_in"] <= attendance.QR_STEP
    # Пока на экране QR, 4-значный код не работает, но отметка идёт
    assert (await c.post("/api/attendance/checkin", headers=s2, json={"code": x["code"]})).status_code == 400
    assert (await c.get("/api/attendance/me", headers=s1)).json()["sessions"][0]["code_active"] is True

    r = await c.post("/api/attendance/checkin", headers=s1, json={"qr": q["token"]})
    assert r.status_code == 200 and r.json()["already"] is False
    detail = (await c.get(f"/api/teacher/sessions/{x['id']}", headers=t)).json()
    assert next(p for p in detail["roster"] if p["present"])["method"] == "qr"
    # Чужая группа и подделанная подпись — нет
    r = await c.post("/api/attendance/checkin", headers=s3, json={"qr": q["token"]})
    assert r.status_code == 400 and "другой группы" in r.json()["detail"]
    fake = q["token"][:-1] + ("0" if q["token"][-1] != "0" else "1")
    assert "не подходит" in (await c.post("/api/attendance/checkin", headers=s2, json={"qr": fake})).json()["detail"]
    assert (await c.post("/api/attendance/checkin", headers=s2, json={"qr": "что-то другое"})).status_code == 422

    # Через шаг QR новый, а прошлый ещё действует; через два шага — устарел
    now[0] += attendance.QR_STEP
    q2 = (await c.post(f"/api/teacher/sessions/{x['id']}/qr", headers=t)).json()
    assert q2["token"] != q["token"]
    now[0] += attendance.QR_STEP
    r = await c.post("/api/attendance/checkin", headers=s2, json={"qr": q["token"]})
    assert r.status_code == 400 and "устарел" in r.json()["detail"]
    assert (await c.post("/api/attendance/checkin", headers=s2, json={"qr": q2["token"]})).status_code == 200

    # Завершили — QR не работает и не выдаётся; чужой преподаватель QR не получит
    await c.post(f"/api/teacher/sessions/{x['id']}/close", headers=t)
    q3_status = (await c.post(f"/api/teacher/sessions/{x['id']}/qr", headers=t)).status_code
    assert q3_status == 400
    _, other = await _teacher(database, "petrov", "Петров Пётр Петрович")
    assert (await c.post(f"/api/teacher/sessions/{x['id']}/qr", headers=other)).status_code == 404
