#!/usr/bin/env bash
# Сервер «Расписание МПГУ» на VPS одной командой. Ubuntu или Debian, под root:
#
#   curl -fsSL https://raw.githubusercontent.com/lvovkonstantin57-del/-/main/install.sh | bash
#
# Что делает: ставит Docker (если его нет), скачивает код в /opt/raspisanie, создаёт server/.env
# с адресом https://<IP-через-дефисы>.sslip.io и кодом главного админа, запускает сервер вместе
# с Caddy (HTTPS-сертификат он получает сам) и проверяет, что всё открывается.
#
# Повторный запуск той же командой обновляет сервер до свежей версии из GitHub; .env и база
# остаются как были. Настройки через переменные перед bash:
#   DOMAIN=schedule.example.ru   свой домен вместо sslip.io (A-запись должна указывать на сервер)
#   NO_CADDY=1                   на сервере уже есть свой nginx или Caddy — HTTPS настроишь в нём
#   APP_PORT=8081                порт на 127.0.0.1 для своего прокси, если 8080 занят
#   INSTALL_DIR, REPO_URL, BRANCH — откуда и куда ставить

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/lvovkonstantin57-del/-.git}"
BRANCH="${BRANCH:-main}"
DIR="${INSTALL_DIR:-/opt/raspisanie}"
PROJECT=raspisanie   # имя проекта в server/docker-compose.yml
CODE_ALPHABET=ABCDEFGHJKLMNPQRSTUVWXYZ23456789   # без 0/O и 1/I, как у остальных кодов

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\n\033[1;31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }

apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  if [ -z "${APT_UPDATED:-}" ]; then apt-get update -qq; APT_UPDATED=1; fi
  apt-get install -y -qq --no-install-recommends "$@" >/dev/null
}

