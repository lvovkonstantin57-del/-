"""Аккаунты: регистрация, вход, личные коды и вступление в группу."""

import re

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import security
from app.importer import clean_group, name_key
from app.models import ROLE_USER, AuthToken, GroupInfo, Student, User
from app.security import GROUP_CODE_LEN, PERSONAL_CODE_LEN, utcnow

LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._@+-]{2,63}$")
PASSWORD_MIN = 8
PASSWORD_MAX = 128


class AccountError(Exception):
    """Ошибка для человека: текст показываем в приложении."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def clean_login(text: str | None) -> str | None:
    login = (text or "").strip().lower()
    return login if LOGIN_RE.match(login) else None


def clean_fio(text: str | None) -> str | None:
    """ФИО без лишних пробелов; None — если это не похоже на ФИО."""
    fio = " ".join((text or "").split())
    return fio if len(fio.split()) >= 2 and len(fio) <= 200 else None


# Буквы любого алфавита, дефис (Петрова-Водкина), апостроф (Д'Артаньян) и точка (инициалы)
FIO_WORD_RE = re.compile(r"^[^\W\d_]+(?:[-'’.][^\W\d_]*)*$")


def person_fio(text: str | None) -> str:
    """ФИО, которое человек вводит сам: фамилия и имя обязательно, только буквы. Иначе AccountError."""
    fio = clean_fio(text)
    if fio is None:
        raise AccountError("Напиши фамилию и имя — лучше полностью, с отчеством", 422)
    words = fio.split()
    if not all(FIO_WORD_RE.match(word) for word in words):
        raise AccountError("В ФИО — только буквы и дефис, без цифр и значков", 422)
    if any(sum(ch.isalpha() for ch in word) < 2 for word in words[:2]):
        raise AccountError("Фамилию и имя — полностью, не инициалами", 422)
    return fio


def password_problems(password: str) -> list[str]:
    """Чего не хватает паролю по обычным правилам: 8+ символов, заглавная и строчная буквы, цифра."""
    missing = []
    if len(password) < PASSWORD_MIN:
        missing.append(f"хотя бы {PASSWORD_MIN} символов")
    if not any(ch.isupper() for ch in password):
        missing.append("заглавная буква")
    if not any(ch.islower() for ch in password):
        missing.append("строчная буква")
    if not any(ch.isdigit() for ch in password):
        missing.append("цифра")
    return missing


def check_password(password: str, login: str | None = None) -> None:
    if len(password) > PASSWORD_MAX:
        raise AccountError("Слишком длинный пароль", 422)
    if any(ch.isspace() for ch in password):
        raise AccountError("Пароль без пробелов", 422)
    missing = password_problems(password)
    if missing:
        raise AccountError("Пароль слишком простой. Нужны: " + ", ".join(missing), 422)
    if login and password.casefold() == login.casefold():
        raise AccountError("Пароль не должен совпадать с логином", 422)


async def _unique_code(s: AsyncSession, model, column, length: int) -> str:
    while True:
        code = security.random_code(length)
        if not await s.scalar(select(model).where(column == code)):
            return code


async def create_user(s: AsyncSession, login: str, password: str, full_name: str) -> User:
    """Новый аккаунт с личным кодом. Коммитит."""
    clean = clean_login(login)
    if clean is None:
        raise AccountError("Логин — от 3 символов: латиница, цифры, точка, дефис или почта", 422)
    fio = person_fio(full_name)
    check_password(password, clean)
    if await s.scalar(select(User.id).where(User.login == clean)):
        raise AccountError("Такой логин уже занят", 409)
    user = User(
        login=clean, password_hash=security.hash_password(password), full_name=fio,
        code=await _unique_code(s, User, User.code, PERSONAL_CODE_LEN), role=ROLE_USER, digest_day="tomorrow",
    )
    s.add(user)
    await s.commit()
    await s.refresh(user, attribute_names=["student", "teacher"])
    return user


async def authenticate(s: AsyncSession, login: str, password: str) -> User | None:
    user = await s.scalar(select(User).where(User.login == (login or "").strip().lower()))
    if user is None or not security.verify_password(password, user.password_hash):
        return None
    return user


async def issue_token(s: AsyncSession, user: User, device: str = "") -> str:
    token = security.new_token()
    now = utcnow()
    s.add(AuthToken(token_hash=security.hash_token(token), user_id=user.id, device=device[:100],
                    created_at=now, last_used_at=now))
    await s.commit()
    return token


async def set_password(s: AsyncSession, user: User, password: str, keep_token: str | None = None) -> None:
    """Новый пароль; со всех устройств, кроме текущего, — выход."""
    check_password(password, user.login)
    user.password_hash = security.hash_password(password)
    stmt = delete(AuthToken).where(AuthToken.user_id == user.id)
    if keep_token:
        stmt = stmt.where(AuthToken.token_hash != security.hash_token(keep_token))
    await s.execute(stmt)
    await s.commit()


async def user_by_code(s: AsyncSession, text: str | None) -> User | None:
    code = security.clean_code(text, PERSONAL_CODE_LEN)
    return await s.scalar(select(User).where(User.code == code)) if code else None


# --- группы ----------------------------------------------------------------------

async def group_info(s: AsyncSession, group: str, create: bool = True) -> GroupInfo | None:
    info = await s.get(GroupInfo, group)
    if info is None and create:
        info = GroupInfo(group_name=group, curator="", curator_contact="")
        s.add(info)
    return info


async def group_code(s: AsyncSession, group: str, regenerate: bool = False) -> str:
    """Код, по которому студенты сами вступают в группу. Создаётся при первом запросе."""
    info = await group_info(s, group)
    if regenerate or not info.join_code:
        info.join_code = await _unique_code(s, GroupInfo, GroupInfo.join_code, GROUP_CODE_LEN)
        await s.commit()
    return info.join_code


async def group_by_code(s: AsyncSession, text: str | None) -> str | None:
    code = security.clean_code(text, GROUP_CODE_LEN)
    if code is None:
        return None
    return await s.scalar(select(GroupInfo.group_name).where(GroupInfo.join_code == code))


async def account_of_student(s: AsyncSession, student_id: int) -> User | None:
    return await s.scalar(select(User).where(User.student_id == student_id))


async def join_group(s: AsyncSession, user: User, group: str) -> Student:
    """Привязывает аккаунт к группе. Если админ уже загрузил список и в нём есть свободная запись
    с таким же ФИО — берём её (вместе с почтой), иначе добавляем студента в список. Коммитит."""
    group = clean_group(group)
    key = name_key(user.full_name)
    taken = select(User.student_id).where(User.student_id.is_not(None))
    student = await s.scalar(
        select(Student).where(Student.group_name == group, Student.name_key == key, Student.id.not_in(taken))
    )
    if student is None:
        student = Student(full_name=user.full_name, name_key=key, group_name=group)
        s.add(student)
        await s.flush()
    user.student_id = student.id
    user.half = None
    await s.commit()
    await s.refresh(user, attribute_names=["student", "teacher"])
    return student


async def users_with_student(s: AsyncSession) -> list[User]:
    return list((await s.scalars(select(User).where(User.student_id.is_not(None)))).all())


async def find_students(s: AsyncSession, query: str, groups: list[str] | None = None) -> list[Student]:
    """Точное совпадение ФИО, иначе — все, в чьём ФИО есть каждое слово запроса.

    groups — искать только в этих группах (у старосты — своя); None — везде.
    """
    stmt = select(Student).order_by(Student.full_name)
    if groups is not None:
        stmt = stmt.where(Student.group_name.in_(groups))
    key = name_key(query)
    exact = list((await s.scalars(stmt.where(Student.name_key == key))).all())
    if exact:
        return exact
    words = key.split()
    return [st for st in (await s.scalars(stmt)).all() if all(w in st.name_key for w in words)]
