from datetime import time

import pytest
from sqlalchemy import select

from app.config import DEFAULT_BELLS
from app.importer import (
    ImportError_,
    detect_kind,
    import_schedule,
    import_students,
    name_key,
    parse_schedule,
    parse_students,
    parse_time,
    parse_week,
    split_half_from_subject,
)
from app.models import Lesson, Student, User
from tests.conftest import make_xlsx


def test_detect_kind(schedule_xlsx, students_xlsx):
    assert detect_kind(schedule_xlsx) == "schedule"
    assert detect_kind(students_xlsx) == "students"
    with pytest.raises(ImportError_):
        detect_kind(make_xlsx([["что-то", "другое"]]))
    with pytest.raises(ImportError_):
        detect_kind(b"not an xlsx")


def test_parse_schedule(schedule_xlsx):
    parsed = parse_schedule(schedule_xlsx, DEFAULT_BELLS)
    assert parsed.errors == []
    assert len(parsed.lessons) == 6
    halves = [l for l in parsed.lessons if l["half"]]
    assert [(l["subject"], l["half"], l["start_time"]) for l in halves] == [
        ("Анатомия", 1, "12:40"),  # время подставлено из сетки звонков
        ("Анатомия", 2, "16:00"),
    ]
    chem = parsed.lessons[-1]
    assert chem["week"] == "every" and chem["start_time"] == "09:00"
    assert chem["kind"] == "лабораторная" and chem["room"] == "401"


def test_parse_schedule_reports_bad_rows():
    data = make_xlsx([
        ["Группа", "День", "Предмет", "Начало", "Конец"],
        ["Г", "Funday", "X", "09:00", "10:00"],
        ["Г", "Пн", "X", "", ""],
        ["Г", "Пн", "OK", "09:00", "10:00"],
    ])
    parsed = parse_schedule(data, DEFAULT_BELLS)
    assert len(parsed.lessons) == 1
    assert len(parsed.errors) == 2 and parsed.errors[0].startswith("строка 2")


@pytest.mark.parametrize("raw,expected", [
    ("каждая", "every"), ("", "every"), (None, "every"),
    ("нечётная", "odd"), ("Нечетная", "odd"), ("числитель", "odd"),
    ("чётная", "even"), ("знаменатель", "even"),
])
def test_parse_week(raw, expected):
    assert parse_week(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("9:00", "09:00"), ("09.30", "09:30"), (time(14, 20), "14:20"), (0.375, "09:00"), (None, None),
])
def test_parse_time(raw, expected):
    assert parse_time(raw) == expected


def test_split_half():
    assert split_half_from_subject("Анатомия (2-я половина группы — уточни)") == ("Анатомия", 2)
    assert split_half_from_subject("Анатомия (лекция)") == ("Анатомия (лекция)", None)


def test_name_key():
    assert name_key("  Ёлкин   пётр Иванович ") == name_key("елкин Петр иванович")


def test_parse_students_skips_duplicates():
    rows, errors = parse_students(make_xlsx([
        ["ФИО", "Группа"], ["Иванов Иван", "А"], ["иванов  иван", "Б"], ["", "А"],
    ]))
    assert rows == [("Иванов Иван", "А", None)]
    assert len(errors) == 2


def test_parse_students_email_column():
    rows, errors = parse_students(make_xlsx([
        ["ФИО", "Группа", "Корпоративная почта"],
        ["Иванов Иван", "А", " ivanov@mpgu.su "], ["Петров Пётр", "А", "не почта"], ["Сидоров Сид", "А", None],
    ]))
    assert rows == [("Иванов Иван", "А", "ivanov@mpgu.su"), ("Петров Пётр", "А", None), ("Сидоров Сид", "А", None)]
    assert len(errors) == 1 and "не похоже на почту" in errors[0]


async def test_import_students_email_kept_when_cell_empty(database):
    async with database.session() as s:
        await import_students(s, make_xlsx([["ФИО", "Группа", "Почта"], ["Иванов Иван", "А", "ivanov@mpgu.su"]]))
        # Файл без столбца «Почта» — почта не стирается
        await import_students(s, make_xlsx([["ФИО", "Группа"], ["Иванов Иван", "А"]]))
        st = await s.scalar(select(Student))
        assert st.email == "ivanov@mpgu.su"
        await import_students(s, make_xlsx([["ФИО", "Группа", "Почта"], ["Иванов Иван", "А", "new@mpgu.su"]]))
        await s.refresh(st)
        assert st.email == "new@mpgu.su"


async def test_bot_database_is_rejected(tmp_path):
    """По DB_PATH лежит база Telegram-бота — сервер не стартует, а подсказывает, как её перенести."""
    import sqlite3

    import pytest

    from app import db

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (tg_id BIGINT PRIMARY KEY, role VARCHAR(16))")
    con.commit()
    con.close()
    db.setup(f"sqlite+aiosqlite:///{path}")
    with pytest.raises(db.LegacyDatabaseError, match="app.legacy"):
        await db.init_db()
    await db.engine.dispose()


async def test_import_schedule_replaces_only_listed_groups(database, schedule_xlsx):
    async with database.session() as s:
        s.add(Lesson(group_name="ДРУГАЯ", weekday=0, week="every", start_time="09:00",
                     end_time="10:00", subject="Не трогать"))
        s.add(Lesson(group_name="ГР 1", weekday=0, week="every", start_time="09:00",
                     end_time="10:00", subject="Старое"))
        await s.commit()
        msg = await import_schedule(s, schedule_xlsx)
        assert "6 пар" in msg
        subjects = set((await s.scalars(select(Lesson.subject))).all())
    assert "Не трогать" in subjects and "Старое" not in subjects


def _account(n: int, student_id: int | None = None) -> User:
    return User(login=f"u{n}", password_hash="x", full_name=f"Студент Номер{n}", code=f"CODE{n:02d}",
                student_id=student_id, digest_day="tomorrow")


async def test_import_students_keeps_links(database, students_xlsx):
    async with database.session() as s:
        await import_students(s, students_xlsx)
        st = await s.scalar(select(Student).where(Student.full_name == "Тестов Тест Тестович"))
        s.add(_account(1, st.id))
        gone = await s.scalar(select(Student).where(Student.full_name == "Пробная Анна Сергеевна"))
        s.add(_account(2, gone.id))
        await s.commit()

        # Повторная загрузка: Тестов перешёл в другую группу. Пробной в файле нет, но она уже
        # в приложении — запись остаётся, удаляет её староста или админ вручную. Ёлкина тоже нет — удаляется.
        msg = await import_students(s, make_xlsx([
            ["ФИО", "Группа"], ["Тестов Тест Тестович", "ГР 2"], ["Новый Студент Петрович", "ГР 2"],
        ]))
        assert "новых: 1, изменено: 1, удалено: 1" in msg and "оставлены: 1" in msg
        s.expire_all()
        u1, u2 = await s.scalar(select(User).where(User.login == "u1")), await s.scalar(select(User).where(User.login == "u2"))
        assert u1.student_id == st.id and u1.student.group_name == "ГР 2"
        assert u2.student_id == gone.id
        names = set((await s.scalars(select(Student.full_name))).all())
        assert names == {"Тестов Тест Тестович", "Пробная Анна Сергеевна", "Новый Студент Петрович"}
