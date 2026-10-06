import sqlite3

from sqlalchemy import select

from app.models import Student, User
from tests.conftest import PASSWORD, owner, refresh, register


# --- аккаунт -------------------------------------------------------------------

async def test_register_login_logout(client, database):
    acc = await register(client, "Ivan.Petrov", "  Петров   Иван Иванович ")
    me = acc.me
    assert me["login"] == "ivan.petrov" and me["full_name"] == "Петров Иван Иванович"
    assert me["role"] == "user" and me["student"] is None and me["unread"] == 0
    # Личный код: 6 символов без похожих букв, показываем как ABC-234
    assert len(me["code"]) == 7 and me["code"][3] == "-"

    r = await client.post("/api/auth/register", json={"login": "ivan.petrov", "full_name": "Другой Человек",
                                                       "password": PASSWORD})
    assert r.status_code == 409
    r = await client.post("/api/auth/register", json={"login": "x y", "full_name": "Петров Иван", "password": PASSWORD})
    assert r.status_code == 422
    r = await client.post("/api/auth/register", json={"login": "petrov2", "full_name": "Петров", "password": PASSWORD})
    assert r.status_code == 422 and "фамилию и имя" in r.json()["detail"]
    r = await client.post("/api/auth/register", json={"login": "petrov2", "full_name": "Петров Иван", "password": "123"})
    assert r.status_code == 422

    r = await client.post("/api/auth/login", json={"login": "IVAN.PETROV", "password": PASSWORD})
    assert r.status_code == 200
    second = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await client.get("/api/me", headers=second)).json()["id"] == acc.id
    assert (await client.post("/api/auth/login", json={"login": "ivan.petrov", "password": "wrong"})).status_code == 401

    await client.post("/api/auth/logout", headers=second)
    assert (await client.get("/api/me", headers=second)).status_code == 401
    assert (await client.get("/api/me", headers=acc.h)).status_code == 200
    assert (await client.get("/api/me")).status_code == 401


async def test_login_rate_limit(client, database):
    await register(client, "victim", "Жертва Перебора")
    for _ in range(10):
        await client.post("/api/auth/login", json={"login": "victim", "password": "nope"})
    r = await client.post("/api/auth/login", json={"login": "victim", "password": PASSWORD})
    assert r.status_code == 429


async def test_change_password_logs_out_other_devices(client, database):
    acc = await register(client, "anna", "Пробная Анна Сергеевна")
    other = (await client.post("/api/auth/login", json={"login": "anna", "password": PASSWORD})).json()["token"]
    r = await client.put("/api/me/password", headers=acc.h, json={"old_password": "bad", "new_password": "newpass1"})
    assert r.status_code == 403
    r = await client.put("/api/me/password", headers=acc.h, json={"old_password": PASSWORD, "new_password": "newpass1"})
    assert r.status_code == 200
    assert (await client.get("/api/me", headers=acc.h)).status_code == 200
    assert (await client.get("/api/me", headers={"Authorization": f"Bearer {other}"})).status_code == 401
    assert (await client.post("/api/auth/login", json={"login": "anna", "password": "newpass1"})).status_code == 200


async def test_delete_account_keeps_roster(client, seeded):
    boss = await owner(client)
    code = (await client.get("/api/admin/group-code", params={"group": "ГР 1"}, headers=boss.h)).json()["code"]
    acc = await register(client, "testov", "Тестов Тест Тестович", code=code)
    sid = acc.me["student"]["id"]
    assert (await client.post("/api/me/delete", headers=acc.h, json={"password": "bad"})).status_code == 403
    assert (await client.post("/api/me/delete", headers=acc.h, json={"password": PASSWORD})).status_code == 200
    assert (await client.get("/api/me", headers=acc.h)).status_code == 401
    async with seeded.session() as s:
        assert await s.get(Student, sid) is not None


# --- коды и группы -----------------------------------------------------------------

async def test_owner_code(client, database):
    acc = await register(client, "boss", "Главный Админ")
    r = await client.post("/api/me/code", json={"code": "wrong-code"}, headers=acc.h)
    assert r.status_code == 404
    r = await client.post("/api/me/code", json={"code": "секрет"}, headers=acc.h)  # не ASCII — не падаем
    assert r.status_code == 404
    r = await client.post("/api/me/code", json={"code": "---"}, headers=acc.h)
    assert r.status_code == 404
    # Телефон сам ставит заглавные, дефис можно не вводить
    r = await client.post("/api/me/code", json={"code": " SECRET CODE "}, headers=acc.h)
    assert r.json()["kind"] == "owner"
    assert (await refresh(client, acc)).me["role"] == "owner"


