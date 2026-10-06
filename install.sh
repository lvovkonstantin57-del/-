#!/usr/bin/env bash
# Сервер «Расписание МПГУ» на VPS одной командой. Ubuntu или Debian, под root:
#
#   curl -fsSL https://raw.githubusercontent.com/lvovkonstantin57-del/-/main/install.sh | bash
#
# Что делает: ставит Docker (если его нет), скачивает код в /opt/raspisanie, создаёт server/.env
# с адресом https://<IP-через-дефисы>.sslip.io и кодом главного админа, запускает сервер и
# проверяет, что всё открывается по HTTPS. Сертификат получает Caddy:
#   • порты 80 и 443 свободны — свой Caddy в контейнере;
#   • на сервере уже работает Caddy (например, для Telegram-бота) — скрипт дописывает свой сайт
#     в /etc/caddy/Caddyfile, а сервер приложения встаёт на свободный порт рядом с ботом.
# Если на этом же сервере работает Telegram-бот, при первой установке его группы, расписание
# и журнал посещаемости переносятся в приложение. Бот при этом не останавливается.
#
# Повторный запуск той же командой обновляет сервер до свежей версии из GitHub; .env и база
# остаются как были. Настройки через переменные перед bash:
#   DOMAIN=schedule.example.ru   свой домен вместо sslip.io (A-запись должна указывать на сервер)
#   NO_CADDY=1                   HTTPS настроишь сам в своём nginx/Caddy (проксируй на APP_PORT)
#   APP_PORT=8081                порт на 127.0.0.1 для сервера приложения (по умолчанию 8080
#                                или первый свободный после него)
#   NO_IMPORT=1                  не переносить данные Telegram-бота; IMPORT_BOT=1 — перенести
#                                и при повторном запуске (только в пустую базу)
#   INSTALL_DIR, REPO_URL, BRANCH — откуда и куда ставить

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/lvovkonstantin57-del/-.git}"
BRANCH="${BRANCH:-main}"
DIR="${INSTALL_DIR:-/opt/raspisanie}"
PROJECT=raspisanie   # имя проекта в server/docker-compose.yml
CODE_ALPHABET=ABCDEFGHJKLMNPQRSTUVWXYZ23456789   # без 0/O и 1/I, как у остальных кодов
CADDYFILE=/etc/caddy/Caddyfile
BLOCK_BEGIN="# >>> raspisanie: сервер приложения, добавлено install.sh"
BLOCK_END="# <<< raspisanie"

MODE=""        # caddy — свой Caddy в контейнере, host-caddy — Caddy сервера, none — HTTPS настроен вручную
FIRST_RUN=""   # сервер приложения на этой машине ещё ни разу не запускался
DOMAIN_NOTE=""

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
  docker volume inspect "${PROJECT}_app_data" >/dev/null 2>&1 || FIRST_RUN=1
}

# Кто слушает порт: пусто — свободен, ours — наш контейнер, docker:<имя> — чужой контейнер,
# иначе имя программы на сервере (caddy, nginx, …)
port_owner() {
  local line proc name
  line=$(ss -Hltnp "sport = :$1" 2>/dev/null | head -n 1)
  [ -n "$line" ] || return 0
  proc=$(sed -n 's/.*users:(("\([^"]*\)".*/\1/p' <<<"$line")
  if [ "$proc" = docker-proxy ]; then
    name=$(docker ps --format '{{.Names}} {{.Ports}}' | grep -E ":$1->" | head -n 1 | cut -d' ' -f1)
    if [ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$name" 2>/dev/null)" = "$PROJECT" ]; then
      echo ours
    else
      echo "docker:${name:-?}"
    fi
  else
    echo "${proc:-неизвестная программа}"
  fi
}

describe_owner() {
  case "$1" in
    "") echo "свободен" ;;
    docker:*) echo "контейнер ${1#docker:}" ;;
    *) echo "программа $1" ;;
  esac
}

# 80 и 443 нужны для HTTPS: по ним ходят приложения и Let's Encrypt при выдаче сертификата
choose_mode() {
  local p80 p443
  if [ -n "${NO_CADDY:-}" ]; then
    MODE=none
  else
    MODE=$(get_env INSTALL_PROXY)
    if [ -z "$MODE" ] || [ "$MODE" = caddy ]; then
      p80=$(port_owner 80)
      p443=$(port_owner 443)
      if [[ -z $p80 || $p80 = ours ]] && [[ -z $p443 || $p443 = ours ]]; then
        MODE=caddy
      elif [ "$p80" = caddy ] && [ "$p443" = caddy ]; then
        if ! { [ -f "$CADDYFILE" ] && systemctl is-active --quiet caddy 2>/dev/null; }; then
          die "порты 80 и 443 занимает Caddy, но не служба caddy с $CADDYFILE — добавить сайт сам не смогу.
  Запусти с NO_CADDY=1 и проксируй домен в своём Caddy на 127.0.0.1:<порт из итогов установки>."
        fi
        MODE=host-caddy
        say "Порты 80 и 443 у Caddy сервера — допишу свой сайт в $CADDYFILE, остальные сайты не трогаю"
      else
        warn "Порт 80: $(describe_owner "$p80"), порт 443: $(describe_owner "$p443")"
        die "порты 80 и 443 заняты не Caddy. Варианты:
  • Если это старый Telegram-бот со своим Caddy в контейнере: в его папке выполни
    docker compose --profile caddy down — и запусти установку ещё раз.
  • Если это твой nginx или другой веб-сервер: запусти с NO_CADDY=1 и проксируй домен
    на 127.0.0.1:<порт из итогов установки>."
      fi
    fi
  fi
  set_env INSTALL_PROXY "$MODE"
}

