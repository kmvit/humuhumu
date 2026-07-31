from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

# Порядок важен: сначала пользователи и каталог, затем заказы, затем кошелёк
# (транзакции токенов ссылаются на заказ).
FIXTURES = ["demo_site", "demo_users", "demo_catalog", "demo_orders", "demo_wallet"]

SEQUENCE_APPS = ["users", "catalog", "orders", "wallet", "payments", "core"]


class Command(BaseCommand):
    help = "Загрузить демо-данные (фикстуры) и сбросить счётчики автоинкремента."

    def handle(self, *args, **options):
        call_command("loaddata", *FIXTURES)
        self._reset_sequences()
        self.stdout.write(self.style.SUCCESS("Демо-данные загружены."))
        self.stdout.write("Пользователи: admin / cashier / anna / boris, пароль: demo12345")

    def _reset_sequences(self):
        """Синхронизируем PostgreSQL-последовательности с максимальными PK."""
        with connection.cursor() as cursor:
            for app_label in SEQUENCE_APPS:
                models = apps.get_app_config(app_label).get_models()
                statements = connection.ops.sequence_reset_sql(
                    self.style, list(models)
                )
                for sql in statements:
                    cursor.execute(sql)