async def test_join_by_group_code(client, seeded):
    boss = await owner(client)
    r = await client.get("/api/admin/group-code", params={"group": "ГР 1"}, headers=boss.h)
    code = r.json()["code"]
    assert len(code) == 7
    # Тот же код при повторном запросе
    assert (await client.get("/api/admin/group-code", params={"group": "ГР 1"}, headers=boss.h)).json()["code"] == code

    # ФИО совпало со строкой из загруженного списка — аккаунт привязался к ней (вместе с почтой и журналом)
    async with seeded.session() as s:
        listed = await s.scalar(select(Student).where(Student.full_name == "Тестов Тест Тестович"))
    r = await client.post("/api/auth/register", json={
        "login": "testov", "full_name": "тестов тест тестович", "password": PASSWORD, "code": code.lower()})
    assert r.json()["code_result"]["kind"] == "group"
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = (await client.get("/api/me", headers=h)).json()
    assert me["student"]["id"] == listed.id and me["student"]["group"] == "ГР 1"
    assert len(me["half_lessons"]) == 2

    # ФИО в списке нет — студент добавился в список группы
    newbie = await register(client, "new", "Новенький Нов Новович", code=code)
    assert newbie.me["student"]["group"] == "ГР 1" and newbie.me["student"]["id"] != listed.id

    # Повторно и в другую группу — нельзя
    r = await client.post("/api/me/code", json={"code": code}, headers=newbie.h)
    assert "уже в группе" in r.json()["message"]
    code2 = (await client.get("/api/admin/group-code", params={"group": "ГР 2"}, headers=boss.h)).json()["code"]
    r = await client.post("/api/me/code", json={"code": code2}, headers=newbie.h)
    assert r.status_code == 409

    # Главному админу пришли уведомления о новых студентах
    feed = (await client.get("/api/notifications", headers=boss.h)).json()
    assert feed["unread"] == 2 and all("Новый студент" in n["title"] for n in feed["items"])

    # Новый код группы — старый перестаёт работать
    fresh = (await client.post("/api/admin/group-code", json={"group": "ГР 1"}, headers=boss.h)).json()["code"]
    assert fresh != code
    late = await register(client, "late", "Опоздавший Студент")
    assert (await client.post("/api/me/code", json={"code": code}, headers=late.h)).status_code == 404
    assert (await client.post("/api/me/code", json={"code": fresh}, headers=late.h)).json()["group"] == "ГР 1"


async def test_registration_with_bad_code_still_creates_account(client, database):
    r = await client.post("/api/auth/register", json={
        "login": "oops", "full_name": "Ошибся Кодом", "password": PASSWORD, "code": "ZZZ-ZZZ"})
    assert r.status_code == 200 and "token" in r.json() and "не подошёл" in r.json()["code_error"]


async def test_own_personal_code_is_explained(client, database):
    acc = await register(client, "myself", "Сам Себе")
    r = await client.post("/api/me/code", json={"code": acc.code}, headers=acc.h)
    assert r.status_code == 400 and "твой личный код" in r.json()["detail"]


async def test_code_attempts_are_limited(client, database):
    acc = await register(client, "guess", "Угадай Код")
    for _ in range(10):
        await client.post("/api/me/code", json={"code": "AAA-AAA"}, headers=acc.h)
    assert (await client.post("/api/me/code", json={"code": "AAA-AAA"}, headers=acc.h)).status_code == 429


