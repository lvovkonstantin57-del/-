import io
import os

os.environ.setdefault("OWNER_CODE", "secret-code")
os.environ.setdefault("ADMIN_CONTACT", "@starosta")
os.environ.setdefault("SEMESTER_START", "2026-09-01")
os.environ.setdefault("PASSWORD_ITERATIONS", "1000")  # в тестах хэшируем быстро

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from openpyxl import Workbook  # noqa: E402

from app import attendance, db, security  # noqa: E402
from app.api.routes import create_app  # noqa: E402
from app.importer import import_schedule, import_students  # noqa: E402

SCHEDULE_HEADER = ["Группа", "День", "Неделя", "№ пары", "Начало", "Конец", "Предмет", "Тип",
                   "Аудитория", "Преподаватель"]
PASSWORD = "parol123"


def make_xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def schedule_xlsx() -> bytes:
    return make_xlsx([
        SCHEDULE_HEADER,
        ["ГР 1", "Понедельник", "каждая", 1, "09:00", "10:30", "Физкультура", "практика", "Спортзал", "Петров П. П."],
        ["ГР 1", "Понедельник", "нечётная", 2, "10:40", "12:10", "Ботаника", "лекция", "304", "Иванов И. И."],
        ["ГР 1", "Понедельник", "чётная", 2, "10:40", "12:10", "Зоология", "лекция", "304", "Сидоров С. С."],
        ["ГР 1", "Чт", "нечётная", 3, None, None,
         "Анатомия (1-я половина группы — уточни, какая твоя)", "практика", "306", "Холмов Х. Х."],
        ["ГР 1", "Чт", "нечётная", 5, None, None,
         "Анатомия (2-я половина группы — уточни, какая твоя)", "практика", "306", "Холмов Х. Х."],
        ["ГР 2", "Вторник", "", 1, "9:00", "10:30", "Химия", "Лабораторная", 401, ""],
    ])


@pytest.fixture
def students_xlsx() -> bytes:
    return make_xlsx([
        ["ФИО", "Группа"],
        ["Тестов Тест Тестович", "ГР 1"],
        ["Пробная Анна Сергеевна", "ГР 1"],
        ["Ёлкин Пётр Иванович", "ГР 2"],
    ])


@pytest.fixture(autouse=True)
def _fresh_limits():
    security.reset_limits()
    attendance._fails.clear()
    yield
    security.reset_limits()
    attendance._fails.clear()


@pytest_asyncio.fixture
async def database(tmp_path):
    db.setup(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await db.init_db()
    yield db
    await db.engine.dispose()


@pytest_asyncio.fixture
async def seeded(database, schedule_xlsx, students_xlsx):
    """База с расписанием и списком студентов из фикстур выше."""
    async with database.session() as s:
        await import_schedule(s, schedule_xlsx)
        await import_students(s, students_xlsx)
    return database


@pytest_asyncio.fixture
async def client(database):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c


class Account:
    """Зарегистрированный в тестах пользователь: заголовки для запросов и данные из /api/me."""

    def __init__(self, token: str, me: dict):
        self.token = token
        self.me = me
        self.h = {"Authorization": f"Bearer {token}"}

    @property
    def id(self) -> int:
        return self.me["id"]

    @property
    def code(self) -> str:
        return self.me["code"]


async def register(client, login: str, fio: str, code: str | None = None) -> Account:
    body = {"login": login, "full_name": fio, "password": PASSWORD}
    if code:
        body["code"] = code
    r = await client.post("/api/auth/register", json=body)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    me = (await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})).json()
    return Account(token, me)


async def owner(client, login: str = "owner") -> Account:
    acc = await register(client, login, "Главный Админ Админович")
    r = await client.post("/api/me/code", json={"code": "secret-code"}, headers=acc.h)
    assert r.json()["kind"] == "owner", r.text
    acc.me = (await client.get("/api/me", headers=acc.h)).json()
    return acc


async def refresh(client, acc: Account) -> Account:
    acc.me = (await client.get("/api/me", headers=acc.h)).json()
    return acc
