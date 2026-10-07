"""Пароли, токены входа, коды и ограничение частоты попыток."""

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

# Сколько раз прогонять PBKDF2. В тестах — меньше, чтобы не ждать
PASSWORD_ITERATIONS = int(os.environ.get("PASSWORD_ITERATIONS", "240000"))

# Коды без похожих символов: нет 0/O и 1/I
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# Код могут набрать русскими буквами — похожие заменяем на латинские
CYR_TO_LAT = str.maketrans("АВЕКМНОРСТУХавекмнорстух", "ABEKMHOPCTYXABEKMHOPCTYX")

PERSONAL_CODE_LEN = 6   # личный код студента: ABC-234
GROUP_CODE_LEN = 6      # код группы
INVITE_CODE_LEN = 8     # приглашение преподавателя: ABCD-EFGH


def utcnow() -> datetime:
    # SQLite хранит время в UTC без таймзоны
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- пароли ------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str, iterations: int | None = None) -> str:
    iterations = iterations or PASSWORD_ITERATIONS
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, base64.b64decode(digest))


def temp_password() -> str:
    """Временный пароль, который староста или админ продиктует студенту: 8 символов без похожих,
    первая — заглавная, есть цифры (проходит правила пароля)."""
    letters = [ch for ch in CODE_ALPHABET if ch.isalpha()]
    digits = [ch for ch in CODE_ALPHABET if ch.isdigit()]
    rest = [secrets.choice(letters).lower() for _ in range(5)] + [secrets.choice(digits) for _ in range(2)]
    for i in range(len(rest) - 1, 0, -1):  # перемешать: цифры не всегда в конце
        j = secrets.randbelow(i + 1)
        rest[i], rest[j] = rest[j], rest[i]
    return secrets.choice(letters) + "".join(rest)


# --- токены ------------------------------------------------------------------

def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- коды --------------------------------------------------------------------

def random_code(length: int) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def clean_code(text: str | None, length: int) -> str | None:
    """«abc-234», «АВС 234» → «ABC234»; None — не похоже на код такой длины."""
    raw = "".join(ch for ch in (text or "").translate(CYR_TO_LAT).upper() if ch.isalnum())
    if len(raw) == length and all(ch in CODE_ALPHABET for ch in raw):
        return raw
    return None


def pretty_code(code: str | None) -> str | None:
    """ABC234 → ABC-234, ABCDEFGH → ABCD-EFGH."""
    if not code:
        return code
    half = len(code) // 2
    return f"{code[:half]}-{code[half:]}"


# --- частота попыток -----------------------------------------------------------

class RateLimit:
    """Не больше limit событий за window секунд на ключ (в памяти процесса)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._log: dict[object, deque] = defaultdict(deque)

    def _fresh(self, key) -> deque:
        q = self._log[key]
        now = time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key) -> bool:
        return len(self._fresh(key)) >= self.limit

    def wait_minutes(self, key) -> int:
        q = self._fresh(key)
        if not q:
            return 0
        return max(1, round((self.window - (time.monotonic() - q[0])) / 60))

    def hit(self, key) -> None:
        self._fresh(key).append(time.monotonic())

    def reset(self, key) -> None:
        self._log.pop(key, None)

    def clear(self) -> None:
        self._log.clear()


login_fails = RateLimit(10, 10 * 60)       # неверный пароль к одному логину: 10 раз за 10 минут — пауза
# С одного адреса: в университете вся группа может сидеть за одним Wi-Fi, поэтому с запасом
login_fails_ip = RateLimit(100, 10 * 60)
signups = RateLimit(200, 60 * 60)          # регистраций с одного адреса в час
code_fails = RateLimit(10, 10 * 60)        # неверные коды (группы, студента, приглашения)
messages = RateLimit(5, 10 * 60)           # сообщения админам и ответы


def reset_limits() -> None:
    for limit in (login_fails, login_fails_ip, signups, code_fails, messages):
        limit.clear()
