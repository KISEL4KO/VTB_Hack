#!/usr/bin/env bash
# Обновить платформу на ВМ до свежей версии из git и перезапустить службы.
#   cd ~/VTB_Hack && bash deploy/cloudru/update.sh             — обновить и пересоздать базу с демо-данными
#   cd ~/VTB_Hack && RESET_DB=0 bash deploy/cloudru/update.sh  — обновить, базу не трогать
#
# Весь скрипт — внутри функции main: bash читает её целиком до запуска. Иначе `git pull`, поменяв этот
# же файл, подсунул бы bash новые строки посреди выполнения, и часть шагов (например, сброс базы) пропала бы.
set -euo pipefail

main() {
  RESET_DB="${RESET_DB:-1}"       # по умолчанию база пересоздаётся; RESET_DB=0 — оставить как есть
  cd "$(dirname "${BASH_SOURCE[0]}")/../.."

  git pull --ff-only
  .venv/bin/pip install -q -r requirements.txt gunicorn

  if [ "$RESET_DB" = "1" ]; then
    echo "==> Пересоздаю базу с демо-данными"
    sudo systemctl stop cfa-web cfa-keeper
    .venv/bin/python database_setup.py
    .venv/bin/flask --app main seed-demo
  else
    echo "==> База не тронута (RESET_DB=0)"
  fi

  sudo systemctl restart cfa-web cfa-keeper
  sleep 3
  systemctl --no-pager --lines=0 status cfa-web cfa-keeper | grep -E "●|Active:"
  echo "Готово. В браузере обновите страницу (Ctrl+F5), если открыта старая версия."
  exit 0
}

main "$@"
