import os
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# Откуда приложение на телефоне ходит к серверу: Android — https://localhost, iOS — capacitor://localhost
DEFAULT_CORS = "capacitor://localhost,https://localhost,http://localhost,ionic://localhost"


@dataclass(frozen=True)
class Config:
    # Код, по которому главный админ получает права: Профиль → «Ввести код»
    owner_code: str = field(default_factory=lambda: _env("OWNER_CODE"))
    # Кому писать, если что-то не так (покажется в профиле), например @username или почта
    admin_contact: str = field(default_factory=lambda: _env("ADMIN_CONTACT"))
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "data/app.db"))
    # Куда складывать ночные бэкапы базы
    backup_dir: str = field(default_factory=lambda: _env("BACKUP_DIR", "data/backups"))
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Europe/Moscow"))
    # Дата начала семестра по умолчанию (неделя с этой датой — нечётная/1-я)
    semester_start: str = field(default_factory=lambda: _env("SEMESTER_START", "2026-09-01"))
    # Название в приложении и в уведомлениях
    app_name: str = field(default_factory=lambda: _env("APP_NAME", "Расписание МПГУ"))
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", DEFAULT_CORS))
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8000")))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def default_semester_start(self) -> date:
        return date.fromisoformat(self.semester_start)

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


# Сетка звонков по умолчанию; админ может поменять её в приложении
DEFAULT_BELLS: list[tuple[str, str]] = [
    ("09:00", "10:30"),
    ("10:40", "12:10"),
    ("12:40", "14:10"),
    ("14:20", "15:50"),
    ("16:00", "17:30"),
]

config = Config()