async def test_admin_creates_group_and_starosta_adds_by_code(client, database):
    boss = await owner(client)
    r = await client.post("/api/admin/groups", json={"name": " БИА  1 "}, headers=boss.h)
    assert r.json()["group"] == "БИА 1"
    assert (await client.post("/api/admin/groups", json={"name": "биа 1"}, headers=boss.h)).status_code == 409
    assert (await client.get("/api/admin/groups", headers=boss.h)).json() == ["БИА 1"]

    # Админ добавляет будущего старосту по его коду и назначает роль
    star = await register(client, "star", "Старостина Мария Петровна")
    r = await client.get("/api/admin/lookup", params={"code": star.code}, headers=boss.h)
    assert r.json()["full_name"] == "Старостина Мария Петровна" and r.json()["group"] is None
    r = await client.post("/api/admin/members", json={"code": star.code, "group": "БИА 1"}, headers=boss.h)
    assert r.json()["status"] == "added"
    r = await client.put(f"/api/admin/users/{star.id}/role", json={"role": "starosta"}, headers=boss.h)
    assert r.json()["role"] == "starosta"
    star = await refresh(client, star)
    assert star.me["role"] == "starosta"
    feed = (await client.get("/api/notifications", headers=star.h)).json()["items"]
    assert [n["kind"] for n in feed] == ["role", "group"]

    # Студент регистрируется без группы, показывает код старосте — тот добавляет
    stud = await register(client, "stud", "Студентов Студент")
    assert (await client.get("/api/schedule", headers=stud.h)).status_code == 403
    r = await client.post("/api/admin/members", json={"code": stud.code.replace("-", "").lower(), "group": "БИА 1"},
                          headers=star.h)
    assert r.json()["status"] == "added"
    stud = await refresh(client, stud)
    assert stud.me["student"]["group"] == "БИА 1"
    assert (await client.get("/api/schedule", headers=stud.h)).status_code == 200
    feed = (await client.get("/api/notifications", headers=stud.h)).json()
    assert feed["unread"] == 1 and feed["items"][0]["title"] == "✅ Ты в группе БИА 1"
    assert "староста" in feed["items"][0]["body"]

    # Староста видит студентов своей группы: кто в приложении и их коды
    students = (await client.get("/api/admin/students", headers=star.h)).json()
    assert {s["full_name"]: s["linked"] for s in students} == {
        "Старостина Мария Петровна": True, "Студентов Студент": True}
    assert "login" not in students[0]

    # Чужую группу староста не трогает; студента из другой группы не забирает — переводит админ
    await client.post("/api/admin/groups", json={"name": "БИА 2"}, headers=boss.h)
    other = await register(client, "other", "Другой Студент")
    r = await client.post("/api/admin/members", json={"code": other.code, "group": "БИА 2"}, headers=star.h)
    assert r.status_code == 403
    await client.post("/api/admin/members", json={"code": other.code, "group": "БИА 2"}, headers=boss.h)
    r = await client.post("/api/admin/members", json={"code": other.code, "group": "БИА 1"}, headers=star.h)
    assert r.status_code == 409 and "уже в группе БИА 2" in r.json()["detail"]
    r = await client.post("/api/admin/members", json={"code": other.code, "group": "БИА 1"}, headers=boss.h)
    assert r.json()["status"] == "moved" and r.json()["group"] == "БИА 1"
    assert (await client.get("/api/admin/lookup", params={"code": "ZZZ-ZZZ"}, headers=star.h)).status_code == 404

    # Староста убирает студента из группы: аккаунт остаётся, но уже без группы
    sid = (await refresh(client, stud)).me["student"]["id"]
    assert (await client.delete(f"/api/admin/students/{sid}", headers=star.h)).status_code == 200
    stud = await refresh(client, stud)
    assert stud.me["student"] is None
    titles = [n["title"] for n in (await client.get("/api/notifications", headers=stud.h)).json()["items"]]
    assert titles[0] == "Ты больше не в группе БИА 1"
    # Себя староста не удаляет
    own = star.me["student"]["id"]
    assert (await client.delete(f"/api/admin/students/{own}", headers=star.h)).status_code == 400


async def test_starosta_resets_password(client, database):
    boss = await owner(client)
    await client.post("/api/admin/groups", json={"name": "А"}, headers=boss.h)
    star = await register(client, "star", "Староста Группы")
    stud = await register(client, "stud", "Забыл Пароль")
    stranger = await register(client, "far", "Чужой Студент")
    for acc in (star, stud):
        await client.post("/api/admin/members", json={"code": acc.code, "group": "А"}, headers=boss.h)
    await client.put(f"/api/admin/users/{star.id}/role", json={"role": "starosta"}, headers=boss.h)

    r = await client.post(f"/api/admin/users/{stud.id}/password", headers=star.h)
    temp = r.json()["password"]
    assert r.json()["login"] == "stud" and len(temp) == 8
    assert (await client.get("/api/me", headers=stud.h)).status_code == 401  # старые входы сброшены
    assert (await client.post("/api/auth/login", json={"login": "stud", "password": temp})).status_code == 200
    assert (await client.post(f"/api/admin/users/{stranger.id}/password", headers=star.h)).status_code == 403
    assert (await client.post(f"/api/admin/users/{boss.id}/password", headers=star.h)).status_code == 403


