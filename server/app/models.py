from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ROLE_USER = "user"
ROLE_STAROSTA = "starosta"  # правит студентов и расписание только своей группы
ROLE_ADMIN = "admin"
ROLE_OWNER = "owner"  # главный админ

WEEK_EVERY = "every"
WEEK_ODD = "odd"
WEEK_EVEN = "even"
WEEK_CUSTOM = "custom"  # свои номера недель — в Lesson.weeks

CHANGE_CANCEL = "cancel"  # пары в этот день не будет
CHANGE_EDIT = "change"    # в этот день другая аудитория, время, преподаватель…
CHANGE_ADD = "add"        # разовая пара (и вторая половина переноса)


class Base(DeclarativeBase):
    pass


class Student(Base):
    """Студент в списке группы. Появляется, когда студента добавили в группу
    (по его личному коду, по коду группы или из xlsx), и связан с аккаунтом в приложении."""

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200), index=True)
    group_name: Mapped[str] = mapped_column(String(100), index=True)
    # Корпоративная почта; видит только сам студент (и админы)
    email: Mapped[str | None] = mapped_column(String(200))


class GroupInfo(Base):
    """Группа: куратор, как с ним связаться и код, по которому студенты вступают в группу."""

    __tablename__ = "group_info"

    group_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    curator: Mapped[str] = mapped_column(String(200), default="")
    curator_contact: Mapped[str] = mapped_column(String(200), default="")
    join_code: Mapped[str | None] = mapped_column(String(16), unique=True)


class User(Base):
    """Аккаунт в приложении: логин и пароль. При регистрации выдаётся личный код."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    # ФИО, которое человек ввёл при регистрации
    full_name: Mapped[str] = mapped_column(String(200))
    # Личный код: по нему староста добавляет студента в группу, админ назначает роль
    code: Mapped[str] = mapped_column(String(16), unique=True)
    role: Mapped[str] = mapped_column(String(16), default=ROLE_USER)
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL"), unique=True
    )
    # Половина группы (1/2) для пар, где группа делится не по спискам
    half: Mapped[int | None] = mapped_column(Integer)
    # Напоминание за N минут до пары; None — выключено
    notify_before: Mapped[int | None] = mapped_column(Integer)
    # Ежедневная сводка в HH:MM; None — выключена
    digest_time: Mapped[str | None] = mapped_column(String(5))
    # Сводка на "today" или "tomorrow"
    digest_day: Mapped[str] = mapped_column(String(10), default="tomorrow")
    # Фото профиля: ключ картинки в Picture, он же часть адреса /api/pictures/<ключ>
    photo: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    student: Mapped[Student | None] = relationship(lazy="joined")
    # Запись преподавателя, если человек зарегистрировался по коду от главного админа
    teacher: Mapped["Teacher | None"] = relationship(lazy="joined", viewonly=True)

    @property
    def is_admin(self) -> bool:
        return self.role in (ROLE_ADMIN, ROLE_OWNER)

    @property
    def is_owner(self) -> bool:
        return self.role == ROLE_OWNER

    @property
    def is_starosta(self) -> bool:
        # Староста без группы прав не имеет: группа берётся из его записи в списке
        return self.role == ROLE_STAROSTA and self.student is not None

    @property
    def is_staff(self) -> bool:
        return self.is_admin or self.is_starosta

    @property
    def effective_role(self) -> str:
        return ROLE_USER if self.role == ROLE_STAROSTA and self.student is None else self.role

    @property
    def is_teacher(self) -> bool:
        return self.teacher is not None

    @property
    def group_name(self) -> str | None:
        return self.student.group_name if self.student else None

    @property
    def display_name(self) -> str:
        return self.student.full_name if self.student else self.full_name

    def can_manage(self, group: str | None) -> bool:
        """Можно ли менять студентов и расписание этой группы."""
        if self.is_admin:
            return True
        return bool(group) and self.is_starosta and group == self.group_name


class AuthToken(Base):
    """Вход на устройстве. Храним только хэш токена."""

    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_used_at: Mapped[datetime] = mapped_column(DateTime)


class Notification(Base):
    """Уведомление в приложении: лента «Уведомления» и системные уведомления на телефоне."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # group / role / contact / reply / announce / schedule / teacher / info
    kind: Mapped[str] = mapped_column(String(16), default="info")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    # От кого — чтобы можно было ответить
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime)  # UTC
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_name: Mapped[str] = mapped_column(String(100), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = понедельник
    week: Mapped[str] = mapped_column(String(8), default=WEEK_EVERY)
    pair_num: Mapped[int | None] = mapped_column(Integer)
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
    subject: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(50), default="")
    room: Mapped[str] = mapped_column(String(100), default="")
    teacher: Mapped[str] = mapped_column(String(200), default="")
    # Только для 1-й или 2-й половины группы; None — для всех
    half: Mapped[int | None] = mapped_column(Integer)
    # Для week == custom — номера учебных недель: «1-4,6», «2/3» (со 2-й каждую 3-ю), «1-16/2»
    weeks: Mapped[str | None] = mapped_column(String(200))


