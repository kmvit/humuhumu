"""Публичные учётные записи отдельного демонстрационного стенда."""

DEMO_PASSWORD = "demo12345"
DEMO_HOST = "demo.padacha.ru"

DEMO_ACCOUNTS = (
    {"username": "demo_admin", "label": "Владелец", "password": DEMO_PASSWORD},
    {"username": "demo_manager", "label": "Менеджер", "password": DEMO_PASSWORD},
    {"username": "demo_waiter", "label": "Официант", "password": DEMO_PASSWORD},
    {"username": "demo_cook", "label": "Повар", "password": DEMO_PASSWORD},
    {"username": "demo_bar", "label": "Бармен", "password": DEMO_PASSWORD},
)