install_tools() {
  local missing=()
  for tool in git curl; do command -v "$tool" >/dev/null || missing+=("$tool"); done
  command -v ss >/dev/null && command -v ip >/dev/null || missing+=(iproute2)
  if [ ${#missing[@]} -gt 0 ]; then
    say "Ставлю ${missing[*]}"
    apt_install ca-certificates "${missing[@]}"
  fi
}

install_docker() {
  if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then return; fi
  say "Ставлю Docker"
  # Сначала из репозитория самой системы: download.docker.com доступен не отовсюду
  command -v docker >/dev/null || apt_install docker.io || true
  if command -v docker >/dev/null && ! docker compose version >/dev/null 2>&1; then
    apt_install docker-compose-v2 2>/dev/null || apt_install docker-compose-plugin 2>/dev/null || true
  fi
  if command -v docker >/dev/null && ! docker compose version >/dev/null 2>&1; then
    # Плагин compose с GitHub — оттуда же скачивается и код
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
      "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
      && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose || true
  fi
  if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh || true
  fi
  docker compose version >/dev/null 2>&1 \
    || die "не получилось поставить Docker. Поставь его вручную (https://docs.docker.com/engine/install/) и запусти установку ещё раз"
  systemctl enable --now docker >/dev/null 2>&1 || true
}

# IPv4, под которым сервер виден из интернета
public_ip() {
  local ip
  ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{ for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit } }')
  case "$ip" in
    ""|10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*)
      # Адрес внутренней сети облака — спросим снаружи
      ip=$(curl -4fsS -m 10 https://api.ipify.org 2>/dev/null || curl -4fsS -m 10 https://ifconfig.me 2>/dev/null || true) ;;
  esac
  [[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] && echo "$ip"
}

new_owner_code() {
  local raw
  raw=$(head -c 600 /dev/urandom | tr -dc "$CODE_ALPHABET" | cut -c1-12)
  [ ${#raw} -eq 12 ] || die "не получилось сгенерировать код главного админа"
  echo "${raw:0:4}-${raw:4:4}-${raw:8:4}"
}

get_env() { sed -n "s/^$1=//p" .env | tail -n 1; }
set_env() {
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}

fetch_code() {
  if [ -d "$DIR/.git" ]; then
    say "Обновляю код в $DIR"
    git -C "$DIR" fetch -q --depth 1 origin "$BRANCH"
    git -C "$DIR" reset -q --hard FETCH_HEAD   # .env и база в git не лежат — не трогаются
  else
    if [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
      die "папка $DIR уже есть и не пустая. Укажи другую: INSTALL_DIR=/opt/другая-папка"
    fi
    say "Скачиваю код в $DIR"
    git clone -q --depth 1 --branch "$BRANCH" "$REPO_URL" "$DIR"
  fi
}

configure() {
  cd "$DIR/server"
  if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
  fi
  if [ -n "${DOMAIN:-}" ]; then
    set_env DOMAIN "$DOMAIN"
  elif [ -z "$(get_env DOMAIN)" ] || [ "$(get_env DOMAIN)" = "schedule.example.ru" ]; then
    local ip
    ip=$(public_ip) || die "не смог узнать IP сервера. Укажи адрес сам: DOMAIN=1-2-3-4.sslip.io (цифры — IP через дефис)"
    set_env DOMAIN "${ip//./-}.sslip.io"
  fi
  [ -n "$(get_env OWNER_CODE)" ] || set_env OWNER_CODE "$(new_owner_code)"
  if [ -n "${APP_PORT:-}" ]; then set_env APP_PORT "$APP_PORT"; fi
  [ -n "$(get_env APP_PORT)" ] || set_env APP_PORT 8080
}

# 80 и 443 нужны Caddy: по ним ходят приложения и Let's Encrypt при выдаче сертификата
check_ports() {
  local port ports=("$(get_env APP_PORT)") busy=() ours
  [ -n "${NO_CADDY:-}" ] || ports+=(80 443)
  ours=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT")
  for port in "${ports[@]}"; do
    if ss -Hltn "sport = :$port" 2>/dev/null | grep -q .; then busy+=("$port"); fi
  done
  [ ${#busy[@]} -eq 0 ] && return
  [ -n "$ours" ] && return   # занято нашим же сервером — это обновление
  warn "Порты ${busy[*]} уже заняты:"
  ss -Hltnp 2>/dev/null | grep -E ":($(IFS='|'; echo "${busy[*]}"))\s" >&2 || true
  docker ps --format '  контейнер {{.Names}} ({{.Image}}): {{.Ports}}' 2>/dev/null | grep -E ":($(IFS='|'; echo "${busy[*]}"))->" >&2 || true
  die "освободи порты и запусти установку ещё раз.
  • Если это старый Telegram-бот: в его папке выполни  docker compose --profile caddy down
    (база бота останется; перенести её в приложение — README, «Перенос данных из Telegram-бота»).
  • Если на сервере свой nginx или Caddy: запусти с NO_CADDY=1 и проксируй домен на 127.0.0.1:$(get_env APP_PORT).
  • Если занят только $(get_env APP_PORT): запусти с APP_PORT=8081."
}

open_firewall() {
  [ -n "${NO_CADDY:-}" ] && return
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    say "Открываю порты 80 и 443 в ufw"
    ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && ufw allow 443/udp >/dev/null
  fi
}

# Docker Hub часто отвечает «429 Too Many Requests» на общих IP — тогда берём то же с зеркала Google
pull_image() {
  docker pull -q "$1" >/dev/null 2>&1 && return
  if docker pull -q "mirror.gcr.io/library/$1" >/dev/null 2>&1; then
    docker tag "mirror.gcr.io/library/$1" "$1"
    return
  fi
  docker image inspect "$1" >/dev/null 2>&1 && { warn "Не смог обновить $1, беру уже скачанный"; return; }
  die "не получилось скачать образ $1 ни с Docker Hub, ни с mirror.gcr.io"
}

start() {
  say "Скачиваю образы"
  pull_image python:3.12-slim
  [ -n "${NO_CADDY:-}" ] || pull_image caddy:2

  say "Собираю и запускаю сервер"
  if [ -n "${NO_CADDY:-}" ]; then
    docker compose up -d --build --remove-orphans app
  else
    docker compose --profile caddy up -d --build --remove-orphans
  fi
}

wait_ready() {
  local port domain i
  port=$(get_env APP_PORT)
  domain=$(get_env DOMAIN)
  say "Жду, пока сервер запустится"
  for i in $(seq 1 60); do
    curl -fsS -m 3 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 && break
    [ "$i" -eq 60 ] && { docker compose logs --tail 40 app >&2 || true; die "сервер не запустился, лог выше"; }
    sleep 2
  done
  [ -n "${NO_CADDY:-}" ] && return

  say "Жду HTTPS-сертификат для $domain (до 3 минут)"
  for i in $(seq 1 36); do
    # Сертификат выдан — значит, Let's Encrypt достучался до сервера снаружи по 80/443
    curl -fsS -m 5 --resolve "$domain:443:127.0.0.1" "https://$domain/healthz" >/dev/null 2>&1 && return
    sleep 5
  done
  docker compose logs --tail 30 caddy >&2 || true
  die "сервер запущен, но HTTPS-сертификат для $domain не получен (лог Caddy выше).
  Чаще всего закрыты порты: открой входящие 80 и 443 (TCP) в панели хостинга — раздел «Firewall»,
  «Сетевая безопасность» или похожий — и запусти установку ещё раз."
}

summary() {
  local domain owner
  domain=$(get_env DOMAIN)
  owner=$(get_env OWNER_CODE)
  printf '\n\033[1;32m✅ Сервер работает\033[0m\n\n'
  if [ -n "${NO_CADDY:-}" ]; then
    printf '  Сервер слушает 127.0.0.1:%s — проксируй на него %s со своим HTTPS.\n' "$(get_env APP_PORT)" "$domain"
  fi
  printf '  Адрес сервера:        https://%s\n' "$domain"
  printf '  Код главного админа:  %s\n\n' "$owner"
  printf '  1. Открой https://%s в браузере или приложение на телефоне и зарегистрируйся.\n' "$domain"
  printf '  2. Введи код главного админа в поле «Есть код группы?» (или «Профиль → Ввести код»).\n\n'
  printf '  Код хранится в %s/server/.env — никому его не отправляй.\n' "$DIR"
  printf '  Обновить сервер:  запусти эту же команду ещё раз\n'
  printf '  Логи:             cd %s/server && docker compose logs -f app\n\n' "$DIR"
}

main() {
  [ "$(id -u)" -eq 0 ] || die "нужен root. Запусти так: curl -fsSL …/install.sh | sudo bash"
  command -v apt-get >/dev/null || die "скрипт рассчитан на Ubuntu или Debian"
  install_tools
  install_docker
  fetch_code
  configure
  check_ports
  open_firewall
  start
  wait_ready
  summary
}

main "$@"
