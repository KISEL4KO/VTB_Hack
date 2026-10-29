#!/usr/bin/env bash
# Установка платформы ЦФА на чистую ВМ Ubuntu 22.04 / 24.04 (Cloud.ru Evolution или любой другой VPS).
#
#   cd ~/VTB_Hack && bash deploy/cloudru/setup.sh
#
# Что делает (можно запускать повторно — ничего не сломает):
#   1. ставит python3-venv, nginx;
#   2. создаёт .venv и ставит зависимости + gunicorn;
#   3. создаёт /etc/cfa.env с секретным ключом Flask;
#   4. если базы ещё нет — создаёт её и заполняет демо-данными (SEED=0 — пустая база);
#   5. ставит две службы systemd: cfa-web (сайт) и cfa-keeper (сторож выплат);
#   6. настраивает nginx: порт 80 -> gunicorn на 127.0.0.1:8000.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_USER="$(id -un)"
SEED="${SEED:-1}"
cd "$APP_DIR"

if [ "$APP_USER" = "root" ]; then
  echo "Запустите скрипт от обычного пользователя (того, под которым вошли по SSH), а не от root." >&2
  exit 1
fi
if [ ! -f main.py ]; then
  echo "Не нашёл main.py в $APP_DIR — скрипт должен лежать в deploy/cloudru/ внутри проекта." >&2
  exit 1
fi

echo "==> 1/6 Системные пакеты"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip nginx git >/dev/null

echo "==> 2/6 Виртуальное окружение и зависимости (2–4 минуты)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt gunicorn

echo "==> 3/6 Настройки окружения (/etc/cfa.env)"
if [ ! -f /etc/cfa.env ]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  sudo tee /etc/cfa.env >/dev/null <<EOF
# Переменные окружения платформы ЦФА. После правки: sudo systemctl restart cfa-web cfa-keeper
SECRET_KEY=$SECRET
# Сторож работает отдельной службой cfa-keeper, поэтому проверка «при запросах» выключена.
KEEPER_REQUEST_INTERVAL=0
PYTHONUNBUFFERED=1
EOF
  sudo chown root:"$APP_USER" /etc/cfa.env
  sudo chmod 640 /etc/cfa.env
else
  echo "    /etc/cfa.env уже есть — не трогаю"
fi

echo "==> 4/6 База данных"
if [ ! -f cfa.db ]; then
  .venv/bin/python database_setup.py
  if [ "$SEED" = "1" ]; then
    .venv/bin/flask --app main seed-demo
  else
    echo "    База пустая. Создайте администратора:  .venv/bin/flask --app main create-admin"
  fi
else
  echo "    cfa.db уже есть — не трогаю (пересоздать: остановить службы, удалить cfa.db, запустить скрипт снова)"
fi

echo "==> 5/6 Службы systemd"
for unit in cfa-web cfa-keeper; do
  sed -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__APP_USER__|$APP_USER|g" \
      "deploy/cloudru/$unit.service" | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable -q cfa-web cfa-keeper
sudo systemctl restart cfa-web cfa-keeper

echo "==> 6/6 nginx"
sed -e "s|__APP_DIR__|$APP_DIR|g" deploy/cloudru/nginx-cfa.conf | sudo tee /etc/nginx/sites-available/cfa >/dev/null
sudo ln -sf /etc/nginx/sites-available/cfa /etc/nginx/sites-enabled/cfa
sudo rm -f /etc/nginx/sites-enabled/default
# nginx (пользователь www-data) раздаёт /static напрямую — ему нужно право «пройти» в домашнюю папку
chmod o+x "$HOME"
sudo nginx -t -q
sudo systemctl reload nginx

echo -n "    Жду запуска сайта"
for _ in $(seq 1 60); do
  if curl -fs -o /dev/null http://127.0.0.1/; then echo " — готово"; break; fi
  echo -n "."; sleep 1
done

IP="$(curl -fs --max-time 3 https://ifconfig.me || hostname -I | awk '{print $1}')"
cat <<EOF

Готово. Откройте в браузере:  http://$IP/
  (если не открывается — проверьте, что в группе безопасности ВМ разрешён входящий TCP 80)

Полезные команды:
  sudo systemctl status cfa-web cfa-keeper     — работают ли службы
  journalctl -u cfa-web -f                     — лог сайта
  journalctl -u cfa-keeper -f                  — лог сторожа выплат
  bash deploy/cloudru/update.sh                — подтянуть новую версию из git и перезапустить
EOF
