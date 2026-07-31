# Сервис кафе (humu)

Веб-сервис (далее PWA) для маленького кафе: продажа товаров по карте и за токены.
Три роли — клиент, кассир, админ.

## Стек

- **Backend:** Django 5 + DRF, PostgreSQL, Redis, Celery
- **Frontend:** React + Vite + TypeScript
- **Платежи:** ЮKassa/CloudPayments (через webhook), учёт 54-ФЗ
- **Токены:** 1 токен = 1 ₽, журнал движений (ledger) с атомарными проводками

## Структура

```
backend/
  config/        — настройки, urls, celery
  users/         — пользователи и роли
  catalog/       — категории и товары
  wallet/        — кошелёк, токены, пакеты (services.py — проводки)
  orders/        — заказы и позиции
  payments/      — платежи провайдера
frontend/        — React (Vite), скелет с роутингом по ролям
docker-compose.yml
```

## Запуск (Docker)

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: http://localhost:5173
- API: http://localhost:8000/api/
- Админка: http://localhost:8000/admin/

Создать суперпользователя:

```bash
docker compose exec backend python manage.py createsuperuser
```

## Демо-данные

Загрузить тестовые данные (категории, товары, пакеты токенов, пользователи, заказ):

```bash
docker compose exec backend python manage.py seed_demo
```

Создаются пользователи (пароль у всех `demo12345`):

| Логин | Роль | Примечание |
|---|---|---|
| `admin` | админ | доступ в Django-админку |
| `cashier` | кассир | касса |
| `anna` | клиент | баланс 850 токенов, 1 заказ |
| `boris` | клиент | пустой кошелёк |

Фикстуры лежат в `*/fixtures/demo_*.json` и переиспользуются в тестах
(`fixtures = ["demo_users", "demo_catalog", "demo_orders", "demo_wallet"]`).
Запуск тестов: `docker compose exec backend python manage.py test`.

## Запуск backend локально (без Docker)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Что дальше

- [ ] API-эндпоинты: каталог, корзина/заказы, кошелёк, пакеты токенов
- [ ] Права по ролям (DRF permissions)
- [ ] Интеграция ЮKassa + webhook + фискализация (54-ФЗ)
- [ ] Экраны фронта (клиент / кассир / админ)
- [ ] PWA (manifest, service worker, push)
