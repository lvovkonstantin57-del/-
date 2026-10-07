#!/usr/bin/env bash
# Ночное автообновление сервера «Расписание МПГУ». Запускает systemd-таймер raspisanie-update.timer,
# который ставит install.sh. Если на GitHub появилась новая версия — обновляет сервер тем же
# install.sh (данные и .env не трогаются); если нет — ничего не делает и сервер не перезапускается.
#
#   Журнал:     /var/log/raspisanie-update.log
#   Вручную:    systemctl start raspisanie-update
#   Отключить:  systemctl disable --now raspisanie-update.timer

set -euo pipefail

DIR="${INSTALL_DIR:-/opt/raspisanie}"
BRANCH="${BRANCH:-main}"

stamp() { date '+%Y-%m-%d %H:%M:%S'; }

# Не мешаем установке, запущенной руками в это же время
exec 9>/run/raspisanie-update.lock
flock -n 9 || { echo "$(stamp) уже идёт обновление — пропускаю"; exit 0; }

[ -d "$DIR/.git" ] || { echo "$(stamp) нет $DIR — сервер не установлен"; exit 1; }
git -C "$DIR" fetch -q --depth 1 origin "$BRANCH"
current=$(git -C "$DIR" rev-parse HEAD)
latest=$(git -C "$DIR" rev-parse FETCH_HEAD)
if [ "$current" = "$latest" ]; then
  echo "$(stamp) обновлений нет (${current:0:7})"
  exit 0
fi

echo "$(stamp) новая версия ${latest:0:7} (было ${current:0:7}) — обновляю"
# Сначала код, потом свежий install.sh из него: работающий скрипт не меняется у себя под ногами
git -C "$DIR" reset -q --hard FETCH_HEAD
if bash "$DIR/install.sh"; then
  echo "$(stamp) готово: ${latest:0:7}"
else
  echo "$(stamp) ошибка при обновлении — подробности выше"
  exit 1
fi
