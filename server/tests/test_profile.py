"""Правила пароля, смена ФИО в профиле и очистка журнала посещаемости."""

import pytest
from sqlalchemy import func, select

from app import security, users, wipe_attendance
from app.models import AttendanceGroup, AttendanceMark, AttendanceSession, Student
from tests.conftest import PASSWORD, owner, register


@pytest.mark.parametrize("password,missing", [
    ("Parol123", []),
    ("Пароль123", []),
    ("parol123", ["заглавная буква"]),
    ("PAROL123", ["строчная буква"]),
    ("Parolparol", ["цифра"]),
    ("Pa1", ["хотя бы 8 символов"]),
    ("12345678", ["заглавная буква", "строчная буква"]),
])
def test_password_rules(password, missing):
    assert users.password_problems(password) == missing


def test_password_check_messages():
    with pytest.raises(users.AccountError, match="Нужны: заглавная буква, цифра"):
        users.check_password("parolparol")
    with pytest.raises(users.AccountError, match="без пробелов"):
        users.check_password("Parol 123")
    with pytest.raises(users.AccountError, match="совпадать с логином"):
        users.check_password("Ivanov123", "ivanov123")
    for _ in range(50):  # временный пароль от старосты всегда проходит правила
        users.check_password(security.temp_password())


async def test_register_checks_password_and_name(client):
    async def reg(full_name, password, login="petrov"):
        return await client.post("/api/auth/register", json={"login": login, "full_name": full_name, "password": password})

    r = await reg("Петров Иван", "parol123")
    assert r.status_code == 422 and "заглавная буква" in r.json()["detail"]
    r = await reg("Петров Иван", "Petrov123", login="petrov123")
    assert r.status_code == 422 and "логином" in r.json()["detail"]
    r = await reg("Петров 1ван", PASSWORD)
    assert r.status_code == 422 and "только буквы" in r.json()["detail"]
    r = await reg("Петрова-Водкина Анна-Мария Д'Арк", PASSWORD)
    assert r.status_code == 200, r.text


async def test_change_name_in_profile(client, seeded):
    boss = await owner(client)
    code = (await client.get("/api/admin/group-code", params={"group": "ГР 1"}, headers=boss.h)).json()["code"]
    star = await register(client, "star", "Тестов Тест Тестович", code=code)
    await client.put(f"/api/admin/users/{star.id}/role", headers=boss.h, json={"role": "starosta"})
    stud = await register(client, "anna", "Пробная Анна Сергеевна", code=code)

    r = await client.put("/api/me/name", headers=stud.h, json={"full_name": "  Пробная   Анна  Викторовна "})
    assert r.status_code == 200 and r.json()["full_name"] == "Пробная Анна Викторовна"
    me = (await client.get("/api/me", headers=stud.h)).json()
    assert me["full_name"] == "Пробная Анна Викторовна" and me["student"]["full_name"] == "Пробная Анна Викторовна"
    # Староста видит новое ФИО в списке группы и получает уведомление
    names = [x["full_name"] for x in (await client.get("/api/admin/students", headers=star.h)).json()]
    assert "Пробная Анна Викторовна" in names and "Пробная Анна Сергеевна" not in names
    inbox = (await client.get("/api/notifications", headers=star.h)).json()["items"]
    assert inbox[0]["title"] == "Студент сменил ФИО"
    assert "Пробная Анна Сергеевна → Пробная Анна Викторовна" in inbox[0]["body"]

    # Чужое ФИО в своей группе и не-ФИО — нельзя
    r = await client.put("/api/me/name", headers=stud.h, json={"full_name": "Тестов Тест Тестович"})
    assert r.status_code == 409
    r = await client.put("/api/me/name", headers=stud.h, json={"full_name": "Анна"})
    assert r.status_code == 422
    # Без группы — меняется только имя аккаунта
    lone = await register(client, "lone", "Одинокий Олег Олегович")
    r = await client.put("/api/me/name", headers=lone.h, json={"full_name": "Одинокий Олег Петрович"})
    assert (await client.get("/api/me", headers=lone.h)).json()["full_name"] == "Одинокий Олег Петрович"


