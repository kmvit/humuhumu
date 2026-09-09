#!/usr/bin/env bash
# Подключение новой точки: один вызов — работающий инстанс «Падачи».
#
#   bash new-cafe.sh <slug> <домен> [опции]
#
#   slug   — имя установки: папка /opt/<slug>, имена контейнеров и томов.
#            Только строчные латинские буквы, цифры и дефис.
#   домен  — по какому адресу заведение будет открываться,
#            напр. park.padacha.ru (перед запуском: A-запись в DNS-зоне
#            padacha.ru и привязка домена к этому VPS в панели хостинга).
#
# Опции:
#   --license-key KEY  ключ из пульта (padacha.ru/pult/ → точка → Лицензия).
#                      Без него инстанс работает автономно: ни сверки
#                      тарифа, ни блокировок — для клиента так не оставлять.
#   --port N           локальный порт web-контейнера (по умолчанию — первый
#                      свободный, начиная с 8081)
#   --timezone TZ      часовой пояс заведения (по умолчанию Europe/Moscow);
#                      от него зависят границы смены и выручка дня
#   --pip-mirror URL   зеркало pip для сборки (на jino pypi.org недоступен:
#                      https://pypi.tuna.tsinghua.edu.cn/simple)
#
# Что делает: клонирует репозиторий в /opt/<slug>, генерирует .env со
# своими секретами, собирает и поднимает стек (порт наружу не торчит —
# только 127.0.0.1:<порт>), прописывает домен в nginx хоста. Соседние
# установки на этом же сервере не трогает: у каждой свой compose-проект,
# своя база и свои тома.
set -euo pipefail

REPO="https://github.com/kmvit/humuhumu.git"
LICENSE_URL="https://padacha.ru/api/license/"

die() { echo "ОШИБКА: $*" >&2; exit 1; }

# ── аргументы ────────────────────────────────────────────────────────────
SLUG="${1:-}"; DOMAIN="${2:-}"
[ -n "$SLUG" ] && [ -n "$DOMAIN" ] || die "использование: new-cafe.sh <slug> <домен> [опции]"
shift 2

LICENSE_KEY="" PORT="" TIMEZONE="Europe/Moscow" PIP_MIRROR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --license-key) LICENSE_KEY="$2"; shift 2 ;;
        --port)        PORT="$2"; shift 2 ;;
        --timezone)    TIMEZONE="$2"; shift 2 ;;
        --pip-mirror)  PIP_MIRROR="$2"; shift 2 ;;
        *) die "неизвестная опция: $1" ;;
    esac
done

echo "$SLUG"   | grep -Eq '^[a-z0-9-]+$' || die "slug — только [a-z0-9-]: '$SLUG'"
echo "$DOMAIN" | grep -Eq '^[a-z0-9.-]+$' || die "домен выглядит странно: '$DOMAIN'"
DIR="/opt/$SLUG"
[ -e "$DIR" ] && die "$DIR уже существует — slug занят"
[ "$(id -u)" = 0 ] || die "запускать от root"

# ── зависимости ──────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    echo "==> Ставим Docker"
    curl -fsSL https://get.docker.com | sh
fi
if ! command -v nginx >/dev/null 2>&1; then
    echo "==> Ставим nginx"
    apt-get update -qq && apt-get install -y -qq nginx
fi

# ── порт ─────────────────────────────────────────────────────────────────
if [ -z "$PORT" ]; then
    PORT=8081
    while ss -tln "sport = :$PORT" | grep -q ":$PORT"; do PORT=$((PORT + 1)); done
fi
ss -tln "sport = :$PORT" | grep -q ":$PORT" && die "порт $PORT занят"
echo "==> Порт web-контейнера: 127.0.0.1:$PORT"

# ── код и .env ───────────────────────────────────────────────────────────
echo "==> Клонируем репозиторий в $DIR"
git clone "$REPO" "$DIR"
cd "$DIR"

SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
DBPASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")

cat > .env <<ENV
# Сгенерировано new-cafe.sh $(date +%F). Секреты свои у каждой установки.
# COMPOSE_PROJECT_NAME изолирует контейнеры и тома от соседей по серверу.
COMPOSE_PROJECT_NAME=$SLUG

DJANGO_SECRET_KEY=$SECRET
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=$DOMAIN
DJANGO_TIME_ZONE=$TIMEZONE

POSTGRES_DB=humu
POSTGRES_USER=humu
POSTGRES_PASSWORD=$DBPASS
POSTGRES_HOST=db
POSTGRES_PORT=5432

REDIS_URL=redis://redis:6379/0

# Наружу порт не торчит — домены разводит nginx хоста.
WEB_BIND=127.0.0.1:$PORT

# SSL терминирует прокси хостинга, до нас доходит http.
CORS_ALLOWED_ORIGINS=https://$DOMAIN
CSRF_TRUSTED_ORIGINS=https://$DOMAIN