# --- уведомления ------------------------------------------------------------------

async def test_contact_admins_and_reply(client, database):
    boss = await owner(client)
    stud = await register(client, "stud", "Пишущий Студент")
    r = await client.post("/api/me/contact", json={"topic": "fio", "text": "Ошибка в фамилии"}, headers=stud.h)
    assert r.json()["delivered"] == 1
    feed = (await client.get("/api/notifications", headers=boss.h)).json()
    msg = feed["items"][0]
    assert msg["kind"] == "contact" and msg["can_reply"] and msg["sender"]["name"] == "Пишущий Студент"
    assert stud.code in msg["body"]

    r = await client.post(f"/api/notifications/{msg['id']}/reply", json={"text": "Исправил"}, headers=boss.h)
    assert r.status_code == 200
    assert (await client.get("/api/notifications", headers=boss.h)).json()["unread"] == 0
    reply = (await client.get("/api/notifications", headers=stud.h)).json()["items"][0]
    assert reply["kind"] == "reply" and "Исправил" in reply["body"]
    # Чужое уведомление не ответить
    assert (await client.post(f"/api/notifications/{msg['id']}/reply", json={"text": "x"},
                              headers=stud.h)).status_code == 404

    # Фоновая проверка на телефоне: только новые непрочитанные
    new = (await client.get("/api/notifications/new", params={"after_id": 0}, headers=stud.h)).json()
    assert [n["id"] for n in new["items"]] == [reply["id"]]
    assert (await client.get("/api/notifications/new", params={"after_id": reply["id"]},
                             headers=stud.h)).json()["items"] == []
    r = await client.post("/api/notifications/read", json={}, headers=stud.h)
    assert r.json()["unread"] == 0


async def test_announce(client, database):
    boss = await owner(client)
    for g in ("А", "Б"):
        await client.post("/api/admin/groups", json={"name": g}, headers=boss.h)
    star = await register(client, "star", "Староста Группы")
    a1 = await register(client, "anya", "Студент Первый")
    b1 = await register(client, "boris", "Студент Второй")
    for acc, g in ((star, "А"), (a1, "А"), (b1, "Б")):
        await client.post("/api/admin/members", json={"code": acc.code, "group": g}, headers=boss.h)
    await client.put(f"/api/admin/users/{star.id}/role", json={"role": "starosta"}, headers=boss.h)

    r = await client.post("/api/announce", json={"groups": ["А"], "text": "Завтра пары не будет"}, headers=star.h)
    assert r.json()["delivered"] == 1
    item = (await client.get("/api/notifications", headers=a1.h)).json()["items"][0]
    assert item["kind"] == "announce" and item["body"] == "Завтра пары не будет" and item["can_reply"]
    assert (await client.post("/api/announce", json={"groups": ["Б"], "text": "x"}, headers=star.h)).status_code == 403
    assert (await client.post("/api/announce", json={"groups": ["А"], "text": "x"}, headers=a1.h)).status_code == 403
    r = await client.post("/api/announce", json={"groups": ["А", "Б"], "text": "Всем"}, headers=boss.h)
    assert r.json()["delivered"] == 3


async def test_schedule_edits_notify_group(client, seeded):
    boss = await owner(client)
    code = (await client.get("/api/admin/group-code", params={"group": "ГР 2"}, headers=boss.h)).json()["code"]
    stud = await register(client, "elkin", "Ёлкин Пётр Иванович", code=code)
    lessons = (await client.get("/api/admin/lessons", params={"group": "ГР 2"}, headers=boss.h)).json()
    body = {**{k: lessons[0][k] for k in ("group", "weekday", "week", "pair_num", "start", "end",
                                          "kind", "room", "teacher", "half")}, "subject": "Химия-2"}
    assert (await client.put(f"/api/admin/lessons/{lessons[0]['id']}", json=body, headers=boss.h)).status_code == 200
    # Ничего не поменялось — уведомления нет
    await client.put(f"/api/admin/lessons/{lessons[0]['id']}", json=body, headers=boss.h)
    await client.delete(f"/api/admin/lessons/{lessons[0]['id']}", headers=boss.h)
    items = (await client.get("/api/notifications", headers=stud.h)).json()["items"]
    assert [n["kind"] for n in items] == ["schedule", "schedule"]
    assert "Было: Вт, 09:00–10:30 — Химия, ауд. 401\nСтало: Вт, 09:00–10:30 — Химия-2, ауд. 401" in items[1]["body"]
    assert items[0]["body"].startswith("Пары больше нет: Вт, 09:00–10:30 — Химия-2, ауд. 401")