class LessonChange(Base):
    """Разовое изменение расписания на одну дату: отмена, другая аудитория или время, разовая пара.
    Перенос — пара записей: отмена в старый день и разовая пара в новый, связанные через moved_id."""

    __tablename__ = "lesson_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_name: Mapped[str] = mapped_column(String(100), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    # Какая пара из расписания (для cancel и change); у разовой пары — None
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(8))
    # Новые значения; у change None — «как обычно»
    pair_num: Mapped[int | None] = mapped_column(Integer)
    start_time: Mapped[str | None] = mapped_column(String(5))
    end_time: Mapped[str | None] = mapped_column(String(5))
    subject: Mapped[str | None] = mapped_column(String(300))
    kind: Mapped[str | None] = mapped_column(String(50))
    room: Mapped[str | None] = mapped_column(String(100))
    teacher: Mapped[str | None] = mapped_column(String(200))
    half: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(300), default="")
    moved_id: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Picture(Base):
    """Картинка: фото профиля или эмблема. Отдаётся по случайному ключу — его не угадать."""

    __tablename__ = "pictures"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    mime: Mapped[str] = mapped_column(String(32))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class JobMark(Base):
    """Отметка «уже сделано» для фоновых задач (ночной бэкап) — переживает перезапуск."""

    __tablename__ = "job_marks"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Teacher(Base):
    """Преподаватель: аккаунт, который ввёл одноразовый код от главного админа."""

    __tablename__ = "teachers"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    full_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Как преподаватель записан в расписании («Сидоров С. С.»). Пусто — ищем по ФИО.
    # Его пары — строки расписания, где в графе «Преподаватель» стоит он.
    schedule_name: Mapped[str | None] = mapped_column(String(200))


class TeacherInvite(Base):
    """Одноразовый код, по которому преподаватель получает доступ."""

    __tablename__ = "teacher_invites"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)  # UTC


class AttendanceSession(Base):
    """Отметка на одной паре: преподаватель показывает код, студенты вводят его в приложении.

    Код живёт минуту; «Новый код» заменяет его, отметки остаются в той же паре.
    Время — в UTC без часового пояса.
    """

    __tablename__ = "attendance_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teachers.user_id", ondelete="SET NULL"), index=True
    )
    teacher_name: Mapped[str] = mapped_column(String(200))  # остаётся в журнале, даже если преподавателя убрали
    subject: Mapped[str] = mapped_column(String(300))
    lesson_date: Mapped[date] = mapped_column(Date, index=True)  # по Москве
    start_time: Mapped[str | None] = mapped_column(String(5))
    end_time: Mapped[str | None] = mapped_column(String(5))
    half: Mapped[int] = mapped_column(Integer, default=0)  # 0 — вся группа, 1/2 — половина
    code: Mapped[str | None] = mapped_column(String(8), index=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)

    groups: Mapped[list["AttendanceGroup"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="AttendanceGroup.group_name"
    )
    marks: Mapped[list["AttendanceMark"]] = relationship(lazy="selectin", cascade="all, delete-orphan")

    @property
    def group_names(self) -> list[str]:
        return [g.group_name for g in self.groups]


class AttendanceGroup(Base):
    """Подгруппы, для которых открыта отметка на паре."""

    __tablename__ = "attendance_groups"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    group_name: Mapped[str] = mapped_column(String(100), primary_key=True, index=True)


class AttendanceMark(Base):
    """Студент был на паре: ввёл код или его отметил преподаватель."""

    __tablename__ = "attendance_marks"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime)
    method: Mapped[str] = mapped_column(String(8), default="code")  # code / manual
