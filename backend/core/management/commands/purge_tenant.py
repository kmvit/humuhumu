"""Стереть данные заведения — чтобы переезд можно было переиграть.

Просто удалить заведение нельзя: внутри есть защищённые связи (товар
держит свою категорию), и каскад обрывается на полпути. Поэтому чистим
послойно — повторяем проходы, пока хоть что-то удаляется: сначала уходят
позиции заказов, потом заказы, потом товары, и только затем категории.

Команда разрушительная и спрашивает подтверждение. Нужна она ровно для
одного: если импорт установки прошёл криво, откатить и повторить.

    python manage.py purge_tenant --domain kafe.padacha.ru
    python manage.py purge_tenant --domain kafe.padacha.ru --with-organization
"""
from django.apps import apps as django_apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError

from core.models import Organization
from core.tenancy import TenantMixin


class Command(BaseCommand):
    help = "Удалить все данные заведения (для повторного импорта)"

    def add_arguments(self, parser):
        parser.add_argument("--domain", required=True)
        parser.add_argument(
            "--with-organization", action="store_true",
            help="Удалить и само заведение, а не только его данные",
        )
        parser.add_argument("--yes", action="store_true", help="Не спрашивать")

    def handle(self, *args, **opts):
        org = Organization.objects.filter(
            domain=Organization.normalize_host(opts["domain"])
        ).first()
        if org is None:
            raise CommandError(f"Заведение с доменом {opts['domain']} не найдено")

        if not opts["yes"]:
            answer = input(
                f"Удалить ВСЕ данные заведения «{org.name}»? Это необратимо. "
                "Напишите название заведения для подтверждения: "
            )
            if answer.strip() != org.name:
                raise CommandError("Не подтверждено — ничего не тронуто")

        models = [m for m in django_apps.get_models() if issubclass(m, TenantMixin)]
        removed = {}

        with transaction.atomic():
            # Проходы, пока есть прогресс: порядок зависимостей так
            # выясняется сам, без ручного списка, который устареет.
            for _ in range(len(models) + 1):
                progress = False
                for model in list(models):
                    manager = getattr(model, "all_objects", model._default_manager)
                    qs = manager.filter(organization=org)
                    if not qs.exists():
                        models.remove(model)
                        continue
                    try:
                        count = qs.delete()[0]
                    except (ProtectedError, RestrictedError):
                        continue  # держат зависимые — вернёмся следующим проходом
                    label = f"{model._meta.app_label}.{model.__name__}"
                    removed[label] = removed.get(label, 0) + count
                    progress = True
                    models.remove(model)
                if not progress:
                    break

            if models:
                raise CommandError(
                    "Не удалось очистить: " + ", ".join(m.__name__ for m in models)
                )

            if opts["with_organization"]:
                name = org.name
                org.delete()
                self.stdout.write(self.style.SUCCESS(f"Заведение «{name}» удалено"))

        for label, count in sorted(removed.items()):
            self.stdout.write(f"  {label}: {count}")
        self.stdout.write(self.style.SUCCESS("Готово"))
