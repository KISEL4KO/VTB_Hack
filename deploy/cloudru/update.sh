#!/usr/bin/env bash
# Обновить платформу на ВМ до свежей версии из git и перезапустить службы.
#   cd ~/VTB_Hack && bash deploy/cloudru/update.sh
# RESET_DB=1 — заодно пересоздать базу с демо-данными (нужно, если поменялась схема таблиц).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

git pull --ff-only
.venv/bin/pip install -q -r requirements.txt gunicorn

if [ "${RESET_DB:-0}" = "1" ]; then
  sudo systemctl stop cfa-web cfa-keeper
  .venv/bin/python database_setup.py
  .venv/bin/flask --app main seed-demo
fi

sudo systemctl restart cfa-web cfa-keeper
sleep 3
systemctl --no-pager --lines=0 status cfa-web cfa-keeper | grep -E "●|Active:"