# --- расписание и админка ------------------------------------------------------------

async def test_admin_flow(client, database, schedule_xlsx, students_xlsx):
    boss = await owner(client)
    h = boss.h
    for data in (schedule_xlsx, students_xlsx):
        r = await client.post("/api/admin/import", headers=h, files={"file": ("x.xlsx", data)})
        assert r.status_code == 200, r.text
    assert (await client.get("/api/admin/groups", headers=h)).json() == ["ГР 1", "ГР 2"]

    r = await client.get("/api/schedule", params={"date": "2026-10-01", "group": "ГР 1"}, headers=h)
    week = r.json()
    assert week["week_number"] == 5 and week["parity"] == "odd"
    assert [l["half"] for l in week["days"][3]["lessons"]] == [1, 2]

    lessons = (await client.get("/api/admin/lessons", params={"group": "ГР 2"}, headers=h)).json()
    bad = {**{k: lessons[0][k] for k in ("group", "weekday", "week", "pair_num", "end", "subject",
                                         "kind", "room", "teacher", "half")}, "start": "25:00"}
    assert (await client.post("/api/admin/lessons", json=bad, headers=h)).status_code == 422

    r = await client.put("/api/admin/config", headers=h, json={
        "semester_start": "2026-09-08", "bells": [{"start": "09:00", "end": "10:30"}]})
    assert r.status_code == 200
    r = await client.get("/api/schedule", params={"date": "2026-10-01", "group": "ГР 1"}, headers=h)
    assert r.json()["parity"] == "even"

    st = (await client.get("/api/admin/stats", headers=h)).json()
    assert st["students"] == 3 and st["registered"] == 0 and st["accounts"] == 1
    assert (await client.delete("/api/admin/groups/ГР 1", headers=h)).status_code == 409


async def test_student_settings_and_plan(client, seeded):
    boss = await owner(client)
    code = (await client.get("/api/admin/group-code", params={"group": "ГР 1"}, headers=boss.h)).json()["code"]
    stud = await register(client, "testov", "Тестов Тест Тестович", code=code)
    h = stud.h
    assert (await client.get("/api/admin/groups", headers=h)).status_code == 403
    r = await client.put("/api/me/settings", headers=h, json={
        "notify_before": 10, "digest_time": "07:30", "digest_day": "today", "half": 2})
    assert r.status_code == 200
    me = (await refresh(client, stud)).me
    assert me["settings"] == {"notify_before": 10, "digest_time": "07:30", "digest_day": "today"}
    assert me["half"] == 2
    r = await client.get("/api/schedule", params={"date": "2026-10-01"}, headers=h)
    assert [l["half"] for l in r.json()["days"][3]["lessons"]] == [2]
    assert (await client.get("/api/schedule", params={"group": "ГР 2"}, headers=h)).status_code == 403
    plan = (await client.get("/api/me/plan", headers=h)).json()
    assert plan["timezone"] == "Europe/Moscow" and isinstance(plan["items"], list)
    assert (await client.put("/api/me/settings", headers=h, json={"digest_time": "7:30"})).status_code == 422


async def test_user_without_group_cannot_save_settings(client, database):
    acc = await register(client, "lonely", "Без Группы")
    assert (await client.put("/api/me/settings", headers=acc.h, json={"notify_before": 10})).status_code == 403
    assert (await client.get("/api/me/plan", headers=acc.h)).json()["items"] == []


async def test_roles_rules(client, database):
    boss = await owner(client)
    await client.post("/api/admin/groups", json={"name": "А"}, headers=boss.h)
    adm = await register(client, "adm", "Админ Второй")
    stud = await register(client, "stud", "Просто Студент")
    # Старостой — только того, кто в группе
    r = await client.put(f"/api/admin/users/{stud.id}/role", json={"role": "starosta"}, headers=boss.h)
    assert r.status_code == 400
    assert (await client.put(f"/api/admin/users/{adm.id}/role", json={"role": "admin"}, headers=boss.h)).status_code == 200
    adm = await refresh(client, adm)
    # Админ не назначает админов и не трогает главного
    assert (await client.put(f"/api/admin/users/{stud.id}/role", json={"role": "admin"},
                             headers=adm.h)).status_code == 400
    assert (await client.put(f"/api/admin/users/{boss.id}/role", json={"role": "user"},
                             headers=adm.h)).status_code == 400
    team = (await client.get("/api/admin/admins", headers=adm.h)).json()
    assert [a["role"] for a in team["admins"]] == ["owner", "admin"]
    assert (await client.delete(f"/api/admin/admins/{adm.id}", headers=boss.h)).status_code == 200
    assert (await refresh(client, adm)).me["role"] == "user"