choose_port() {
  local port wanted owner
  wanted=${APP_PORT:-}
  port=${wanted:-$(get_env APP_PORT)}
  port=${port:-8080}
  owner=$(port_owner "$port")
  if [ -n "$owner" ] && [ "$owner" != ours ]; then
    [ -z "$wanted" ] || die "порт $port занят ($(describe_owner "$owner")). Выбери другой: APP_PORT=$((port + 1))"
    local busy=$port
    for port in $(seq 8081 8099); do [ -z "$(port_owner "$port")" ] && break; done
    [ -z "$(port_owner "$port")" ] || die "не нашёл свободный порт в 8081–8099. Укажи сам: APP_PORT=…"
    say "Порт $busy занят ($(describe_owner "$owner")) — сервер приложения будет на 127.0.0.1:$port"
  fi
  set_env APP_PORT "$port"
}

# Адреса сайтов в Caddyfile, кроме нашего блока
caddy_sites() {
  awk -v b="$BLOCK_BEGIN" -v e="$BLOCK_END" '
    $0 == b { skip = 1; next }
    $0 == e { skip = 0; next }
    skip { next }
    { sub(/#.*/, "") }
    depth == 0 && /\{[ \t]*$/ {
      line = $0; sub(/\{[ \t]*$/, "", line)
      n = split(line, a, /[ \t,]+/)
      for (i = 1; i <= n; i++) if (a[i] != "") print a[i]
    }
    { depth += gsub(/\{/, "{") - gsub(/\}/, "}") }
  ' "$CADDYFILE" | sed -E 's#^https?://##; s#/.*$##; s#:[0-9]+$##'
}

configure_host_caddy() {
  local domain port tmp
  domain=$(get_env DOMAIN)
  port=$(get_env APP_PORT)
  if caddy_sites | grep -qxF "$domain"; then
    [ -z "${DOMAIN:-}" ] || die "домен $domain уже занят другим сайтом в $CADDYFILE. Укажи другой: DOMAIN=…"
    # Основной адрес уже у другого сайта (обычно у Telegram-бота) — приложению отдельное имя на том же IP
    DOMAIN_NOTE="$domain уже занят другим сайтом в $CADDYFILE (скорее всего, Telegram-ботом)"
    domain="app.$domain"
    set_env DOMAIN "$domain"
  fi

  tmp=$(mktemp)
  # Убираем свой прошлый блок и пустые строки в конце, дописываем свежий
  awk -v b="$BLOCK_BEGIN" -v e="$BLOCK_END" '
    $0 == b { skip = 1; next }
    $0 == e { skip = 0; next }
    skip { next }
    /^[ \t]*$/ { blank = blank $0 "\n"; next }
    { printf "%s%s\n", blank, $0; blank = "" }
  ' "$CADDYFILE" > "$tmp"
  printf '\n%s\n%s {\n\tencode gzip\n\treverse_proxy 127.0.0.1:%s\n}\n%s\n' \
    "$BLOCK_BEGIN" "$domain" "$port" "$BLOCK_END" >> "$tmp"
  if cmp -s "$tmp" "$CADDYFILE"; then
    rm -f "$tmp"
    return
  fi

  say "Добавляю $domain в $CADDYFILE"
  cp -p "$CADDYFILE" "$CADDYFILE.bak-raspisanie"
  cat "$tmp" > "$CADDYFILE"   # так у файла остаются прежние владелец и права
  rm -f "$tmp"
  # Неверную настройку reload не применяет — Caddy продолжает работать со старой
  if ! systemctl reload caddy; then
    cat "$CADDYFILE.bak-raspisanie" > "$CADDYFILE"
    die "Caddy не принял новую настройку — вернул $CADDYFILE как было (копия: $CADDYFILE.bak-raspisanie).
  Подробности: journalctl -u caddy -n 30"
  fi
}

open_firewall() {
  [ "$MODE" = caddy ] || return 0
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

build_image() {
  say "Скачиваю образы"
  pull_image python:3.12-slim
  if [ "$MODE" = caddy ]; then pull_image caddy:2; fi
  say "Собираю сервер"
  docker compose build app
}

# Контейнер Telegram-бота на этой же машине: в нём лежит его база (DB_PATH, по умолчанию /data/bot.db)
find_bot_container() {
  local c
  for c in $(docker ps --format '{{.Names}}'); do
    [ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$c" 2>/dev/null)" = "$PROJECT" ] && continue
    if docker exec "$c" sh -c 'test -f "${DB_PATH:-/data/bot.db}"' >/dev/null 2>&1; then
      echo "$c"
      return
    fi
  done
}

import_bot_data() {
  [ -z "${NO_IMPORT:-}" ] || return 0
  [ -n "$FIRST_RUN" ] || [ -n "${IMPORT_BOT:-}" ] || return 0
  local bot snapshot="$DIR/server/bot-import.db"
  bot=$(find_bot_container)
  [ -n "$bot" ] || return 0

  say "Переношу данные Telegram-бота из контейнера $bot (бот продолжит работать)"
  # Снимок базы средствами SQLite — целый, даже пока бот в неё пишет
  if ! docker exec "$bot" python -c '
import os, sqlite3
src = sqlite3.connect(os.environ.get("DB_PATH", "/data/bot.db"))
dst = sqlite3.connect("/tmp/raspisanie-import.db")
src.backup(dst)
dst.execute("PRAGMA journal_mode=DELETE")
dst.close()
src.close()' || ! docker cp "$bot:/tmp/raspisanie-import.db" "$snapshot" >/dev/null; then
    warn "Не получилось скопировать базу бота — пропускаю перенос. Перенести позже: README, «Перенос данных из Telegram-бота»"
    return 0
  fi
  docker exec "$bot" rm -f /tmp/raspisanie-import.db || true
  chmod 644 "$snapshot"
  docker compose run --rm --no-deps -v "$snapshot:/import/bot.db:ro" app python -m app.legacy /import/bot.db \
    || warn "Перенос не удался (сообщение выше). Сервер всё равно запустится"
  rm -f "$snapshot"   # в базе ФИО и почты — копию не оставляем
}

start_server() {
  say "Запускаю сервер"
  if [ "$MODE" = caddy ]; then
    docker compose --profile caddy up -d --remove-orphans
  else
    docker compose up -d --remove-orphans app
  fi
}

wait_ready() {
  local port domain i
  port=$(get_env APP_PORT)
  domain=$(get_env DOMAIN)
  say "Жду, пока сервер запустится"
  for i in $(seq 1 60); do
    curl -fsS -m 3 --noproxy "*" "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 && break
    [ "$i" -eq 60 ] && { docker compose logs --tail 40 app >&2 || true; die "сервер не запустился, лог выше"; }
    sleep 2
  done
  [ "$MODE" != none ] || return 0

  say "Жду HTTPS-сертификат для $domain (до 3 минут)"
  for i in $(seq 1 36); do
    # Сертификат выдан — значит, Let's Encrypt достучался до сервера снаружи по 80/443
    curl -fsS -m 5 --noproxy "*" --resolve "$domain:443:127.0.0.1" "https://$domain/healthz" >/dev/null 2>&1 && return
    sleep 5
  done
  if [ "$MODE" = caddy ]; then
    docker compose logs --tail 30 caddy >&2 || true
  else
    journalctl -u caddy --no-pager -n 30 >&2 || true
  fi
  die "сервер запущен, но HTTPS-сертификат для $domain не получен (лог Caddy выше).
  Чаще всего закрыты порты: открой входящие 80 и 443 (TCP) в панели хостинга — раздел «Firewall»,
  «Сетевая безопасность» или похожий — и запусти установку ещё раз."
}

summary() {
  local domain
  domain=$(get_env DOMAIN)
  printf '\n\033[1;32m✅ Сервер работает\033[0m\n\n'
  printf '  Адрес сервера:        https://%s\n' "$domain"
  printf '  Код главного админа:  %s\n\n' "$(get_env OWNER_CODE)"
  if [ -n "$DOMAIN_NOTE" ]; then
    printf '  Адрес с «app.»: %s.\n' "$DOMAIN_NOTE"
    printf '  Если в приложении другой адрес — на экране входа нажми «Сервер: … · изменить».\n\n'
  fi
  case "$MODE" in
    host-caddy) printf '  HTTPS — через Caddy сервера (%s), сервер приложения на 127.0.0.1:%s.\n' "$CADDYFILE" "$(get_env APP_PORT)"
                printf '  Остальные сайты в Caddy и Telegram-бот работают как раньше.\n\n' ;;
    none)       printf '  HTTPS настрой сам: проксируй https://%s на 127.0.0.1:%s.\n\n' "$domain" "$(get_env APP_PORT)" ;;
  esac
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
  choose_mode
  choose_port
  open_firewall
  build_image
  import_bot_data
  start_server
  if [ "$MODE" = host-caddy ]; then configure_host_caddy; fi
  wait_ready
  summary
}

main "$@"