async def test_wipe_attendance(database, tmp_path):
    async with database.session() as s:
        s.add(Student(full_name="Тестов Тест", name_key="тестов тест", group_name="ГР 1"))
        await s.flush()
        sid = await s.scalar(select(Student.id))
        x = AttendanceSession(teacher_name="Петров", subject="Химия", lesson_date=security.utcnow().date(),
                              created_at=security.utcnow(), groups=[AttendanceGroup(group_name="ГР 1")], marks=[])
        s.add(x)
        await s.flush()
        s.add(AttendanceMark(session_id=x.id, student_id=sid, marked_at=security.utcnow()))
        await s.commit()
    assert await wipe_attendance.count() == (1, 1)
    sessions, marks, path = await wipe_attendance.wipe(str(tmp_path))
    assert (sessions, marks) == (1, 1) and path.startswith(str(tmp_path))
    async with database.session() as s:
        for model in (AttendanceSession, AttendanceGroup, AttendanceMark):
            assert await s.scalar(select(func.count()).select_from(model)) == 0
        assert await s.scalar(select(func.count()).select_from(Student)) == 1  # студенты остались


async def test_wipe_once(database, tmp_path):
    async with database.session() as s:
        s.add(AttendanceSession(teacher_name="Петров", subject="Химия", lesson_date=security.utcnow().date(),
                                created_at=security.utcnow(), groups=[AttendanceGroup(group_name="ГР 1")], marks=[]))
        await s.commit()
    assert await wipe_attendance.wipe_once(str(tmp_path)) == 1
    assert await wipe_attendance.count() == (0, 0)
    # Второй запуск ничего не трогает: новые отметки остаются
    async with database.session() as s:
        s.add(AttendanceSession(teacher_name="Петров", subject="Химия", lesson_date=security.utcnow().date(),
                                created_at=security.utcnow(), groups=[AttendanceGroup(group_name="ГР 1")], marks=[]))
        await s.commit()
    assert await wipe_attendance.wipe_once(str(tmp_path)) is None
    assert await wipe_attendance.count() == (1, 0)


async def test_initials_are_not_a_name(client):
    for bad in ("admin t t", "д.д.д", "1 1 1", "И. Иванов"):
        r = await client.post("/api/auth/register", json={"login": "x" + str(abs(hash(bad)) % 9999), "full_name": bad,
                                                          "password": PASSWORD})
        assert r.status_code == 422, bad


async def test_security_and_cache_headers(client):
    page = await client.get("/")
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert page.headers["cache-control"] == "no-cache" and page.headers["x-content-type-options"] == "nosniff"
    assert (await client.get("/icon-192.png")).headers["cache-control"] == "public, max-age=2592000"
    assert "immutable" in (await client.get("/app.js?v=abc")).headers["cache-control"]
    assert (await client.get("/healthz")).headers["cache-control"] == "no-cache"
    api = await client.get("/api/me")
    assert api.headers["cache-control"] == "no-store" and "content-security-policy" not in api.headers


async def test_bad_names_cleanup(client, database, tmp_path):
    from app import bad_names
    boss = await owner(client)
    good = await register(client, "good", "Хороший Хорош Хорошевич")
    async with database.session() as s:  # пробные аккаунты из старой версии, когда ФИО не проверялось
        for login, fio in (("ddd", "д.д.д ж"), ("ones", "1 1 1"), ("adm", "admin t t")):
            await users.create_user(s, login, PASSWORD, "Временный Временный")
            u = await s.scalar(select(users.User).where(users.User.login == login))
            u.full_name = fio
        await s.commit()
    report = await bad_names.run(False)
    assert len(report) == 4 and any("admin t t" in line for line in report)
    report = await bad_names.run(True, str(tmp_path))
    assert sum("удалён" in line for line in report) == 3
    async with database.session() as s:
        logins = set((await s.scalars(select(users.User.login))).all())
    assert logins == {boss.me["login"], "good"}
    assert (await bad_names.run(False)) == ["Аккаунтов с неправильным ФИО нет."]


@pytest.mark.parametrize("module", ["app.wipe_attendance", "app.bad_names"])
def test_cli_commands_run(module, tmp_path):
    """Команды запускаются так же, как на сервере: python -m … — сами подключаются к базе."""
    import os
    import subprocess
    import sys
    env = {**os.environ, "DB_PATH": str(tmp_path / "app.db"), "BACKUP_DIR": str(tmp_path / "backups")}
    for args in ([], ["--yes"]):
        r = subprocess.run([sys.executable, "-m", module, *args], env=env, capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.dirname(__file__)), timeout=60)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip()