async def test_backup_download(client, database):
    boss = await owner(client)
    r = await client.get("/api/admin/backup", headers=boss.h)
    assert r.status_code == 200 and r.content.startswith(b"SQLite format 3")
    assert "schedule-backup-" in r.headers["content-disposition"]
    stud = await register(client, "stud", "Студент Любопытный")
    assert (await client.get("/api/admin/backup", headers=stud.h)).status_code == 403


# --- веб-приложение ----------------------------------------------------------------

async def test_webapp_is_never_stale(client):
    page = (await client.get("/")).text
    import re

    assert re.search(r'src="app\.js\?v=[0-9a-f]{10}"', page) and re.search(r'href="style\.css\?v=[0-9a-f]{10}"', page)
    assert "telegram" not in page.lower()
    assert (await client.get("/index.html")).text == page
    r = await client.get("/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert (await client.get("/api/me")).headers["cache-control"] == "no-store"
    assert (await client.get("/api/info")).json()["app"] == "schedule"


async def test_cors_for_mobile_app(client):
    for origin in ("capacitor://localhost", "https://localhost"):
        r = await client.options("/api/auth/login", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin
    r = await client.get("/api/info", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


async def test_legacy_import(tmp_path, database, monkeypatch):
    """Перенос из базы бота: списки, расписание, журнал — без аккаунтов Telegram."""
    from app import legacy

    old = tmp_path / "bot.db"
    con = sqlite3.connect(old)
    con.executescript("""
        CREATE TABLE users (tg_id BIGINT PRIMARY KEY, role VARCHAR(16), student_id INTEGER);
        CREATE TABLE students (id INTEGER PRIMARY KEY, full_name TEXT, name_key TEXT, group_name TEXT, email TEXT);
        CREATE TABLE group_info (group_name TEXT PRIMARY KEY, curator TEXT, curator_contact TEXT);
        CREATE TABLE lessons (id INTEGER PRIMARY KEY, group_name TEXT, weekday INT, week TEXT, pair_num INT,
            start_time TEXT, end_time TEXT, subject TEXT, kind TEXT, room TEXT, teacher TEXT, half INT);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE attendance_sessions (id INTEGER PRIMARY KEY, teacher_id BIGINT, teacher_name TEXT, subject TEXT,
            lesson_date DATE, start_time TEXT, end_time TEXT, half INT, code TEXT, code_expires_at DATETIME,
            created_at DATETIME, closed_at DATETIME);
        CREATE TABLE attendance_groups (session_id INT, group_name TEXT);
        CREATE TABLE attendance_marks (session_id INT, student_id INT, marked_at DATETIME, method TEXT);
        INSERT INTO users VALUES (111, 'owner', NULL);
        INSERT INTO students VALUES (5, 'Иванов Иван', 'иванов иван', 'А', 'i@mpgu.su');
        INSERT INTO group_info VALUES ('А', 'Куратор К.', '@kurator');
        INSERT INTO lessons VALUES (1, 'А', 0, 'every', 1, '09:00', '10:30', 'Химия', '', '', '', NULL);
        INSERT INTO settings VALUES ('semester_start', '"2026-09-08"');
        INSERT INTO attendance_sessions VALUES (1, 111, 'Сидоров С.', 'Химия', '2026-10-05', '09:00', '10:30', 0,
            '1234', '2026-10-05 06:01:00', '2026-10-05 06:00:00', NULL);
        INSERT INTO attendance_groups VALUES (1, 'А');
        INSERT INTO attendance_marks VALUES (1, 5, '2026-10-05 06:00:30', 'code');
    """)
    con.commit()
    con.close()
    new = tmp_path / "test.db"  # база из фикстуры database, схема уже создана
    counts = legacy.copy_data(str(old), str(new))
    assert counts["students"] == 1 and counts["attendance_marks"] == 1
    async with database.session() as s:
        st = await s.get(Student, 5)
        assert st.email == "i@mpgu.su"
        assert await s.scalar(select(User)) is None
    import pytest

    with pytest.raises(SystemExit):
        legacy.copy_data(str(old), str(new))  # второй раз в непустую базу — нельзя
