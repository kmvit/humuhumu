#!/usr/bin/env bash
# Первичная настройка сервера и запуск проекта. Запускать на сервере от root.
#   ssh -p 49294 root@b5971f2535c5.vps.myjino.ru
#   bash setup-server.sh
set -euo pipefail

REPO="https://github.com/kmvit/humuhumu.git"
DIR="/opt/humu"

echo "==> Ставим Docker, если его нет"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
fi

echo "==> Клонируем/обновляем репозиторий в $DIR"
if [ -d "$DIR/.git" ]; then
    git -C "$DIR" pull --ff-only
else
    git clone "$REPO" "$DIR"
fi
cd "$DIR"

echo "==> Проверяем .env"
if [ ! -f .env ]; then
    cp .env.prod.example .env
    # автоматически генерируем секреты, чтобы не оставлять заглушки
    SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
    DBPASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    sed -i "s|ЗАМЕНИТЬ_НА_СЛУЧАЙНУЮ_СТРОКУ|$SECRET|" .env
    sed -i "s|ЗАМЕНИТЬ_НА_СИЛЬНЫЙ_ПАРОЛЬ|$DBPASS|" .env
    echo "    Создан .env со сгенерированными секретами. Проверьте домены при необходимости."
else
    echo "    .env уже есть — оставляю как есть."
fi

echo "==> Собираем и поднимаем контейнеры"
docker compose -f docker-compose.prod.yml up -d --build

echo "==> Ждём миграции и статику (10 c)"
sleep 10
docker compose -f docker-compose.prod.yml ps

cat <<'EOF'

==> Готово. Дальше — создать администратора:
    cd /opt/humu
    docker compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser
    docker compose -f docker-compose.prod.yml exec backend python manage.py load_menu

Сайт: http://b5971f2535c5.vps.myjino.ru
Админка: http://b5971f2535c5.vps.myjino.ru/admin/
EOF