PIP_INDEX_URL=$PIP_MIRROR

# Лицензия «Падачи»: тариф и «оплачено до» приезжают из пульта раз в сутки.
LICENSE_KEY=$LICENSE_KEY
LICENSE_URL=$LICENSE_URL

# Эквайринг: банк выбирается в админке заведения, доступы — сюда.
TBANK_TERMINAL_KEY=
TBANK_PASSWORD=
SBER_USERNAME=
SBER_PASSWORD=

# Распознавание накладных по фото (тариф «Максимум»). Пусто — склад
# работает, но приход только руками. Ключ и туннель — как у флагмана.
OPENAI_API_KEY=
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_PROXY_URL=
ENV
chmod 600 .env

# ── сборка и запуск ──────────────────────────────────────────────────────
echo "==> Собираем и поднимаем стек (10–15 минут при первой сборке)"
docker compose -f docker-compose.prod.yml up -d --build

# ── nginx хоста ──────────────────────────────────────────────────────────
echo "==> Прописываем $DOMAIN в nginx"
cat > "/etc/nginx/sites-available/$SLUG" <<NGINX
# Заведение «$SLUG» — инстанс «Падачи» на 127.0.0.1:$PORT (WEB_BIND в
# /opt/$SLUG/.env). SSL терминируется на стороне хостинга.
server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 20m;   # фото чеков и блюд

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
        proxy_connect_timeout 120s;
    }
}
NGINX
ln -sf "/etc/nginx/sites-available/$SLUG" "/etc/nginx/sites-enabled/$SLUG"
nginx -t
if ! systemctl reload nginx 2>/dev/null; then
    # nginx мог быть не запущен (свежий сервер) или порт 80 занят
    # одиночной установкой (WEB_BIND=0.0.0.0:80 у соседа) — подсказываем.
    systemctl start nginx || die "nginx не стартует. Если порт 80 занят \
контейнером соседней установки — переведи её на 127.0.0.1:<порт> \
(WEB_BIND в её .env, потом 'up -d web') и запусти nginx снова."
fi

# ── проверка ─────────────────────────────────────────────────────────────
echo "==> Ждём, пока backend поднимется (миграции, статика)"
for i in $(seq 1 30); do
    sleep 5
    CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $DOMAIN" "http://127.0.0.1:$PORT/api/site/" || true)
    [ "$CODE" = 200 ] && break
done
[ "$CODE" = 200 ] || die "API не отвечает (последний код: $CODE) — смотри: docker compose -f docker-compose.prod.yml logs backend"
echo "    API отвечает: 200"

# ── владелец заведения ───────────────────────────────────────────────────
# Не суперпользователь: Django-админка — наш инструмент поддержки, а
# заведение управляет людьми и настройками из панели владельца.
echo "==> Заводим владельца заведения"
OWNER_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
docker compose -f docker-compose.prod.yml exec -T backend python manage.py shell -c \
"from users.models import User
u, created = User.objects.get_or_create(username='owner', defaults={'role': 'admin'})
u.role = 'admin'
u.set_password('$OWNER_PASS')
u.save()
print('   владелец:', 'создан' if created else 'обновлён')"

if [ -n "$LICENSE_KEY" ]; then
    echo "==> Первая сверка с пультом"
    docker compose -f docker-compose.prod.yml exec -T backend python manage.py shell -c \
"from core.license import sync_license, effective_status
s = sync_license()
print('   тариф:', s.plan or '—', '| оплачено до:', s.paid_until, '| статус:', effective_status())
print('   ошибка:', s.last_error or 'нет')"
fi

cat <<DONE

==> Готово. Стек «$SLUG» работает на 127.0.0.1:$PORT за доменом $DOMAIN.

ДОСТУП ВЛАДЕЛЬЦА (передать клиенту, попросить сменить пароль):
    https://$DOMAIN/  —  логин: owner  пароль: $OWNER_PASS
    Сотрудников он заводит сам: Админ-панель → «Сотрудники».

Дальше руками:
1. DNS: A-запись $DOMAIN → IP этого сервера (если ещё нет).
2. Панель хостинга: привязать $DOMAIN к этому VPS — иначе прокси
   хостинга не пропустит запросы (грабля jino).
3. В Django-админке https://$DOMAIN/admin/ (наш доступ поддержки —
   суперпользователя заводим отдельно при необходимости) или силами
   владельца заполнить настройки заведения, меню и столы.
$( [ -z "$LICENSE_KEY" ] && echo "5. ВНИМАНИЕ: LICENSE_KEY пуст — установка автономная. Для клиента:
   завести точку в пульте, ключ в .env, перезапустить стек." )

Обновление в будущем: cd $DIR && git pull --ff-only && \\
  docker compose -f docker-compose.prod.yml up -d --build
DONE
